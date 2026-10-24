"""Recovery and unchanged-pool safeguards for the P2 example."""
import importlib.util, json
from pathlib import Path
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

example=Path(__file__).parents[1]/'examples/snd1_scale_screen.py'
if not example.exists():example=Path(__file__).with_name('snd1_scale_screen.py')
spec=importlib.util.spec_from_file_location('snd1_scale_screen',example)
screen=importlib.util.module_from_spec(spec);spec.loader.exec_module(screen)


def checked(valid=True):
    contract=screen.freeze_check_contract({'modules':[{'name':'Geometry','chosen_binary_test_output':['geometry','loaded']}]},
        version='fixture',implementation={'fixture':{'sha256':'a'*64,'bytes':1}})
    binding={'pool':{'sha256':'b'*64,'bytes':1},'receptor':{'sha256':'c'*64,'bytes':1},'reference':{'sha256':'d'*64,'bytes':1}}
    row={'pose_index':0,'physical_valid':valid,'checks':{'geometry':valid,'loaded':True},'error':None,
         'rmsd_A':1.25,'valid_and_RMSD_le_2A':valid}
    verifier={'rows':[row],'binding':binding,**binding,'check_contract_digest':screen.digest(contract),
              'posebusters_version':'fixture','expected_pose_slots':1,'coverage_complete':True}
    return verifier,contract,binding

def sdf(p,delta=0.):
    m=Chem.AddHs(Chem.MolFromSmiles('CCO'));AllChem.EmbedMolecule(m,randomSeed=41)
    conf=m.GetConformer();v=conf.GetAtomPosition(0);conf.SetAtomPosition(0,(v.x+delta,v.y,v.z))
    with Chem.SDWriter(str(p)) as w:w.write(m)

def test_pool_coordinates_are_a_scientific_invariant(tmp_path):
    sdf(tmp_path/'a.sdf');sdf(tmp_path/'b.sdf')
    assert screen.same_pool(tmp_path/'a.sdf',tmp_path/'b.sdf')['pose_count']==1
    sdf(tmp_path/'b.sdf',.1)
    with pytest.raises(ValueError,match='changed heavy-atom'):screen.same_pool(tmp_path/'a.sdf',tmp_path/'b.sdf')

def test_immutable_source_refuses_overwrite(tmp_path):
    p=tmp_path/'request.json';screen.save(p,{'request':'A'});screen.save(p,{'request':'A'})
    with pytest.raises(ValueError,match='immutable'):screen.save(p,{'request':'B'})
    assert json.loads(p.read_text())=={'request':'A'}

def test_unknown_started_attempt_is_never_reexecuted(tmp_path):
    batch=object.__new__(screen.Batch);batch.root=tmp_path;batch.freeze_pin={'sha256':'f'*64,'bytes':4}
    compiled={'case_id':'case'};key=screen.execution_key(compiled,'empirical',batch.freeze_pin)
    folder=tmp_path/'executions'/key;folder.mkdir(parents=True);screen.save(folder/'started.json',{'pid':123})
    with pytest.raises(RuntimeError,match='ambiguous started request'):batch.native(compiled,'empirical',None,tmp_path/'out.sdf')
    assert not (folder/'receipt.json').exists()


@pytest.mark.parametrize('valid',[False,True])
def test_top1_preserves_measured_validity_under_frozen_requirements(valid):
    verifier,contract,binding=checked(valid)
    assert screen.top1_verification(verifier,{'ordered_pose_indices':[0]},1,check_contract=contract,binding=binding)=={
        'pose_index':0,'physical_valid':valid,'rmsd_A':1.25,'valid_and_RMSD_le_2A':valid}


