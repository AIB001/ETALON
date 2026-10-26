"""Task-local SND1 contact combinations and minimum-motion rigid proposals.

The enclosing-ball bound is a necessary condition for the declared donor-H
cone and distance shell. Passing it is not proof of a compatible binding pose.
No force field, protein flexibility, water displacement, or affinity is inferred.
"""
from __future__ import annotations
from itertools import combinations
import math
import numpy as np


def donor_region(donor, hydrogen, *, distance_shell=(2.5, 3.0), angle_min=150.0):
    """Conservative ball enclosing every acceptor in this exact contact region.

    Coordinates are in Angstrom and must use one consistent periodic image.
    The cone is at H, not D. Its closure includes collinear contacts. For the
    supported shell (D-A >= D-H), maximal distance from the axial ball center
    occurs on the angular boundary and at one of the shell endpoints.
    """
    d,h=np.asarray(donor,float),np.asarray(hydrogen,float)
    length=float(np.linalg.norm(h-d));lo,hi=map(float,distance_shell)
    if d.shape!=(3,) or h.shape!=(3,) or not np.all(np.isfinite([*d,*h,lo,hi,angle_min])):
        raise ValueError('Finite 3D coordinates and contact thresholds required')
    if length<=0 or not length<=lo<=hi or not 90<=angle_min<=180:
        raise ValueError('Require 0 < D-H <= D-A shell and 90 <= angle <= 180')
    axis=(h-d)/length;center_distance=(lo+hi)/2
    cosine=math.cos(math.radians(180-angle_min));sine2=1-cosine*cosine
    radii=[]
    for s in (lo,hi):
        axial=length*sine2+cosine*math.sqrt(max(s*s-length*length*sine2,0))
        radii.append(math.sqrt(max(s*s+center_distance**2-2*center_distance*axial,0)))
    return dict(center=d+center_distance*axis,radius=max(radii),donor=d,hydrogen=h,
                distance_shell=(lo,hi),angle_min=float(angle_min))


def compatibility(points, regions, *, tolerance=1e-10):
    """Rigid-distance certificate for balls enclosing the contact regions.

    All pair inequalities must hold; chirality, directions, and multiway ball
    intersections remain unknown. Never reuse after changing internal geometry.
    """
    p=np.asarray(points,float)
    if p.shape!=(len(regions),3) or not np.all(np.isfinite(p)) or tolerance<0:
        raise ValueError('One finite 3D anchor per region required')
    rows=[]
    for i,j in combinations(range(len(regions)),2):
        a,b=regions[i],regions[j]
        ra,rb=float(a['radius']),float(b['radius'])
        centers=np.asarray([a['center'],b['center']],float)
        if ra<0 or rb<0 or not np.all(np.isfinite([ra,rb,*centers.ravel()])):
            raise ValueError('Finite nonnegative ball radii required')
        source=float(np.linalg.norm(p[i]-p[j]));target=float(np.linalg.norm(centers[0]-centers[1]))
        gap=max(abs(source-target)-ra-rb,0)
        rows.append(dict(pair=[i,j],source_distance_A=source,center_distance_A=target,
                         radius_sum_A=ra+rb,excess_A=gap,excluded=bool(gap>tolerance)))
    return dict(necessary_condition_passes=not any(r['excluded'] for r in rows),pairs=rows,
                uniform_extra_radius_lower_bound_A=max((r['excess_A']/2 for r in rows),default=0.0),
                sufficiency='unknown',scope='One fixed internal conformation; declared contact regions only')


def _rotation_between(source,target):
    a=np.asarray(source,float);b=np.asarray(target,float)
    if min(np.linalg.norm(a),np.linalg.norm(b))<1e-12:
        raise ValueError('Distinct source and target anchors required')
    a=a/np.linalg.norm(a);b=b/np.linalg.norm(b);v=np.cross(a,b);c=float(np.clip(a@b,-1,1))
    if np.linalg.norm(v)<1e-12:
        if c>0:return np.eye(3)
        axis=np.cross(a,np.eye(3)[np.argmin(np.abs(a))]);axis/=np.linalg.norm(axis)
        return 2*np.outer(axis,axis)-np.eye(3)
    k=np.array([[0,-v[2],v[1]],[v[2],0,-v[0]],[-v[1],v[0],0]])
    return np.eye(3)+k+k@k/(1+c)


def pair_motion(xyz, indices, centers, *, twist_deg=0.0):
    """Proper rigid fit of two anchors, with an explicit axial twist.

    Uses the shortest alignment rotation at zero twist, then translates the
    anchor midpoint. Does not change any bond, torsion, or stereochemistry.
    """
    xyz=np.asarray(xyz,float);centers=np.asarray(centers,float)
    if len(indices)!=2 or len(set(indices))!=2 or centers.shape!=(2,3):
        raise ValueError('Two distinct ligand anchors and two 3D target centers required')
    p=xyz[list(indices)];r=_rotation_between(p[1]-p[0],centers[1]-centers[0])
    axis=centers[1]-centers[0];axis/=np.linalg.norm(axis)
    theta=math.radians(float(twist_deg));k=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
    twist=np.eye(3)+math.sin(theta)*k+(1-math.cos(theta))*(k@k)
    return (xyz-p.mean(0))@(twist@r).T+centers.mean(0)


