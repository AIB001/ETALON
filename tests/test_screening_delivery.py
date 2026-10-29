"""Real small CPU gates and labelled pose fixtures, not an affinity validation."""
import csv
import gzip
from io import StringIO
import json

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from etalon.boundary.screen import Screen
from etalon.campaign.design import compose, component
from etalon.screening_delivery import export_screen_delivery, rmsd_acceptance, _sdf


def test_archive_read_export_retains_rejected_smiles_without_writer_directories(tmp_path):
    screen = Screen(tmp_path / 'work')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(compose('delivery', [{'id':'window','title':'test window',
        'criteria':[component('mw', 'chemistry.rdkit_property_range_gate@0.1.0',
                             settings={'mw_min':60.0, 'mw_max':100.0})]}])))
    library = tmp_path / 'input.csv'
    library.write_text('id,smiles\nethanol,CCO\nbenzene,c1ccccc1\n')
    state = screen.run(screen.plan(config, library), run_id='cpu')
    assert state.status == 'SUCCEEDED'
    # Archives often omit empty writer directories. Reads must not recreate them.
    (screen.workspace / '.staging').rmdir()
    # lock files may remain after a writer exits; leave .locks intact if nonempty.
    if not list((screen.workspace / '.locks').iterdir()):
        (screen.workspace / '.locks').rmdir()
    report = export_screen_delivery(screen.workspace, 'cpu', tmp_path / 'delivery')
    assert not (screen.workspace / '.staging').exists()
    assert report['scientific_calls'] == 0
    records = []
    for p in (tmp_path / 'delivery/gates').glob('*decisions.csv.gz'):
        with gzip.open(p, 'rt', encoding='utf-8') as f:
            records.extend(csv.DictReader(f))
    assert any(r['smiles']=='CCO' and r['outcome']=='REJECT' for r in records)
    gate = next(s for s in report['stages'] if s['stage_id']=='mw')
    assert gate['parent_input']==2 and gate['parent_output']==1
    assert gate['net_parent_removal']==1
    assert export_screen_delivery(screen.workspace,'cpu',tmp_path/'delivery')['reused']
    assert all(s.attempts == 1 for s in screen.state('cpu').stages)


def test_strict_rmsd_and_sdf_coordinates_are_not_reinterpreted(tmp_path):
    assert [rmsd_acceptance(v,'OK') for v in [1.999,2.0,3.0]] == ['accept','reject','reject']
    assert rmsd_acceptance(None,'UNSCORABLE')=='unknown'
    assert rmsd_acceptance(float('nan'),'OK')=='unknown'
    mol = Chem.AddHs(Chem.MolFromSmiles('c1ccccc1'))
    AllChem.EmbedMolecule(mol, randomSeed=41)
    output = StringIO()
    _sdf(output, Chem.MolToMolBlock(mol), 'fixture', role='redock', rmsd=2.0)
    path = tmp_path/'pose.sdf';path.write_text(output.getvalue())
    written = Chem.SDMolSupplier(str(path),removeHs=False)[0]
    original = Chem.MolFromMolBlock(Chem.MolToMolBlock(mol),removeHs=False)
    assert (written.GetConformer().GetPositions()==original.GetConformer().GetPositions()).all()
    assert written.GetProp('rmsd')=='2.0'


def test_new_screen_preserves_quality_cap_and_budget_decisions(tmp_path):
    """Actual CPU execution, with different reasons for three discarded populations."""
    from etalon.screening import plan_screen

    library = tmp_path/'input.csv'
    library.write_text('id,smiles\na,c1ccccc1\nb,Cc1ccccc1\nc,CCc1ccccc1\nd,c1ccncc1\ne,C1CCCCC1\nf,CCO\n')
    selected = []
    for version in ('0.1.0','0.2.0'):
        config = tmp_path/(version+'.json')
        config.write_text(json.dumps(compose('new-population', [
            {'id':'quality','title':'quality','criteria':[component('mw','chemistry.rdkit_property_range_gate@0.1.0',settings={'mw_min':60})]},
            {'id':'budget','title':'budget','criteria':[component('select','select.native_scaffold_round_robin@'+version,
                settings={'target_count':2,'max_per_scaffold':1,'seed':31})]}])))
        screen=Screen(tmp_path/('ws-'+version))
        plan=screen.plan(config,library)
        assert plan.as_dict()['execution_contract']['budget_selection'][0]['target_count']==2
        run=screen.run(plan,run_id='fresh')
        assert run.status=='SUCCEEDED'
        report=export_screen_delivery(screen.workspace,'fresh',tmp_path/('delivery-'+version))
        gate=next(s for s in report['stages'] if s['stage_id']=='mw')
        assert (gate['parent_input'],gate['parent_output'])==(6,5)
        path=next((tmp_path/('delivery-'+version)/'gates').glob('*select-selection.csv.gz'))
        with gzip.open(path,'rt') as f:rows=list(csv.DictReader(f))
        selected.append({r['entity_id'] for r in rows if r['outcome']=='SELECTED'})
        assert len(rows)==5 and all(r['smiles'] for r in rows)
        if version=='0.2.0':
            from collections import Counter
            assert Counter(r['reason'] for r in rows)=={
                'SELECTED_SCAFFOLD_ROUND_ROBIN':2,'NOT_SELECTED_SCAFFOLD_CAP':2,'NOT_SELECTED_BUDGET':1}
        with pytest.raises(ValueError,match='redock delivery'):
            plan_screen(config,library,screen.workspace,delivery={'require_redock':True})
    assert selected[0]==selected[1]