@pytest.mark.parametrize('failure',[
    'missing_verifier','missing_rows','missing_selected_row','duplicate_rows','verifier_error',
    'row_error','incomplete_coverage','coverage_count_mismatch','missing_checks','nonboolean_check','missing_validity',
    'placeholder_false','unchecked_status','missing_ranking','single_true_check','wrong_pool','wrong_receptor','missing_binding',
])
def test_top1_missing_or_failed_checks_are_unknown(failure):
    verifier,contract,binding=checked();row=verifier['rows'][0];rank={'ordered_pose_indices':[0]};expected=1
    if failure=='missing_verifier':verifier=None
    elif failure=='missing_rows':verifier={}
    elif failure=='missing_selected_row':verifier['rows']=[]
    elif failure=='duplicate_rows':verifier['rows']=[row,row];expected=2
    elif failure=='verifier_error':verifier['error']='PoseBusters failed'
    elif failure=='row_error':row.update(error='check failed',physical_valid=False)
    elif failure=='incomplete_coverage':verifier['coverage_complete']=False
    elif failure=='coverage_count_mismatch':verifier.update(coverage_complete=True,expected_pose_slots=2)
    elif failure=='missing_checks':row['checks']={}
    elif failure=='nonboolean_check':row['checks']['missing']=None
    elif failure=='missing_validity':row.pop('physical_valid')
    elif failure=='placeholder_false':row['physical_valid']=False
    elif failure=='unchecked_status':row['status']='failed_check'
    elif failure=='missing_ranking':rank['ordered_pose_indices']=[]
    elif failure=='single_true_check':row['checks']={'geometry':True}
    elif failure=='wrong_pool':verifier['pool']={'sha256':'f'*64,'bytes':1}
    elif failure=='wrong_receptor':verifier['receptor']={'sha256':'f'*64,'bytes':1}
    elif failure=='missing_binding':verifier.pop('binding')
    result=screen.top1_verification(verifier,rank,expected,check_contract=contract,binding=binding)
    assert result['physical_valid'] is None
    assert result['rmsd_A'] is None
    assert result['valid_and_RMSD_le_2A'] is None


@pytest.mark.parametrize('valid',[False,True,None])
def test_legacy_result_cache_cannot_bypass_identity_validation_or_trigger_native(tmp_path,monkeypatch,valid):
    """A legacy cached result requires explicit versioned reassessment."""
    batch=object.__new__(screen.Batch);batch.root=tmp_path
    verifier,contract,binding=checked(valid)
    batch.freeze={'policy':{'selection':'empirical'},'check_contract':contract};batch.freeze_pin={'sha256':'f'*64,'bytes':4}
    def no_native(*args):raise AssertionError('cached result must never launch native execution')
    batch.cnn_lock=screen.threading.Lock();batch.native=no_native
    monkeypatch.setattr(screen,'make_task',lambda *args:{})
    compiled={'case_id':'cached-case','consumption':{'input_sha256':'x'},'method':{},'files':{'ligand':{}},'parameters':{'seed':41}}
    monkeypatch.setattr(screen,'compile_task',lambda task:compiled)
    folder=tmp_path/'outputs/cached-case';folder.mkdir(parents=True)
    (folder/'empirical.sdf').write_text('fixture')
    receptor_path=tmp_path/'receptor.pdb';receptor_path.write_text('fixture')
    screen.save(folder/'result.json',{'top1':{'empirical':{'physical_valid':valid}}})
    parent={'id':'parent','parent_key':'key','series_id':'series','split':'development','evidence_class':'U','full_inchikey':'ligand'}
    receptor={'id':'receptor','reference_inchikey':'different-ligand','receptor':str(receptor_path)}
    with pytest.raises(ValueError,match='result cache'):
        batch.case(parent,receptor,41)


def test_legacy_single_true_check_is_unknown_even_with_claimed_coverage():
    verifier,_,_=checked()
    assert screen.top1_verification(verifier,{'ordered_pose_indices':[0]},1)['physical_valid'] is None


