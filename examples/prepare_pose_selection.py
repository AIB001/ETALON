"""New-panel organizer; deterministic input separation, no outcome filtering."""
import argparse, difflib, hashlib, json, shutil
from pathlib import Path
from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem, rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold
from etalon.research.publication import _save
from etalon.research.evidence_refs import file_identity


def sequences(path):
    codes=dict(zip('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split(),'ARNDCQEGHILKMFPSTWYV'))
    chains={};seen=set()
    for line in path.read_text().splitlines():
        if not line.startswith('ATOM  ') or line[12:16].strip()!='CA':continue
        key=(line[21],line[22:27])
        if key in seen:continue
        seen.add(key);chains.setdefault(key[0],[]).append(codes.get(line[17:20],'X'))
    return [''.join(v) for v in chains.values() if len(v)>=30]


def prepare(old,root):
    if (root/'cases.json').exists():raise ValueError('panel exists; never silently resample')
    oldpanel=json.loads((old/'cases.json').read_text());raw=old/'organizer/raw'
    ids=(old/'downloads/final308.txt').read_text().split()
    generator=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048)
    fingerprints=[];seqs=[];scaffolds=set();oldids={r['id'] for r in oldpanel['cases']}
    for r in oldpanel['cases']:
        m=Chem.MolFromSmiles(r['smiles']);fingerprints.append(generator.GetFingerprint(m));scaffolds.add(r['scaffold'])
        seqs.extend(sequences(old/'inputs'/r['id']/'receptor.pdb'))
    rows=[];rejected=[]
    for caseid in sorted(ids,key=lambda x:hashlib.sha256(('etalon-selection-independent-v1:'+x).encode()).hexdigest()):
        if caseid in oldids:rejected.append({'id':caseid,'reason':'already seen'});continue
        matches=list(raw.rglob(caseid))
        if not matches:rejected.append({'id':caseid,'reason':'archive directory missing'});continue
        folder=matches[0];ligand=folder/(caseid+'_ligand.sdf');proteins=list(folder.glob('*protein*.pdb'))
        if not ligand.exists() or not proteins:rejected.append({'id':caseid,'reason':'missing inputs'});continue
        protein=proteins[0];m=Chem.SDMolSupplier(str(ligand),removeHs=True)[0]
        if m is None:rejected.append({'id':caseid,'reason':'unreadable molecule'});continue
        smi=Chem.MolToSmiles(m,isomericSmiles=True);scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=m)
        if '.' in smi or not 10<=m.GetNumHeavyAtoms()<=45 or any(a.GetAtomicNum() not in {1,6,7,8,9,15,16,17,35,53} for a in m.GetAtoms()):
            rejected.append({'id':caseid,'reason':'outside organic single-ligand 10-45-heavy-atom scope'});continue
        fp=generator.GetFingerprint(m);similarity=max(DataStructs.TanimotoSimilarity(fp,v) for v in fingerprints)
        if scaffold in scaffolds or similarity>=.5:
            rejected.append({'id':caseid,'reason':'duplicate scaffold or Morgan Tanimoto >= 0.5','max_similarity':similarity});continue
        current_sequences=sequences(protein)
        seqsim=max((difflib.SequenceMatcher(None,a,b,autojunk=False).ratio() for a in current_sequences for b in seqs),default=1.)
        if seqsim>=.5:
            rejected.append({'id':caseid,'reason':'chain sequence-string similarity >= 0.5 or missing chain','max_similarity':seqsim});continue
        # The organizer releases only the known-pocket centroid; gold atoms stay evaluator-only.
        center=[round(float(v),4) for v in m.GetConformer().GetPositions().mean(axis=0)]
        fresh=Chem.AddHs(Chem.MolFromSmiles(smi));cfg=AllChem.ETKDGv3();cfg.randomSeed=20261023
        if AllChem.EmbedMolecule(fresh,cfg)!=0:
            rejected.append({'id':caseid,'reason':'ETKDG failed before prediction'});continue
        mmff=AllChem.MMFFOptimizeMolecule(fresh,maxIters=200)
        conf=fresh.GetConformer();shift=center-conf.GetPositions().mean(axis=0)
        for i,pos in enumerate(conf.GetPositions()):conf.SetAtomPosition(i,tuple(float(v) for v in pos+shift))
        public=root/'inputs'/caseid;gold=root/'evaluator'/caseid;public.mkdir(parents=True);gold.mkdir(parents=True)
        shutil.copy2(protein,public/'receptor.pdb');shutil.copy2(ligand,gold/'reference.sdf')
        with Chem.SDWriter(str(public/'ligand.sdf')) as w:w.write(fresh)
        settings={'box':{'center':center,'size':[24.,24.,24.]},'seed':41,'num_modes':10,'exhaustiveness':8,
            'pocket_policy':'Known-pocket centroid only; no reference atomic coordinates or scores in execution inputs.',
            'preparation_mmff_status':mmff}
        _save(public/'settings.json',settings)
        rows.append({'id':caseid,'split':'validation','smiles':smi,'scaffold':scaffold,
            'max_prior_fingerprint_similarity':similarity,'max_prior_sequence_string_similarity':seqsim,
            'inputs':{p.name:file_identity(p) for p in public.iterdir()},'reference_identity':file_identity(gold/'reference.sdf')})
        fingerprints.append(fp);scaffolds.add(scaffold);seqs.extend(current_sequences)
        if len(rows)==24:break
    if len(rows)!=24:raise ValueError('insufficient eligible independent inputs: '+str(len(rows)))
    _save(root/'cases.json',{'schema':'etalon-posebusters-panel/1','cases':rows,'rejected':rejected,'seeds':[41,137],
        'development_panel':file_identity(old/'cases.json'),'archive':file_identity(old/'downloads/posebusters_paper_data.zip'),
        'members':file_identity(old/'downloads/final308.txt'),
        'selection':'fixed hash order; exclude all old IDs/scaffolds; Morgan radius2 Tanimoto <0.5; chain string similarity <0.5 against seen and new panel',
        'scope':'New project-unseen cases. Protein filtering heuristic is not remote-homology certification; model pretraining overlap unverified.'})
    print(json.dumps({'ids':[r['id'] for r in rows],'rejected':len(rejected)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--old',required=True);p.add_argument('--root',required=True);a=p.parse_args()
    prepare(Path(a.old),Path(a.root))
