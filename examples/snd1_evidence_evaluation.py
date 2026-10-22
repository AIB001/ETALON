"""Independent receptor fit and regional SND1 evaluation, separate from generation."""
from pathlib import Path
import numpy as np
from rdkit import Chem
from rdkit.Numerics import rdAlignment
from etalon.research.scientific_input import compile_input
from snd1_evidence_workflow import TARGETS, read, metrics, old


def receptor_fit(fixed, moving):
    # Low-level RDKit returns SSD, unlike rdMolAlign's RMSD interface.
    ssd,transform=rdAlignment.GetAlignmentTransform(np.array(fixed),np.array(moving))
    return float(np.sqrt(ssd/len(fixed))),transform


def evaluate(bundle_path, input_dir, output, reference_pdb, reference_ccd, reference_smiles):
    parameters=compile_input(read(bundle_path),allowed_targets=TARGETS)['parameters']
    input_dir,output=Path(input_dir),Path(output)
    score=old.evaluate(input_dir,output,reference_pdb,reference_ccd,reference_smiles)
    receptor=read(input_dir/'receptor.json')
    fixed={tuple(a['key'][2:]):a['xyz'] for a in receptor['atoms'] if a['key'][-1]=='CA'}
    moving={tuple(a['key'][2:]):a['xyz'] for a in old.pdb_atoms(reference_pdb) if a['protein'] and a['key'][1]=='A' and a['key'][-1]=='CA'}
    keys=sorted(set(fixed)&set(moving))
    rms,transform=receptor_fit([fixed[k] for k in keys],[moving[k] for k in keys])
    if abs(rms-score['protein_alignment']['rmsd_A'])>1e-6:raise ValueError('independent receptor fits disagree')
    answer,_=old.ligand(reference_pdb,reference_ccd,reference_smiles)
    truth=answer.GetConformer().GetPositions()@transform[:3,:3].T+transform[:3,3]
    raw=read(output/'result.json');anchors=parameters['mobility']['fixed_indices']
    for row,execution in zip(score['rows'],raw['rows']):
        row.update(non_anchor_rmsd_A=None,anchor_rmsd_A=None)
        if not execution['pose']:continue
        mol=Chem.RemoveHs(Chem.SDMolSupplier(str(output/execution['pose']),removeHs=False)[0])
        matches=answer.GetSubstructMatches(mol,uniquify=False,maxMatches=2048,useChirality=True)
        if not matches or len(matches)==2048:raise ValueError('truncated atom mapping')
        delta=min([mol.GetConformer().GetPositions()-truth[list(m)] for m in matches],key=lambda d:np.sum(d*d))
        full=float(np.sqrt(np.mean(np.sum(delta**2,axis=1))))
        if abs(full-row['rmsd_A'])>1e-6:raise ValueError('independent ligand RMSD disagrees')
        free=[i for i in range(mol.GetNumAtoms()) if i not in anchors]
        row.update(non_anchor_rmsd_A=float(np.sqrt(np.mean(np.sum(delta[free]**2,axis=1)))),
            anchor_rmsd_A=float(np.sqrt(np.mean(np.sum(delta[anchors]**2,axis=1)))),**metrics(mol,parameters))
    values=[r['non_anchor_rmsd_A'] for r in score['rows'] if r['non_anchor_rmsd_A'] is not None]
    score.update(median_non_anchor_rmsd_A=float(np.median(values)) if values else None,
        anchor_indices=anchors,non_anchor_count=answer.GetNumAtoms()-len(anchors),
        independent_rmsd_checked=True,template_contacts_are_optimization_diagnostics=True,
        evaluation_role='retrospective previously seen structures; not an independent holdout')
    return score
