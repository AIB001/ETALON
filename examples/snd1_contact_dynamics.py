"""Explain saved SND1 contact loss without rerunning dynamics or changing its endpoint.

Frozen donor/H/acceptor identities distinguish contact motion from changing
best-contact identity. Coordinate swaps diagnose geometry, not causal forces.
Water contacts and threshold sensitivity remain descriptive, not adoption tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np


def minimum_image(delta, box):
    frac = np.asarray(delta) @ np.linalg.inv(box)
    return (frac - np.rint(frac)) @ box


def contact_geometry(xyz, box, triples):
    triples = np.asarray(triples, dtype=int)
    d, h, a = (xyz[triples[:, i]] for i in range(3))
    dh, ah = minimum_image(d-h, box), minimum_image(a-h, box)
    distance = np.linalg.norm(minimum_image(d-a, box), axis=-1)
    denominator = np.linalg.norm(dh, axis=-1)*np.linalg.norm(ah, axis=-1)
    if np.any(denominator < 1e-10):
        raise ValueError('Degenerate donor-hydrogen-acceptor coordinates')
    angle = np.degrees(np.arccos(np.clip(np.sum(dh*ah, axis=-1)/denominator, -1, 1)))
    return distance, angle


def rigid_fit(source, target):
    a, b = np.asarray(source), np.asarray(target)
    u, _, vt = np.linalg.svd((a-a.mean(0)).T @ (b-b.mean(0)))
    rotation = u @ np.diag([1., 1., np.linalg.det(u @ vt)]) @ vt
    return rotation, b.mean(0)-a.mean(0) @ rotation


def local_reference(xyz, box, ligand_start, ligand_end):
    """Place the contact neighborhood in one image before rotating coordinates.

    Rotating periodically separated D/A images but leaving the original box
    fixed changes apparent contact geometry. Centering the reference first
    keeps this local comparison Euclidean; native geometry still uses MIC.
    """
    anchor=xyz[ligand_start:ligand_end].mean(0)
    return anchor+minimum_image(xyz-anchor,box)


def pin(path):
    path = Path(path); h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return dict(path=str(path.absolute()), bytes=path.stat().st_size, sha256=h.hexdigest())


def _fraction(mask):
    return float(np.mean(mask)) if len(mask) else None


def periodic_torsion_energy(angles_rad, phase_deg, k_kJ_mol, multiplicity):
    """GROMACS explicit proper dihedral type 1/9, in kJ/mol."""
    return np.asarray(k_kJ_mol)*(1+np.cos(np.asarray(multiplicity)*angles_rad-np.radians(phase_deg)))


def torsion_component(case_root, output, analysis_result):
    """QOV S1-N3 explicit proper-torsion component, excluding other energies."""
    import mdtraj as md
    case_root,output=Path(case_root).absolute(),Path(output).absolute()
    primary=json.loads(Path(analysis_result).read_text())
    if output.exists():raise FileExistsError('Reuse existing torsion readout')
    output.mkdir(parents=True);started=time.perf_counter();cpu=time.process_time()
    pair_path=next((case_root/'pairs').glob('*/pair.json'));pair=json.loads(pair_path.read_text())
    rows=[];originals=[pin(analysis_result)]
    for arm in ('input','candidate'):
        folder=case_root/'primary'/pair['run_id']/arm;itp=folder/'ligand.itp'
        section='';atoms={};raw=[]
        for line in itp.read_text().splitlines():
            text=line.split(';')[0].strip()
            if not text:continue
            if text.startswith('['):section=text.strip('[] ').strip();continue
            fields=text.split()
            if section=='atoms':atoms[fields[4]]=int(fields[0])
            if section=='dihedrals':raw.append(fields)
        bond=[atoms['S1'],atoms['N3']];terms=[]
        for fields in raw:
            if set(map(int,fields[1:3]))!=set(bond):continue
            if int(fields[4]) not in (1,9) or len(fields)!=8:
                raise ValueError('Expected explicit proper torsion parameters, no implicit/default lookup')
            ids=list(map(int,fields[:4]))
            if ids[1:3]!=bond:ids.reverse()
            terms.append({'atoms_1based':ids,'function':int(fields[4]),'phase_deg':float(fields[5]),
                'k_kJ_mol':float(fields[6]),'multiplicity':int(fields[7])})
        if not terms:raise ValueError('No explicit S1-N3 proper torsion terms')
        reference=md.load(str(folder/'em.gro'));trajectory=md.load(str(folder/'mdjob/md.xtc'),top=reference.topology)
        indices=np.array([t['atoms_1based'] for t in terms])+pair['ligand_offset']-1
        phi=md.compute_dihedrals(trajectory,indices,periodic=True).astype(float)
        phi0=md.compute_dihedrals(reference,indices,periodic=True)[0].astype(float)
        phases=np.array([t['phase_deg'] for t in terms]);ks=np.array([t['k_kJ_mol'] for t in terms]);ns=np.array([t['multiplicity'] for t in terms])
        energy=periodic_torsion_energy(phi,phases,ks,ns).sum(axis=-1)
        initial=float(periodic_torsion_energy(phi0,phases,ks,ns).sum())
        scan_deg=np.arange(-180.,181.);scan=periodic_torsion_energy(phi0+np.radians(scan_deg[:,None]),phases,ks,ns).sum(axis=-1)
        np.savez_compressed(output/(arm+'-energy.npz'),time_ps=trajectory.time,proper_SN_energy_kJ_mol=energy,
            scan_deg=scan_deg,scan_SN_only_kJ_mol=scan)
        rows.append({'arm':arm,'terms':terms,'EM_component_kJ_mol':initial,
            'late_mean_component_kJ_mol':float(energy[trajectory.time>=50].mean()),
            'late_mean_delta_from_EM_kJ_mol':float(energy[trajectory.time>=50].mean()-initial),
            'observed_min_max_kJ_mol':[float(energy.min()),float(energy.max())],
            'fixed_EM_geometry_SN_component_scan_range_kJ_mol':float(scan.max()-scan.min()),
            'fixed_EM_geometry_SN_component_scan_min_max_kJ_mol':[float(scan.min()),float(scan.max())]})
        originals += [pin(itp),pin(folder/'em.gro'),pin(folder/'mdjob/md.xtc'),pin(folder/'mdjob/input.tpr')]
    result={'schema':'snd1-native-torsion-component/1','rows':rows,'originals':originals,'source':pin(__file__),
        'formula_source':'https://manual.gromacs.org/2026.1/reference-manual/functions/bonded-interactions.html#proper-dihedrals-periodic-type',
        'scope':'Sum of explicit S1-N3 proper dihedral terms only. Excludes other bonded terms, 1-4, solvent/protein interactions, entropy and free energy. A component scan is not a molecular torsional free-energy barrier.',
        'new_MD':0,'cost':{'wall_seconds':time.perf_counter()-started,'CPU_seconds':time.process_time()-cpu}}
    (output/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'result':str(output/'RESULT.json'),'rows':rows,'cost':result['cost']}));return result


def factorial_motion(case_root, output, analysis_result):
    """Eight coordinate counterfactuals, not eight simulations or binding poses.

    Remove rigid ligand pose change, internal change, and donor motion separately.
    Complete restoration is a geometric identity control, never a method gain.
    """
    import mdtraj as md
    from rdkit import Chem
    case_root,output=Path(case_root).absolute(),Path(output).absolute()
    primary=json.loads(Path(analysis_result).read_text())
    for source in primary['originals']:
        if pin(source['path'])!=source:raise ValueError('Prior analysis original changed')
    if output.exists():raise FileExistsError('Reuse the existing factorial result, not a new execution')
    output.mkdir(parents=True);started,cpu=time.perf_counter(),time.process_time()
    pair_path=next((case_root/'pairs').glob('*/pair.json'));pair=json.loads(pair_path.read_text())
    offset,end=pair['ligand_offset'],pair['ligand_offset']+pair['ligand_atoms']
    mol=Chem.SDMolSupplier(str(pair_path.parent/'exact-input.sdf'),removeHs=False)[0]
    heavy=offset+np.array([a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum()>1])
    rows=[];series={}
    for prior in primary['rows']:
        folder=case_root/'primary'/pair['run_id']/prior['arm']
        ref=md.load(str(folder/'em.gro'));traj=md.load(str(folder/'mdjob/md.xtc'),top=ref.topology)
        ca=np.array([a.index for a in ref.topology.atoms if a.index<offset and a.name=='CA'])
        box0=ref.unitcell_vectors[0].astype(float)*10
        base=local_reference(ref.xyz[0].astype(float)*10,box0,offset,end)
        triples=np.asarray(prior['fixed_triples'],dtype=int)
        hit=np.zeros((len(traj),8,2),dtype=bool)
        for fi,frame in enumerate(traj):
            xyz=frame.xyz[0].astype(float)*10;box=frame.unitcell_vectors[0].astype(float)*10
            whole=base+minimum_image(xyz-base,box)
            rot,shift=rigid_fit(whole[ca],base[ca]);aligned=whole@rot+shift
            current=aligned[offset:end]
            forward,translation=rigid_fit(base[heavy],aligned[heavy])
            inverse,inverse_translation=rigid_fit(aligned[heavy],base[heavy])
            for mask in range(8):
                pose_reset,internal_reset,donor_reset=(bool(mask&(1<<i)) for i in range(3))
                if pose_reset and internal_reset:ligand=base[offset:end]
                elif pose_reset:ligand=current@inverse+inverse_translation
                elif internal_reset:ligand=base[offset:end]@forward+translation
                else:ligand=current
                mixed=aligned.copy();mixed[offset:end]=ligand
                if donor_reset:mixed[:offset]=base[:offset]
                da,angle=contact_geometry(mixed,box0,triples)
                hit[fi,mask]=(da<=3)&(angle>=150)
                if mask==0:
                    native_d,native_a=contact_geometry(xyz,box,triples)
                    if not np.allclose(da,native_d,atol=1e-8) or not np.allclose(angle,native_a,atol=1e-8):
                        raise ValueError('Zero-intervention geometry must equal the native frame')
        conditions=[]
        for mask in range(8):
            conditions.append({'reset_to_EM':[name for i,name in enumerate(['ligand_rigid_pose','ligand_internal_geometry','protein_donors']) if mask&(1<<i)],
                'whole_dual_count':int(hit[:,mask].all(axis=1).sum()),
                'early_0_10ps_dual_count':int(hit[traj.time<=10,mask].all(axis=1).sum()),
                'late_50_100ps_dual_count':int(hit[traj.time>=50,mask].all(axis=1).sum()),
                'late_contact_counts':dict(zip(['ASN281','ARG255'],hit[traj.time>=50,mask].sum(axis=0).astype(int).tolist()))})
        rows.append({'arm':prior['arm'],'frames':len(traj),'early_frames':int(np.sum(traj.time<=10)),
            'late_frames':int(np.sum(traj.time>=50)),'conditions':conditions})
        series[prior['arm']+'_hit']=hit
    np.savez_compressed(output/'factorial.npz',**series)
    result={'schema':'snd1-contact-motion-factorial/1','prior_analysis':pin(analysis_result),'source':pin(__file__),
        'rows':rows,'new_MD':0,'quality':'Geometric decomposition only. Counterfactual coordinates are not force-relaxed, sterically verified or physically realizable candidates.',
        'cost':{'wall_seconds':time.perf_counter()-started,'CPU_seconds':time.process_time()-cpu}}
    (output/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'result':str(output/'RESULT.json'),'cost':result['cost'],'new_MD':0}))
    return result


def analyze(case_root, output):
    import mdtraj as md
    from rdkit import Chem
    from rdkit.Chem import Lipinski
    case_root, output = Path(case_root).absolute(), Path(output).absolute()
    if output.exists():
        raise FileExistsError('Existing analysis must be resumed from its saved result, not overwritten')
    output.mkdir(parents=True)
    started, cpu = time.perf_counter(), time.process_time()
    pair_files = list((case_root/'pairs').glob('*/pair.json'))
    if len(pair_files) != 1:
        raise ValueError('Select one exact paired case')
    pair_path = pair_files[0]; pair = json.loads(pair_path.read_text())
    offset, end = pair['ligand_offset'], pair['ligand_offset']+pair['ligand_atoms']
    molecule_path = pair_path.parent/'exact-input.sdf'
    mol = Chem.SDMolSupplier(str(molecule_path), removeHs=False)[0]
    if mol is None or mol.GetNumAtoms() != pair['ligand_atoms']:
        raise ValueError('Ligand atom mapping is unavailable')
    heavy = np.array([a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum()>1])
    torsions=[]
    for j,k in mol.GetSubstructMatches(Lipinski.RotatableBondSmarts):
        left=sorted(a.GetIdx() for a in mol.GetAtomWithIdx(j).GetNeighbors() if a.GetIdx()!=k and a.GetAtomicNum()>1)
        right=sorted(a.GetIdx() for a in mol.GetAtomWithIdx(k).GetNeighbors() if a.GetIdx()!=j and a.GetAtomicNum()>1)
        if left and right:torsions.append([left[0]+offset,j+offset,k+offset,right[0]+offset])
    originals=[pin(pair_path),pin(molecule_path),pin(case_root/'PROTOCOL.json')]
    rows=[]; arrays={}
    for arm in ('input','candidate'):
        folder=case_root/'primary'/pair['run_id']/arm
        contacts=json.loads((folder/'contacts.json').read_text())
        reference=md.load(str(folder/'em.gro'))
        trajectory=md.load(str(folder/'mdjob/md.xtc'),top=reference.topology)
        # The archived endpoint used trjconv's recompressed, whole-solute XTC.
        # Reproduce that exact input, and report its difference from native XTC.
        solute=md.load(str(folder/'solute-whole.xtc'),top=reference.topology.subset(np.arange(end)))
        if len(trajectory)!=len(contacts):raise ValueError('Contact and trajectory frame count mismatch')
        if not np.allclose(solute.time,trajectory.time):raise ValueError('Native/derived trajectory times differ')
        originals += [pin(folder/n) for n in ('em.gro','contacts.json','mdjob/md.xtc','mdjob/input.tpr','solute-whole.xtc')]
        labels=('ASN281','ARG255')
        fixed=np.array([contacts[0][label]['triple'] for label in labels])
        if np.any(fixed[:,:2]>=offset) or np.any(fixed[:,2]<offset) or np.any(fixed[:,2]>=end):
            raise ValueError('This analysis expects protein donors and mapped ligand acceptors')
        top=reference.topology
        ca=np.array([a.index for a in top.atoms if a.index<offset and a.name=='CA'])
        water=[]
        for residue in top.residues:
            if not residue.is_water:continue
            oxygen=[a.index for a in residue.atoms if a.element.symbol=='O']
            hydrogens=[a.index for a in residue.atoms if a.element.symbol=='H']
            if len(oxygen)==1 and len(hydrogens)==2:water.append([oxygen[0],*hydrogens])
        water=np.asarray(water,dtype=int)
        if len(ca)<3 or not len(water):raise ValueError('Backbone or explicit water mapping unavailable')
        basebox=reference.unitcell_vectors[0].astype(float)*10
        base=local_reference(reference.xyz[0].astype(float)*10,basebox,offset,end)
        shape=(len(trajectory),len(labels))
        distance=np.zeros(shape); angle=np.zeros(shape); ligand_swap=np.zeros((*shape,2));protein_swap=np.zeros((*shape,2))
        donor_water=np.zeros(shape,dtype=int);acceptor_water=np.zeros(shape,dtype=int)
        donor_shift=np.zeros(shape);acceptor_shift=np.zeros(shape);h_shift=np.zeros(shape)
        internal=np.zeros(len(trajectory));global_rmsd=np.zeros(len(trajectory));reproduction=[];conversion=[]
        for fi,frame in enumerate(trajectory):
            xyz=frame.xyz[0].astype(float)*10;box=frame.unitcell_vectors[0].astype(float)*10
            distance[fi],angle[fi]=contact_geometry(xyz,box,fixed)
            observed=np.array([contacts[fi][label]['triple'] for label in labels])
            da,an=contact_geometry(xyz,box,observed)
            ds,asn=contact_geometry(solute.xyz[fi].astype(float)*10,solute.unitcell_vectors[fi].astype(float)*10,observed)
            for li,label in enumerate(labels):
                original=contacts[fi][label]
                reproduction.append([abs(ds[li]-original['best_DA_A']),abs(asn[li]-original['best_DHA_deg'])])
                conversion.append([abs(da[li]-ds[li]),abs(an[li]-asn[li])])
            whole=base+minimum_image(xyz-base,box)
            rot,shift=rigid_fit(whole[ca],base[ca]);aligned=whole@rot+shift
            global_rmsd[fi]=np.sqrt(np.mean(np.sum((aligned[offset+heavy]-base[offset+heavy])**2,axis=-1)))
            rot2,shift2=rigid_fit(whole[offset+heavy],base[offset+heavy])
            internal[fi]=np.sqrt(np.mean(np.sum((whole[offset+heavy]@rot2+shift2-base[offset+heavy])**2,axis=-1)))
            mixed=base.copy();mixed[offset:end]=aligned[offset:end]
            ligand_swap[fi,:,0],ligand_swap[fi,:,1]=contact_geometry(mixed,basebox,fixed)
            mixed=base.copy();mixed[:offset]=aligned[:offset]
            protein_swap[fi,:,0],protein_swap[fi,:,1]=contact_geometry(mixed,basebox,fixed)
            donor_shift[fi]=np.linalg.norm(aligned[fixed[:,0]]-base[fixed[:,0]],axis=-1)
            h_shift[fi]=np.linalg.norm(aligned[fixed[:,1]]-base[fixed[:,1]],axis=-1)
            acceptor_shift[fi]=np.linalg.norm(aligned[fixed[:,2]]-base[fixed[:,2]],axis=-1)
            for li,(d,h,a) in enumerate(fixed):
                near=water[np.linalg.norm(minimum_image(xyz[water[:,0]]-xyz[d],box),axis=-1)<=3.5]
                if len(near):
                    triples=np.array([[d,h,o] for o in near[:,0]])
                    wd,wa=contact_geometry(xyz,box,triples)
                    donor_water[fi,li]=np.sum((wd<=3)&(wa>=150))
                near=water[np.linalg.norm(minimum_image(xyz[water[:,0]]-xyz[a],box),axis=-1)<=3.5]
                if len(near):
                    triples=np.array([[o,hwater,a] for o,h1,h2 in near for hwater in (h1,h2)])
                    wd,wa=contact_geometry(xyz,box,triples)
                    acceptor_water[fi,li]=np.sum((wd<=3)&(wa>=150))
        reproduction=np.asarray(reproduction)
        if reproduction.max()>1e-6:
            raise ValueError('Saved contact geometry does not reproduce from its original whole-solute coordinates')
        conversion=np.asarray(conversion)
        dihedrals=np.degrees(md.compute_dihedrals(trajectory,np.asarray(torsions,dtype=int),periodic=True))
        ref_dihedrals=np.degrees(md.compute_dihedrals(reference,np.asarray(torsions,dtype=int),periodic=True))[0]
        delta=(dihedrals-ref_dihedrals+180)%360-180
        times=trajectory.time.astype(float)
        strict=(distance<=3)&(angle>=150)
        archive_dual=np.array([c['ASN281']['primary'] and c['ARG255']['primary'] for c in contacts])
        sensitivity={}
        for maximum,minimum in [(3.,150.),(3.3,135.),(3.5,120.)]:
            mask=(distance<=maximum)&(angle>=minimum)
            sensitivity[f'{maximum}A_{minimum}deg']={'whole_dual':_fraction(mask.all(axis=1)),
                'late_dual':_fraction(mask[times>=50].all(axis=1)),
                'late_by_contact':{label:_fraction(mask[times>=50,li]) for li,label in enumerate(labels)}}
        windows=[]
        for lo,hi in [(0,5),(5,20),(20,50),(50,100)]:
            mask=(times>=lo)&(times<=hi)
            windows.append({'time_ps':[lo,hi],'frames':int(mask.sum()),
                'fixed_dual_fraction':_fraction(strict[mask].all(axis=1)),
                'mean_DA_A':distance[mask].mean(0).tolist(),'mean_DHA_deg':angle[mask].mean(0).tolist(),
                'ligand_global_rmsd_mean_A':float(global_rmsd[mask].mean()),
                'ligand_internal_rmsd_mean_A':float(internal[mask].mean())})
        early=[]
        for fi in [0,1,2,5,10,20,50,100]:
            if fi>=len(times):continue
            early.append({'time_ps':float(times[fi]),'DA_A':distance[fi].tolist(),'DHA_deg':angle[fi].tolist(),
                'ligand_only_swap':ligand_swap[fi].tolist(),'protein_only_swap':protein_swap[fi].tolist(),
                'donor_movement_A':donor_shift[fi].tolist(),'hydrogen_movement_A':h_shift[fi].tolist(),
                'acceptor_movement_A':acceptor_shift[fi].tolist(),
                'donor_water_contacts':donor_water[fi].tolist(),'acceptor_water_contacts':acceptor_water[fi].tolist(),
                'internal_rmsd_A':float(internal[fi]),'global_rmsd_A':float(global_rmsd[fi])})
        rows.append({'arm':arm,'labels':list(labels),'fixed_triples':fixed.tolist(),
            'atom_names':[[str(top.atom(int(i))) for i in triple] for triple in fixed],
            'frame_count':len(times),'archived_primary_dual_count':int(archive_dual.sum()),
            'reproduction_max_distance_error_A':float(reproduction[:,0].max()),
            'reproduction_max_angle_error_deg':float(reproduction[:,1].max()),
            'native_vs_recompressed_distance_max_A':float(conversion[:,0].max()),
            'native_vs_recompressed_angle_max_deg':float(conversion[:,1].max()),
            'coordinate_semantics':'Motion/water analysis uses native XTC. Archived endpoint is reproduced from whole-solute XTC; its original classifications are unchanged.',
            'threshold_sensitivity_descriptive_only':sensitivity,'windows':windows,'samples':early,
            'torsions':[{'indices':t,'names':[str(top.atom(i)) for i in t],
                'EM_angle_deg':float(ref_dihedrals[i]),'max_absolute_change_deg':float(np.max(np.abs(delta[:,i]))),
                'late_mean_absolute_change_deg':float(np.mean(np.abs(delta[times>=50,i])))} for i,t in enumerate(torsions)],
            'water_descriptive':{'donor_fraction':dict(zip(labels,np.mean(donor_water>0,axis=0).tolist())),
                'acceptor_fraction':dict(zip(labels,np.mean(acceptor_water>0,axis=0).tolist())),
                'interpretation':'Directional water contacts; neither energetic competition nor causation established'}})
        for name,value in dict(time_ps=times,DA_A=distance,DHA_deg=angle,ligand_swap=ligand_swap,protein_swap=protein_swap,
                internal_rmsd_A=internal,global_rmsd_A=global_rmsd,dihedral_delta_deg=delta,
                donor_water=donor_water,acceptor_water=acceptor_water).items():arrays[arm+'_'+name]=value
    np.savez_compressed(output/'series.npz',**arrays)
    result={'schema':'snd1-saved-contact-dynamics/1','run_id':pair['run_id'],'rows':rows,
        'originals':originals,'series':pin(output/'series.npz'),'source':pin(__file__),
        'new_MD':0,'sample_role':'Retrospective analysis of two existing paired trajectories; correlated frames.',
        'original_endpoint_unchanged':True,'cost':{'wall_seconds':time.perf_counter()-started,'CPU_seconds':time.process_time()-cpu}}
    (output/'RESULT.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'result':str(output/'RESULT.json'),'cost':result['cost'],'arms':len(rows),'new_MD':0}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--mode',choices=['analysis','factorial','torsion-component'],default='analysis')
    parser.add_argument('--analysis-result',type=Path)
    args=parser.parse_args()
    if args.mode in ('factorial','torsion-component'):
        if args.analysis_result is None:parser.error('selected mode requires --analysis-result')
        method=factorial_motion if args.mode=='factorial' else torsion_component
        method(args.case_root,args.output,args.analysis_result)
    else:analyze(args.case_root,args.output)