def contact_options(ctx, *, distance_shell=(2.5,3.0), angle_min=150):
    """Existing chemistry-typed protein donors to ligand acceptors, no retyping."""
    options=[];offset=ctx['offset'];xyz=ctx['xyz'];box=ctx['box']
    inv=np.linalg.inv(box);center=ctx['ligand'].mean(0)
    def image(v):
        f=np.asarray(v)@inv;return (f-np.rint(f))@box
    for triple,label in zip(ctx['case']['triples'],ctx['case']['triple_residues']):
        d,h,a=map(int,triple)
        if label not in ('SER254','ASN281','ARG255') or d>=offset or a<offset:continue
        donor=center+image(xyz[d]-center);hydrogen=donor+image(xyz[h]-xyz[d])
        region=donor_region(donor,hydrogen,distance_shell=distance_shell,angle_min=angle_min)
        options.append(dict(ligand_index=a-offset,triple=[d,h,a],residue=label,region=region))
    return options


def contact_combinations(xyz, options, *, objective='ASN_ARG'):
    """List all declared distinct-acceptor pairs, with conservative exclusions."""
    if objective not in ('ASN_ARG','SER_other'):raise ValueError('Unknown SND1 contact objective')
    rows=[]
    for i,j in combinations(range(len(options)),2):
        a,b=options[i],options[j];labels={a['residue'],b['residue']}
        eligible=(labels=={'ASN281','ARG255'} if objective=='ASN_ARG' else
                  'SER254' in labels and bool(labels&{'ASN281','ARG255'}))
        if not eligible or a['ligand_index']==b['ligand_index']:continue
        ids=[a['ligand_index'],b['ligand_index']];regions=[a['region'],b['region']]
        certificate=compatibility(np.asarray(xyz)[ids],regions)
        centers=np.asarray([r['center'] for r in regions]);points=np.asarray(xyz)[ids]
        midpoint_motion=float(np.linalg.norm(centers.mean(0)-points.mean(0)))
        mismatch=abs(certificate['pairs'][0]['source_distance_A']-certificate['pairs'][0]['center_distance_A'])
        rows.append(dict(option_indices=[i,j],ligand_indices=ids,certificate=certificate,
                         midpoint_motion_A=midpoint_motion,center_distance_mismatch_A=mismatch))
    return rows


def paired_proposals(ctx, *, budget=96, objective='ASN_ARG', guided=True):
    """Equal number of expensive atom-level evaluations for either ordinary arm.

    Baseline uniformly samples pair fits. Guided fits prioritize compatible
    pairs near the current pose and sweep their remaining axial degree of
    freedom. Both use only rigid movement and have identical coordinate support.
    Any excluded pair is excluded only for this exact rigid conformation.
    """
    if budget<2:raise ValueError('At least two evaluations required')
    options=contact_options(ctx);pairs=contact_combinations(ctx['ligand'],options,objective=objective)
    viable=[r for r in pairs if r['certificate']['necessary_condition_passes']]
    if guided:
        pool=sorted(viable,key=lambda r:(r['midpoint_motion_A'],r['center_distance_mismatch_A'],r['option_indices']))
        # Diversity across contact pairs is retained; local motion is prioritized.
        pool=pool[:min(24,len(pool))]
    else:pool=pairs
    poses=[ctx['ligand'].copy()];metadata=[dict(action='identity')]
    for i in range(budget-1):
        if not pool:
            poses.append(ctx['ligand'].copy());metadata.append(dict(action='no_compatible_pair'));continue
        if guided:
            row=pool[i%len(pool)];twist=(i//len(pool))*90.0
        else:
            row=pool[(i*137)%len(pool)];twist=(i*137.507764)%360
        centers=np.asarray([options[j]['region']['center'] for j in row['option_indices']])
        pose=pair_motion(ctx['ligand'],row['ligand_indices'],centers,twist_deg=twist)
        poses.append(pose);metadata.append(dict(action='rigid_pair_fit',pair=row,twist_deg=twist,
            triples=[options[j]['triple'] for j in row['option_indices']]))
    return poses,dict(options=len(options),combinations=len(pairs),excluded=len(pairs)-len(viable),
        candidate_metadata=metadata,scope='Distinct ligand acceptors for two protein donors; fixed conformation; D-A 2.5-3.0 A, D-H-A >=150 deg',
        unknown=['ligand-donor combinations','same-acceptor bifurcated contacts','changed internal conformations','protein flexibility'])


def combined_readout(pose,ctx,anchor):
    """Ordinary atomistic validation; certificates never substitute for this."""
    score=anchor.score(pose,ctx);xyz=ctx['xyz'].copy();xyz[ctx['offset']:]=pose
    triples=ctx['case']['triples'];labels=np.asarray(ctx['case']['triple_residues'])
    v=anchor.minimum_image(xyz[triples[:,0]]-xyz[triples[:,1]],ctx['box'])
    w=anchor.minimum_image(xyz[triples[:,2]]-xyz[triples[:,1]],ctx['box'])
    da=np.linalg.norm(anchor.minimum_image(xyz[triples[:,0]]-xyz[triples[:,2]],ctx['box']),axis=1)
    angles=np.degrees(np.arccos(np.clip(np.sum(v*w,axis=1)/np.linalg.norm(v,axis=1)/np.linalg.norm(w,axis=1),-1,1)))
    good=(da>=2.5)&(da<=3)&(angles>=150)
    hit={label:bool(np.any(good[labels==label])) for label in ('SER254','ASN281','ARG255')}
    movement=float(np.sqrt(np.mean(np.sum((pose[ctx['chemical']['core']]-ctx['ligand'][ctx['chemical']['core']])**2,axis=1))))
    return dict(valid=score['valid'],contacts=hit,ASN_ARG=bool(hit['ASN281'] and hit['ARG255']),
                SER_other=bool(hit['SER254'] and (hit['ASN281'] or hit['ARG255'])),core_movement_A=movement,
                min_protein_vdw_ratio=score['min_vdw_ratio'],strain_change_kcal_mol=score['energy_change_kcal_mol'])
