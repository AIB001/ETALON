"""Dataset organizer. Does not run methods or select cases by prediction quality."""
import difflib, hashlib, json, os, shutil, sys, zipfile
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors
from rdkit.Chem.Scaffolds import MurckoScaffold
from etalon.research.publication import _save
from etalon.research.evidence_refs import file_identity

root=Path(sys.argv[1]); archive=root/'downloads/posebusters_paper_data.zip'
assert hashlib.md5(archive.read_bytes()).hexdigest()=='f004ac7c4e68317b5348497d2bb6bee6'
raw=root/'organizer/raw'; raw.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive) as z:
    for n in z.namelist():
        target=(raw/n).resolve()
        if not target.is_relative_to(raw.resolve()): raise ValueError('unsafe archive member')
    z.extractall(raw)
print('archive files',list(raw.rglob('*.sdf'))[:4],flush=True)
ids=(root/'downloads/final308.txt').read_text().split(); assert len(ids)==308
rows=[]; rejected=[]; seqs=[]; scaffolds=set()
for caseid in sorted(ids,key=lambda x:hashlib.sha256(('etalon-pb24-v1:'+x).encode()).hexdigest()):
    matches=list(raw.rglob(caseid))
    if not matches: rejected.append({'id':caseid,'reason':'archive directory missing'}); continue
    folder=matches[0]
    print(caseid,[p.name for p in folder.iterdir()],flush=True) if not rows else None
    ligands=list(folder.glob('*ligand*.sdf')); proteins=list(folder.glob('*protein*.pdb'))
    if not ligands or not proteins: rejected.append({'id':caseid,'reason':'input missing'});continue
    ligand=folder/(caseid+'_ligand.sdf'); protein=proteins[0]
    m=Chem.SDMolSupplier(str(ligand),removeHs=True)[0]
    if m is None: rejected.append({'id':caseid,'reason':'unreadable graph'});continue
    smi=Chem.MolToSmiles(m,isomericSmiles=True); scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=m)
    allowed={1,6,7,8,9,15,16,17,35,53}
    if '.' in smi or not 10<=m.GetNumHeavyAtoms()<=45 or any(a.GetAtomicNum() not in allowed for a in m.GetAtoms()):
        rejected.append({'id':caseid,'reason':'predeclared noncovalent organic 10-45-heavy-atom scope'});continue
    residues={}
    for line in protein.read_text().splitlines():
        if line.startswith('ATOM  ') and line[12:16].strip()=='CA':residues[(line[21:22],line[22:27])]=line[17:20]
    seq=''.join(residues.values())
    if not seq or scaffold in scaffolds or any(difflib.SequenceMatcher(None,seq,s,autojunk=False).ratio()>=0.5 for s in seqs):
        rejected.append({'id':caseid,'reason':'duplicate scaffold or >=0.5 sequence-string similarity'});continue
    public=root/'inputs'/caseid; public.mkdir(parents=True,exist_ok=True)
    gold=root/'evaluator'/caseid;gold.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ligand,gold/'reference.sdf');shutil.copy2(protein,public/'receptor.pdb')
    center=[round(float(v),4) for v in m.GetConformer().GetPositions().mean(axis=0)]
    fresh=Chem.AddHs(Chem.MolFromSmiles(smi)); cfg=AllChem.ETKDGv3();cfg.randomSeed=20261023
    if AllChem.EmbedMolecule(fresh,cfg)!=0:rejected.append({'id':caseid,'reason':'ETKDG failed before docking'});continue
    AllChem.MMFFOptimizeMolecule(fresh,maxIters=200)
    conf=fresh.GetConformer(); shift=center-conf.GetPositions().mean(axis=0)
    for i,pos in enumerate(conf.GetPositions()):conf.SetAtomPosition(i,tuple(float(x) for x in pos+shift))
    with Chem.SDWriter(str(public/'ligand.sdf')) as w:w.write(fresh)
    # Released task information; atomic reference coordinates never enter a task.
    settings={'box':{'center':center,'size':[24.,24.,24.]},'seed':41,'num_modes':10,'exhaustiveness':8,
              'pocket_policy':'Known-pocket redocking. Organizer releases reference-derived centroid only; no pose constraints.',
              'reference_sha256':file_identity(gold/'reference.sdf')['sha256']}
    _save(public/'settings.json',settings)
    row={'id':caseid,'split':'development' if len(rows)<8 else 'validation', 'smiles':smi,'scaffold':scaffold,
         'sequence_sha256':hashlib.sha256(seq.encode()).hexdigest(),'rotatable_bonds':rdMolDescriptors.CalcNumRotatableBonds(m),
         'inputs':{p.name:file_identity(p) for p in public.iterdir()},'reference_identity':file_identity(gold/'reference.sdf')}
    rows.append(row);scaffolds.add(scaffold);seqs.append(seq)
    if len(rows)==24:break
if len(rows)!=24: raise RuntimeError('not enough eligible new cases: '+str(len(rows)))
_save(root/'cases.json',{'schema':'etalon-posebusters-panel/1','cases':rows,'rejected':rejected,
    'archive':file_identity(archive),'members':file_identity(root/'downloads/final308.txt'),
    'selection':'Deterministic hash order; molecular input compatibility; distinct scaffolds and sequence heuristic. No prediction or truth score used.',
    'scope':'24 new local research tasks, not proof of absence from model pretraining or remote structural homology.',
    'seeds':[41,137], 'reference_visibility':'evaluator only; known-pocket centroid explicitly released'})
print(json.dumps({'cases':[(r['id'],r['split']) for r in rows],'rejected':len(rejected)}),flush=True)
