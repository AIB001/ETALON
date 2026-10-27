"""Rooted linker interventions on existing SND1 trajectories.

Instantaneous fixed-protein geometry, not free-energy or binding prediction.
No MD, shared-environment mutation, or harness policy is performed here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import numpy as np

PRIOR = Path('/home/zx/etalon/runs/etalon-task-learning-20261026/science')
PRIOR_SHA = '6ca722560fb46f276a05c0160913e569a50225da4f13a5f60feb012310277a1e'


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n', encoding='utf-8')


def pin(path):
    path = Path(path)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)


def prior_module():
    if pin(PRIOR / 'snd1_task_learning.py')['sha256'] != PRIOR_SHA:
        raise ValueError('Prior exact analyzer changed')
    sys.path.insert(0, str(PRIOR))
    import snd1_task_learning
    return snd1_task_learning


def wrap(degrees):
    return (np.asarray(degrees)+180) % 360-180


def dihedral(xyz, ids):
    p = np.asarray(xyz)[..., ids, :]
    b0, b1, b2 = p[..., 0, :]-p[..., 1, :], p[..., 2, :]-p[..., 1, :], p[..., 3, :]-p[..., 2, :]
    b1 = b1/np.linalg.norm(b1, axis=-1, keepdims=True)
    v = b0-(b0*b1).sum(-1, keepdims=True)*b1
    w = b2-(b2*b1).sum(-1, keepdims=True)*b1
    return np.degrees(np.arctan2((np.cross(b1, v)*w).sum(-1), (v*w).sum(-1)))


def moving_component(bonds, root, tip):
    adjacency = {}
    for a,b in bonds:
        if {a,b} == {root,tip}: continue
        adjacency.setdefault(a,set()).add(b); adjacency.setdefault(b,set()).add(a)
    seen, pending = set(), [tip]
    while pending:
        atom = pending.pop()
        if atom in seen: continue
        seen.add(atom); pending.extend(adjacency.get(atom,set())-seen)
    if root in seen: raise ValueError('Rotor must be an acyclic bridge')
    return sorted(seen)


def rotate(xyz, root, tip, moving, degrees):
    xyz = np.asarray(xyz, dtype=float).copy()
    origin = xyz[root].copy(); axis = xyz[tip]-origin
    axis /= np.linalg.norm(axis)
    vectors = xyz[moving]-origin
    angle = math.radians(float(degrees))
    xyz[moving] = origin+vectors*math.cos(angle)+np.cross(axis,vectors)*math.sin(angle)+np.outer(vectors@axis,axis)*(1-math.cos(angle))
    return xyz


def set_torsions(xyz, rotors, targets):
    answer = np.asarray(xyz,dtype=float).copy()
    for rotor,target in zip(rotors,targets):
        ids,moving = rotor['dihedral'],rotor['moving']
        answer = rotate(answer,ids[1],ids[2],moving,float(wrap(target-dihedral(answer,ids))))
    if np.max(np.abs(wrap(np.asarray([dihedral(answer,r['dihedral']) for r in rotors])-targets))) > 1e-7:
        raise ValueError('Torsion construction failed')
    return answer


def rigid_fit(source, target):
    source,target = np.asarray(source),np.asarray(target)
    a,b = source.mean(0),target.mean(0)
    u,_,vt = np.linalg.svd((source-a).T@(target-b))
    correction = np.eye(3); correction[-1,-1] = np.linalg.det(u@vt)
    rotation = u@correction@vt
    return rotation,b-a@rotation


def minimum_image(vectors, box):
    fractional = np.asarray(vectors)@np.linalg.inv(box)
    return (fractional-np.rint(fractional))@box


def model(system, prior):
    from rdkit import Chem
    atoms,bonds = prior.topology_sections(prior.P4/'controls'/system/'ligand.itp')
    molecule = Chem.SDMolSupplier(str(prior.P4/'inputs'/system.split('-')[1]/'ligand.sdf'),removeHs=False)[0]
    return molecular_model(molecule, atoms, bonds)


def molecular_model(molecule, atoms, bonds):
    """Build the same local chemistry from explicit, mapped producer inputs.

    This avoids loading an entire historical trajectory to score one new pose.
    Atom order must already agree with the native topology.
    """
    from rdkit import Chem
    from rdkit.Chem import AllChem
    molecule = Chem.Mol(molecule)
    if molecule.GetNumAtoms() != len(atoms):
        raise ValueError('Molecule and native atom map differ')
    sulfur = next(a.GetIdx() for a in molecule.GetAtoms() if a.GetAtomicNum()==16)
    nitrogen = next(a.GetIdx() for a in molecule.GetAtomWithIdx(sulfur).GetNeighbors() if a.GetAtomicNum()==7)
    aryl = next(a.GetIdx() for a in molecule.GetAtomWithIdx(sulfur).GetNeighbors() if a.GetAtomicNum()==6)
    core_carbon = next(a.GetIdx() for a in molecule.GetAtomWithIdx(nitrogen).GetNeighbors() if a.GetAtomicNum()==6)
    hydrogen = next(a.GetIdx() for a in molecule.GetAtomWithIdx(nitrogen).GetNeighbors() if a.GetAtomicNum()==1)
    core_neighbor = min(a.GetIdx() for a in molecule.GetAtomWithIdx(core_carbon).GetNeighbors() if a.GetIdx()!=nitrogen and a.GetAtomicNum()>1)
    aryl_neighbor = min(a.GetIdx() for a in molecule.GetAtomWithIdx(aryl).GetNeighbors() if a.GetIdx()!=sulfur and a.GetAtomicNum()>1)
    ids = [(core_neighbor,core_carbon,nitrogen,sulfur),(core_carbon,nitrogen,sulfur,aryl),(nitrogen,sulfur,aryl,aryl_neighbor)]
    rotors = [dict(dihedral=list(d),names=[atoms[i]['name'] for i in d],moving=moving_component(bonds,d[1],d[2])) for d in ids]
    core = sorted(i for i in moving_component(bonds,nitrogen,core_carbon) if molecule.GetAtomWithIdx(i).GetAtomicNum()>1)
    props = AllChem.MMFFGetMoleculeProperties(molecule,mmffVariant='MMFF94s')
    if props is None: raise ValueError('MMFF94s parameters missing')
    forcefield = AllChem.MMFFGetMoleculeForceField(molecule,props)
    neighbors = {i:[] for i in range(len(atoms))}
    for a,b in bonds: neighbors[a].append(b); neighbors[b].append(a)
    angles = [(a,c,b) for c,near in neighbors.items() for j,a in enumerate(near) for b in near[j+1:]]
    stereo = [(a.GetIdx(),[n.GetIdx() for n in a.GetNeighbors()][:3]) for a in molecule.GetAtoms() if str(a.GetChiralTag())!='CHI_UNSPECIFIED']
    return dict(molecule=molecule,atoms=atoms,bonds=np.asarray(bonds),angles=np.asarray(angles),rotors=rotors,core=core,
        donor=nitrogen,hydrogen=hydrogen,heavy=[a.GetIdx() for a in molecule.GetAtoms() if a.GetAtomicNum()>1],
        forcefield=forcefield,stereo=stereo,radii=np.asarray([Chem.GetPeriodicTable().GetRvdw(a.GetAtomicNum()) for a in molecule.GetAtoms()]))


def bond_angles(xyz, ids):
    a,b = xyz[ids[:,0]]-xyz[ids[:,1]],xyz[ids[:,2]]-xyz[ids[:,1]]
    return np.degrees(np.arccos(np.clip((a*b).sum(1)/np.linalg.norm(a,axis=1)/np.linalg.norm(b,axis=1),-1,1)))


def shape_check(xyz, reference, chemical):
    b=chemical['bonds']; a=chemical['angles']
    bond = float(np.max(np.abs(np.linalg.norm(xyz[b[:,0]]-xyz[b[:,1]],axis=1)-np.linalg.norm(reference[b[:,0]]-reference[b[:,1]],axis=1))))
    angle = float(np.max(np.abs(bond_angles(xyz,a)-bond_angles(reference,a))))
    stereo = all(np.sign(np.linalg.det(xyz[n]-xyz[c]))==np.sign(np.linalg.det(reference[n]-reference[c])) for c,n in chemical['stereo'])
    return bond,angle,stereo


def frame_context(case, chemical, index):
    from rdkit import Chem
    from scipy.spatial import cKDTree
    xyz=case['trajectory'].xyz[index].astype(float)*10
    offset=len(xyz)-len(chemical['atoms'])
    protein=[a for a in case['trajectory'].topology.atoms if a.index<offset and a.element.symbol!='H']
    box=case['trajectory'].unitcell_vectors[index].astype(float)*10
    ligand=xyz[offset:].copy()
    # Replicate original periodic protein near this ligand; exact nearest atom
    # for each radius/element class makes the clash ratio an exact minimum.
    trees=[]
    for symbol in sorted({a.element.symbol for a in protein}):
        rows=np.asarray([a.index for a in protein if a.element.symbol==symbol])
        central=ligand.mean(0)+minimum_image(xyz[rows]-ligand.mean(0),box)
        images=np.concatenate([central+np.asarray([i,j,k])@box for i in (-1,0,1) for j in (-1,0,1) for k in (-1,0,1)])
        trees.append((cKDTree(images),Chem.GetPeriodicTable().GetRvdw(symbol)))
    ctx=dict(xyz=xyz,ligand=ligand,offset=offset,box=box,trees=trees,case=case,chemical=chemical,index=int(index))
    ctx['input_energy']=float(chemical['forcefield'].CalcEnergy(ligand.ravel().tolist()))
    ctx['input_clash_ratio']=clash_ratio(ligand,ctx)
    return ctx


def clash_ratio(ligand,ctx):
    m=ctx['chemical']; heavy=m['heavy']
    return float(min(np.min(tree.query(ligand[heavy],workers=1)[0]/(radius+m['radii'][heavy])) for tree,radius in ctx['trees']))


def score(ligand,ctx):
    m=ctx['chemical']; case=ctx['case']; xyz=ctx['xyz'].copy(); xyz[ctx['offset']:]=ligand
    triples=case['triples']; v=minimum_image(xyz[triples[:,0]]-xyz[triples[:,1]],ctx['box']); w=minimum_image(xyz[triples[:,2]]-xyz[triples[:,1]],ctx['box'])
    distance=np.linalg.norm(minimum_image(xyz[triples[:,0]]-xyz[triples[:,2]],ctx['box']),axis=1)
    angle=np.degrees(np.arccos(np.clip((v*w).sum(1)/np.linalg.norm(v,axis=1)/np.linalg.norm(w,axis=1),-1,1)))
    labels=np.asarray(case['triple_residues']); primary=(distance<=3)&(angle>=150); secondary=(distance<=3.5)&(angle>=150)
    energy=float(m['forcefield'].CalcEnergy(ligand.ravel().tolist())); ratio=clash_ratio(ligand,ctx)
    bond,ba,stereo=shape_check(ligand,ctx['ligand'],m)
    delta=energy-ctx['input_energy']
    valid=bond<=1e-5 and ba<=1e-5 and stereo and delta<=10 and ratio>=.65 and ratio>=min(ctx['input_clash_ratio'],.75)-1e-9
    targets={}
    for target,residues in [('SER254',['SER254']),('ASN281_or_ARG255',['ASN281','ARG255']),('prepared',case['reference_contacts']),('any',case['residues'])]:
        eligible=np.isin(labels,residues)
        loss=np.maximum(distance/3-1,0)**2+np.maximum((150-angle)/30,0)**2
        best=int(np.argmin(np.where(eligible,loss,np.inf)))
        targets[target]=dict(primary=bool(primary[eligible].any()),sensitivity=bool(secondary[eligible].any()),
             loss=float(loss[best]),best_DA_A=float(distance[best]),best_DHA_deg=float(angle[best]),
             triple=triples[best].tolist(),residue=str(labels[best]))
    return dict(valid=bool(valid),energy_kcal_mol=energy,energy_change_kcal_mol=delta,min_vdw_ratio=ratio,
        bond_error_A=bond,angle_error_deg=ba,stereo_preserved=stereo,targets=targets)


def load_with_cached(run_id,prior):
    case=prior.load_case(run_id); folder=PRIOR/('development' if run_id==prior.DEVELOPMENT else run_id)
    if case['originals'] != prior.read(folder/'input-identities.json'): raise ValueError('Original input identity changed')
    cache=np.load(folder/'directional-frames.npz',allow_pickle=False)
    if not np.array_equal(cache['time_ps'],case['trajectory'].time): raise ValueError('Cached endpoint time binding failed')
    return case,cache,pin(folder/'directional-frames.npz')


def aligned_cache(run_id,prior):
    old=np.load(prior.ANALYSIS/run_id/'attempt-001/frame-data.npz',allow_pickle=False)
    xyz=old['ligand_aligned_nm'].astype(float)*10
    if run_id==prior.DEVELOPMENT:
        extension=np.load(prior.EXTENSION/'frame-data.npz',allow_pickle=False)
        xyz=np.concatenate([xyz,extension['aligned_ligand_nm'][1:]*10])
    return xyz


def observe(run_id,out,prior):
    import mdtraj as md
    begin=time.perf_counter(); cpu=time.process_time()
    case,cache,cachepin=load_with_cached(run_id,prior); m=model(case['system'],prior)
    offset=case['trajectory'].n_atoms-len(m['atoms']); ligand=case['trajectory'].xyz[:,offset:]*10
    torsions=np.asarray([dihedral(ligand,r['dihedral']) for r in m['rotors']]).T
    # Reuse the producer's verified lattice-image correction and protein fit.
    # An uncorrected ligand image can create spurious box-scale displacement.
    core=aligned_cache(run_id,prior)[:,m['core']]; reference=case['reference'].xyz[0,offset+np.asarray(m['core'])]*10
    displacement=np.linalg.norm(core.mean(1)-reference.mean(0),axis=1)
    core_rmsd=np.sqrt(((core-reference)**2).sum(2).mean(1))
    serO=[a.index for a in case['trajectory'].topology.atoms if a.residue.name=='SER' and a.residue.resSeq==254 and a.name=='O']
    if len(serO)!=1: raise ValueError('Unique SER254 backbone O required')
    da=md.compute_distances(case['trajectory'],np.asarray([[offset+m['donor'],serO[0]]]),periodic=True)[:,0]*10
    dha=md.compute_angles(case['trajectory'],np.asarray([[offset+m['donor'],offset+m['hydrogen'],serO[0]]]),periodic=True)[:,0]*180/np.pi
    labels=cache['residues'].tolist(); endpoint=cache['primary']; states={}
    target_masks={target:endpoint[:,[i for i,x in enumerate(labels) if x in rs]].any(1) for target,rs in [('SER254',['SER254']),('ASN281_or_ARG255',['ASN281','ARG255'])]}
    for target,mask in target_masks.items():
        states[target]=dict(frames=int(mask.sum()),occupancy=float(mask.mean()),
            mean_core_displacement_A=float(displacement[mask].mean()) if mask.any() else None,
            mean_core_RMSD_A=float(core_rmsd[mask].mean()) if mask.any() else None,
            circular_torsion_mean_deg=np.degrees(np.angle(np.exp(1j*np.radians(torsions[mask])).mean(0))).tolist() if mask.any() else None,
            donor_position_reachable_fraction=float((da[mask]<=3).mean()) if mask.any() else None)
    indices=np.rint(np.linspace(0,len(ligand)-1,13)).astype(int)
    np.savez_compressed(out/f'{run_id}-geometry.npz',torsions_deg=torsions,core_displacement_A=displacement,core_RMSD_A=core_rmsd,
        donor_DA_A=da,donor_DHA_deg=dha,time_ps=case['trajectory'].time,selected_indices=indices,**target_masks)
    summary=dict(run_id=run_id,frames=len(ligand),selected_indices=indices.tolist(),rotors=m['rotors'],core_atom_indices=m['core'],
        states=states,donor_position_reachable_fraction=float((da<=3).mean()),donor_primary_fraction=float(((da<=3)&(dha>=150)).mean()),
        windows=[dict(start_ps=s,end_ps=s+5000,frames=int(((case['trajectory'].time>=s)&(case['trajectory'].time<(s+5000) if s+5000<case['trajectory'].time[-1] else case['trajectory'].time<=s+5000)).sum()),
                      donor_reachable_fraction=float((da[(case['trajectory'].time>=s)&(case['trajectory'].time<(s+5000) if s+5000<case['trajectory'].time[-1] else case['trajectory'].time<=s+5000)]<=3).mean())) for s in range(0,int(case['trajectory'].time[-1]),5000)],
        originals=case['originals'],cached_endpoint=cachepin,cost=dict(wall_seconds=time.perf_counter()-begin,CPU_seconds=time.process_time()-cpu))
    save(out/f'{run_id}-observation.json',summary)
    return summary


def torsion_bank(geometry):
    bank={}
    for target in ['SER254','ASN281_or_ARG255']:
        members=np.flatnonzero(geometry[target])
        # No evaluation trajectories are available to fitting.
        chosen=members[np.rint(np.linspace(0,len(members)-1,min(24,len(members)))).astype(int)] if len(members) else np.asarray([],dtype=int)
        bank[target]=dict(indices=chosen.tolist(),torsions_deg=geometry['torsions_deg'][chosen].tolist())
    return bank


def target_nh_angle(ligand,ctx):
    m=ctx['chemical']; ids=m['rotors'][0]['dihedral']; n=m['donor']; h=m['hydrogen']
    ser=next(a.index for a in ctx['case']['trajectory'].topology.atoms if a.residue.name=='SER' and a.residue.resSeq==254 and a.name=='O')
    axis=ligand[ids[2]]-ligand[ids[1]]; axis/=np.linalg.norm(axis)
    current=ligand[h]-ligand[n]; desired=minimum_image(ctx['xyz'][ser]-ligand[n],ctx['box'])
    current-=np.dot(current,axis)*axis; desired-=np.dot(desired,axis)*axis
    delta=np.degrees(np.arctan2(np.dot(axis,np.cross(current,desired)),np.dot(current,desired)))
    return float(wrap(dihedral(ligand,ids)+delta))


def proposal_torsions(ctx,arm,target,bank):
    m=ctx['chemical']; base=np.asarray([dihedral(ctx['ligand'],r['dihedral']) for r in m['rotors']])
    output=[base]
    library=np.asarray(bank[target]['torsions_deg'])
    for i in range(1,96):
        low=wrap(360*np.mod(i*np.asarray([math.sqrt(2),math.sqrt(3),math.sqrt(5)]),1)-180)
        if arm=='baseline' or not len(library): value=low
        else:
            value=library[(i-1)%len(library)].copy()
            if i>len(library): value=wrap(value+low*.12)
            if target=='SER254' and i%2: value[0]=target_nh_angle(ctx['ligand'],ctx)
        output.append(value)
    return np.asarray(output)


def search(run_id,out,prior,bank,phase='torsion'):
    start=time.perf_counter(); cpu=time.process_time(); case,cache,cachepin=load_with_cached(run_id,prior); m=model(case['system'],prior)
    indices=np.rint(np.linspace(0,len(case['trajectory'])-1,13)).astype(int); rows=[]; best_coordinates=[]
    for frame in indices:
        ctx=frame_context(case,m,int(frame)); identity=score(ctx['ligand'],ctx)
        for target in ['SER254','ASN281_or_ARG255']:
            arms={}
            order=['baseline','candidate'] if (int(frame)+int(run_id[-1]))%2 else ['candidate','baseline']
            for arm in order:
                begin=time.perf_counter(); cp=time.process_time(); proposals=proposal_torsions(ctx,arm,target,bank); scores=[]; coordinates=[]
                for i,angles in enumerate(proposals):
                    pose=set_torsions(ctx['ligand'],m['rotors'],angles)
                    if phase=='joint': pose=joint_proposal(pose,ctx,arm,target,i)
                    coordinates.append(pose); scores.append(score(pose,ctx))
                eligible=[i for i,s in enumerate(scores) if s['valid']]
                best=min(eligible,key=lambda i:(scores[i]['targets'][target]['loss'],scores[i]['energy_change_kcal_mol'])) if eligible else 0
                arms[arm]=dict(proposals=96,valid_count=len(eligible),primary_success=any(scores[i]['targets'][target]['primary'] for i in eligible),
                    sensitivity_success=any(scores[i]['targets'][target]['sensitivity'] for i in eligible),best_proposal=best,best=scores[best],
                    all_scores=scores,cost=dict(wall_seconds=time.perf_counter()-begin,CPU_seconds=time.process_time()-cp))
                best_coordinates.append(coordinates[best])
            rows.append(dict(frame=int(frame),time_ps=float(case['trajectory'].time[frame]),target=target,input=identity,arms=arms))
        if time.perf_counter()-start>90: raise TimeoutError('Frozen per-case 90 second limit')
    np.savez_compressed(out/f'{run_id}-{phase}-best-poses.npz',coordinates_A=np.asarray(best_coordinates))
    result=dict(run_id=run_id,phase=phase,rows=rows,cached_endpoint=cachepin,
        cost=dict(wall_seconds=time.perf_counter()-start,CPU_seconds=time.process_time()-cpu),new_MD=0,GPU=0,
        summary={target:{arm:dict(primary_successes=sum(r['arms'][arm]['primary_success'] for r in rows if r['target']==target),
            sensitivity_successes=sum(r['arms'][arm]['sensitivity_success'] for r in rows if r['target']==target),
            total_valid=sum(r['arms'][arm]['valid_count'] for r in rows if r['target']==target),snapshots=13,
            scoring_wall_seconds=sum(r['arms'][arm]['cost']['wall_seconds'] for r in rows if r['target']==target)) for arm in ['baseline','candidate']} for target in ['SER254','ASN281_or_ARG255']})
    save(out/f'{run_id}-{phase}-search.json',result); return result


def counterfactual(out,prior):
    start=time.perf_counter(); cpu=time.process_time(); case,_,_=load_with_cached(prior.DEVELOPMENT,prior); m=model(case['system'],prior)
    frames=np.rint(np.linspace(0,len(case['trajectory'])-1,13)).astype(int); rows=[]; poses=[]
    offset=case['trajectory'].n_atoms-len(m['atoms']); aligned=aligned_cache(prior.DEVELOPMENT,prior)
    for a,b in zip(frames[:6],frames[-6:]):
        for destination,partner in [(a,b),(b,a)]:
            ctx=frame_context(case,m,int(destination))
            # Recover the exact cached protein-fit/image transform using all
            # ligand atoms, then put the partner into the destination frame.
            R,t=rigid_fit(ctx['ligand'],aligned[destination])
            error=float(np.max(np.linalg.norm(ctx['ligand']@R+t-aligned[destination],axis=1)))
            if error>.003: raise ValueError('Cached fit is not a rigid transform of the bound input')
            donor=(aligned[partner]-t)@R.T
            angles=np.asarray([dihedral(donor,r['dihedral']) for r in m['rotors']]); R,t=rigid_fit(ctx['ligand'][m['core']],donor[m['core']])
            changed=set_torsions(ctx['ligand'],m['rotors'],angles)
            values={'identity':ctx['ligand'],'torsion':changed,'rigid_body':ctx['ligand']@R+t,'both':changed@R+t}
            rows.append(dict(destination=int(destination),partner=int(partner),core_translation_A=float(np.linalg.norm(donor[m['core']].mean(0)-ctx['ligand'][m['core']].mean(0))),arms={k:score(v,ctx) for k,v in values.items()}))
            poses.extend(values.values())
    np.savez_compressed(out/'counterfactual-poses.npz',coordinates_A=np.asarray(poses))
    result=dict(rows=rows,cost=dict(wall_seconds=time.perf_counter()-start,CPU_seconds=time.process_time()-cpu))
    save(out/'counterfactual.json',result); return result


def joint_proposal(pose,ctx,arm,target,index):
    if index==0: return ctx['ligand'].copy()
    # Both arms reserve half their proposals for rigid-only, avoiding the
    # unnecessary strain demonstrated by cross-frame torsion transplantation.
    if index<48: pose=ctx['ligand'].copy()
    fraction=np.mod(index*np.asarray([math.sqrt(2),math.sqrt(3),math.sqrt(5)]),1)
    axis=np.asarray([2*fraction[0]-1,2*fraction[1]-1,2*fraction[2]-1]);axis/=np.linalg.norm(axis)
    theta=(fraction[0]*2-1)*np.pi
    K=np.asarray([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
    rotation=np.eye(3)+np.sin(theta)*K+(1-np.cos(theta))*(K@K)
    center=pose.mean(0);pose=(pose-center)@rotation.T+center
    if arm=='baseline':
        direction=np.asarray([math.sin(index*2.39996),math.cos(index*2.39996),2*fraction[1]-1]);direction/=np.linalg.norm(direction)
        return pose+direction*(12*fraction[2]**(1/3))
    m=ctx['chemical'];offset=ctx['offset'];case=ctx['case']
    if target=='SER254':
        ser=next(a.index for a in case['trajectory'].topology.atoms if a.residue.name=='SER' and a.residue.resSeq==254 and a.name=='O')
        n,h=m['donor'],m['hydrogen'];u=pose[h]-pose[n];u/=np.linalg.norm(u)
        desired=pose[n]+minimum_image(ctx['xyz'][ser]-pose[n],ctx['box'])-2.8*u
        translation=desired-pose[n]
    else:
        triples=case['triples'][np.isin(case['triple_residues'],['ASN281','ARG255'])]
        d=np.linalg.norm(minimum_image(ctx['xyz'][triples[:,0]]-ctx['xyz'][triples[:,2]],ctx['box']),axis=1)
        eligible=triples[np.argsort(d)[:8]]
        donor,hydrogen,acceptor=eligible[(index-1)%len(eligible)]
        if donor<offset:
            u=minimum_image(ctx['xyz'][hydrogen]-ctx['xyz'][donor],ctx['box']);u/=np.linalg.norm(u)
            desired=ctx['xyz'][donor]+2.8*u
            translation=minimum_image(desired-pose[acceptor-offset],ctx['box'])
        else:
            u=pose[hydrogen-offset]-pose[donor-offset];u/=np.linalg.norm(u)
            translation=minimum_image(ctx['xyz'][acceptor]-pose[donor-offset],ctx['box'])-2.8*u
    # Identical 12 A translation support. Out-of-support anchor moves get
    # clipped and remain in the denominator; no impossible candidate omitted.
    norm=np.linalg.norm(translation)
    if norm>12: translation*=12/norm
    return pose+translation


def reachable_anchor_actions(donor,rotors,distance_A,cutoff_A=3.0):
    """Exact donor-position certificate for these rooted bridge rotations.

    A moved donor is fixed only when it lies on the rotor axis. This does not
    classify any other donor/acceptor, core root, receptor, or action family.
    When the donor is invariant under these torsions, d - cutoff bounds its
    required spatial displacement for contact with the fixed acceptor. It
    does not bound ligand-centroid translation: a rigid rotation can displace
    the donor even when the centroid does not translate.
    """
    fixed=all(donor not in r['moving'] or donor in r['dihedral'][1:3] for r in rotors)
    excluded=bool(fixed and distance_A>cutoff_A)
    return dict(donor_position_invariant=fixed,donor_specific_torsion_contact_excluded=excluded,
        minimum_donor_displacement_A=max(float(distance_A)-cutoff_A,0) if fixed else None,
        preferred_action_family='rigid_or_joint' if excluded else 'orientation_then_rigid_or_joint',
        scope='Fixed protein acceptor and box, specified donor and rooted bridge rotations only')


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--phase',choices=['observe','correct-observe','correct-counterfactual','develop','evaluate','joint-develop','joint-evaluate'],required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); start=time.perf_counter(); cpu=time.process_time()
    if hasattr(os,'sched_getaffinity'): os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    args.output.mkdir(parents=True,exist_ok=True); prior=prior_module(); resultfile=args.output/f'{args.phase}-result.json'
    if resultfile.exists(): raise FileExistsError('Completed phase is immutable; use a new version directory')
    source=pin(__file__); protocol=pin(args.output/'PROTOCOL.json')
    if args.phase=='correct-counterfactual':
        corrected=args.output/'geometry-v2';corrected.mkdir(exist_ok=True)
        result=counterfactual(corrected,prior); rows=[dict(run_id=prior.DEVELOPMENT,cost=result['cost'])]
    elif args.phase in ['observe','correct-observe']:
        destination=args.output if args.phase=='observe' else args.output/'geometry-v2';destination.mkdir(exist_ok=True)
        rows=[observe(run_id,destination,prior) for run_id in [prior.DEVELOPMENT,*prior.EVALUATION]]
        if args.phase=='correct-observe':
            geometry=np.load(destination/f'{prior.DEVELOPMENT}-geometry.npz',allow_pickle=False)
            old=np.load(args.output/f'{prior.DEVELOPMENT}-geometry.npz',allow_pickle=False)
            if not np.array_equal(geometry['torsions_deg'],old['torsions_deg']): raise ValueError('Unrelated torsion feature changed')
        else:
            geometry=np.load(args.output/f'{prior.DEVELOPMENT}-geometry.npz',allow_pickle=False)
            save(args.output/'BANK-FREEZE.json',dict(bank=torsion_bank(geometry),source=source,protocol=protocol,fit_run=prior.DEVELOPMENT,created_unix=time.time()))
    else:
        bank=json.loads((args.output/'BANK-FREEZE.json').read_text('utf-8'))['bank']
        if args.phase=='develop':
            rows=[search(prior.DEVELOPMENT,args.output,prior,bank)]; counterfactual(args.output,prior)
        else:
            runs=[prior.DEVELOPMENT] if args.phase=='joint-develop' else prior.EVALUATION
            rows=[search(run,args.output,prior,bank,'joint' if args.phase.startswith('joint') else 'torsion') for run in runs]
    save(resultfile,dict(phase=args.phase,status='complete',source=source,protocol=protocol,prior_source=pin(PRIOR/'snd1_task_learning.py'),
        modules={name:getattr(sys.modules.get(name),'__file__',None) for name in ['numpy','rdkit','mdtraj','scipy','snd1_task_learning']},
        cost=dict(wall_seconds=time.perf_counter()-start,CPU_seconds=time.process_time()-cpu),
        cases=[dict(run_id=r['run_id'],summary=r.get('summary'),cost=r['cost']) for r in rows]))
    print(json.dumps(dict(status='complete',phase=args.phase,cost=json.loads(resultfile.read_text('utf-8'))['cost'])))


if __name__=='__main__': main()