@pytest.mark.parametrize('tamper',['top1','ranking','metadata','stage','verifier','binding','missing_stage','correctness'])
def test_result_cache_must_match_recomputed_observations_and_context(tmp_path,monkeypatch,tamper):
    batch=object.__new__(screen.Batch);batch.root=tmp_path;batch.freeze_pin={'sha256':'f'*64,'bytes':4}
    verifier,contract,_=checked(False);batch.freeze={'check_contract':contract}
    folder=tmp_path/'outputs/case';folder.mkdir(parents=True)
    output=folder/'empirical.sdf';output.write_text('synthetic pool')
    receptor_path=tmp_path/'receptor.pdb';receptor_path.write_text('synthetic receptor')
    compiled={'case_id':'case','consumption':{'input_sha256':'fixture'},'method':{'engine':'fixture'},
              'files':{'ligand':{'sha256':'a'*64,'bytes':1}},'parameters':{'seed':41}}
    binding=screen.result_binding(compiled=compiled,protocol=batch.freeze_pin,pool=screen.identity(output),
        receptor=screen.identity(receptor_path),reference=None,check_contract=contract)
    verifier.update(binding=binding,**{k:binding[k] for k in ('pool','receptor','reference')})
    verifier['rows'][0].update(rmsd_A=None,valid_and_RMSD_le_2A=None)
    screen.save(folder/'verifier.json',verifier)
    key=screen.execution_key(compiled,'empirical',batch.freeze_pin);execution=tmp_path/'executions'/key
    request={'request_id':key,'compiled':compiled,'stage':'empirical','freeze':batch.freeze_pin,'pool':None,'attempt_id':'a'}
    receipt={'request_id':key,'case_id':'case','stage':'empirical','attempt_id':'a','status':'completed',
             'native_resource':{'exit_status':0},'error':None,'execution_mode':'new_execution',
             'artifacts':{str(output):screen.identity(output)}}
    screen.save(execution/'request.json',request);screen.save(execution/'receipt.json',receipt)
    cnn_key=screen.execution_key(compiled,'cnn',batch.freeze_pin);cnn_execution=tmp_path/'executions'/cnn_key
    screen.save(cnn_execution/'request.json',{**request,'request_id':cnn_key,'stage':'cnn','pool':binding['pool']})
    screen.save(cnn_execution/'receipt.json',{**receipt,'request_id':cnn_key,'stage':'cnn','status':'failed','artifacts':{}})
    rank={'ordered_pose_indices':[0]}
    monkeypatch.setattr(screen,'ranking',lambda *args:rank)
    monkeypatch.setattr(screen,'pool_identity',lambda *args:[object()])
    parent={'id':'parent','parent_key':'key','series_id':'series','split':'development','evidence_class':'U'}
    receptor={'id':'rec','receptor':str(receptor_path)}
    result={'schema':'snd1-p2-result/2','binding':binding,'case_id':'case','parent_id':'parent','parent_key':'key','series_id':'series',
        'split':'development','evidence_class':'U','correctness_unknown':True,'receptor':'rec','seed':41,'stages':{'empirical':'completed','cnn':'failed'},
        'verifier':{'path':str(folder/'verifier.json'),**screen.identity(folder/'verifier.json')},
        'rankings':{'empirical':rank},'top1':{'empirical':screen.top1_verification(verifier,rank,1,check_contract=contract,binding=binding)},
        'integrity':screen.validate_verifier(verifier,expected_pose_count=1,check_contract=contract,expected_binding=binding)}
    screen.save(folder/'result.json',result)
    assert batch.cached_case(compiled,parent,receptor,None,folder)==result
    if tamper=='top1':result['top1']['empirical']['physical_valid']=True
    elif tamper=='ranking':result['rankings']={'empirical':{'ordered_pose_indices':[1]}}
    elif tamper=='metadata':result['series_id']='other'
    elif tamper=='stage':result['stages']['empirical']='failed'
    elif tamper=='verifier':result['verifier']['sha256']='0'*64
    elif tamper=='binding':result['binding']['method']={'engine':'other'}
    elif tamper=='missing_stage':result['stages'].pop('cnn')
    elif tamper=='correctness':result['correctness_unknown']=False
    screen.save(folder/'result.json',result,replace=True)
    with pytest.raises(ValueError,match='result cache'):
        batch.cached_case(compiled,parent,receptor,None,folder)