def test_declared_delivery_distinguishes_computing_redock_from_enforcing_it():
    from etalon.screening_delivery import delivery_options

    pipeline={'stages':[
        {'id':'repeat','plugin':'docking.redock_consistency@0.1.0','config':{'keep_poses':True}},
        {'id':'gate','plugin':'derived.numeric_evidence_gate@0.1.0','config':{
            'metric_id':'dock_redock_rmsd','maximum':2.,'maximum_exclusive':True,'on_unscorable':'reject'}}]}
    assert delivery_options({'require_redock':True},pipeline)['rmsd_threshold']==2
    for changes in ({'maximum':3.},{'maximum_exclusive':False},{'on_unscorable':'accept'}):
        original=dict(pipeline['stages'][1]['config'])
        pipeline['stages'][1]['config'].update(changes)
        with pytest.raises(ValueError,match='redock delivery'):
            delivery_options({'require_redock':True},pipeline)
        pipeline['stages'][1]['config']=original


def test_worker_completes_delivery_and_readback_does_not_repeat_computation(tmp_path):
    """Real worker body on Windows too; POSIX process detachment is tested separately."""
    import os
    from etalon.screening import plan_screen, _journal, _worker, screen_status
    config=tmp_path/'config.json'
    config.write_text(json.dumps(compose('delivery-worker',[{'id':'window','title':'window',
        'criteria':[component('mw','chemistry.rdkit_property_range_gate@0.1.0',settings={'mw_min':60})]}])))
    library=tmp_path/'library.csv';library.write_text('id,smiles\na,CCO\nb,c1ccccc1\n')
    workspace=tmp_path/'work'
    plan=plan_screen(config,library,workspace,delivery={})
    assert plan['plan_id']!=plan_screen(config,library,workspace)['plan_id']
    request={**plan['request'],'workers':1,'devices':[]}
    with _journal(workspace,create=True) as db:
        db.execute('INSERT INTO screen_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('fresh',plan['plan_id'],json.dumps(request),'queued',os.getpid(),'now','now',None,None,str(workspace/'job.log')))
        db.commit()
    assert _worker(str(workspace),'fresh')==0
    job=screen_status(workspace,'fresh')
    assert job['state']=='succeeded' and job['result']['delivery']['state']=='complete'
    assert _worker(str(workspace),'fresh')==1
    screen=Screen.open_existing(workspace)
    assert all(s.attempts==1 for s in screen.state('fresh').stages)
    assert screen_status(workspace,'fresh')['result']==job['result']


def test_delivery_failure_is_not_a_scientific_retry(tmp_path,monkeypatch):
    import os
    from etalon.screening import plan_screen, _journal, _worker, screen_status
    config=tmp_path/'config.json'
    config.write_text(json.dumps(compose('one',[{'id':'measure','title':'measure',
        'criteria':[component('props','features.rdkit_properties@0.1.0')]}])))
    library=tmp_path/'library.csv';library.write_text('id,smiles\na,CCO\n')
    workspace=tmp_path/'work'
    plan=plan_screen(config,library,workspace,delivery={})
    request={**plan['request'],'workers':1,'devices':[]}
    with _journal(workspace,create=True) as db:
        db.execute('INSERT INTO screen_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('fresh',plan['plan_id'],json.dumps(request),'queued',os.getpid(),'now','now',None,None,str(workspace/'job.log')))
        db.commit()
    def unavailable(*args,**kwargs):
        raise OSError('labelled delivery I/O failure')
    monkeypatch.setattr('etalon.screening_delivery.export_screen_delivery',unavailable)
    assert _worker(str(workspace),'fresh')==0
    job=screen_status(workspace,'fresh')
    assert job['state']=='succeeded' and job['result']['delivery']['state']=='failed'
    assert job['result']['outcome']=='committed'
    assert _worker(str(workspace),'fresh')==1


def test_interrupted_materialization_resumes_without_science(tmp_path,monkeypatch):
    import etalon.screening_delivery as delivery
    config=tmp_path/'config.json'
    config.write_text(json.dumps(compose('measure',[{'id':'measure','title':'measure',
        'criteria':[component('props','features.rdkit_properties@0.1.0')]}])))
    library=tmp_path/'input.csv';library.write_text('id,smiles\na,CCO\n')
    screen=Screen(tmp_path/'screen')
    screen.run(screen.plan(config,library),run_id='new')
    save=delivery._save
    def interrupt_receipt(path,*args,**kwargs):
        if path.name=='delivery.json':
            raise OSError('labelled interrupted output publication')
        return save(path,*args,**kwargs)
    monkeypatch.setattr(delivery,'_save',interrupt_receipt)
    with pytest.raises(OSError,match='interrupted output'):
        export_screen_delivery(screen.workspace,'new',tmp_path/'out')
    monkeypatch.setattr(delivery,'_save',save)
    report=export_screen_delivery(screen.workspace,'new',tmp_path/'out')
    assert report['scientific_calls']==0
    assert all(s.attempts==1 for s in screen.state('new').stages)
