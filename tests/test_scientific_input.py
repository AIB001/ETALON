import copy
import pytest
from etalon.research.scientific_input import SCHEMA, identity, compile_input, consumption_receipt


def bundle(tmp_path):
    p=tmp_path/'source.txt';p.write_text('fixture source')
    return {'schema':SCHEMA,'task_id':'fixture','scope':'fixture','subject':{'id':'A'},
        'sources':[{'id':'raw','role':'conditioning','scope':'fixture','path':str(p),**identity(p),'locator':'line 1'}],
        'observations':[{'id':'obs','role':'conditioning','scope':'fixture','basis_ids':['raw'],'kind':'structure','data':{}}],
        'interpretations':[{'id':'hypothesis','role':'conditioning','scope':'fixture','basis_ids':['obs'],'assumption':'fixture hypothesis'}],
        'bindings':[{'id':'b','target':'constraint','use':'constraint','basis_ids':['hypothesis'],'value':3,'rationale':'fixture'}]}


def test_indirect_answer_leak_and_context_refused(tmp_path):
    b=bundle(tmp_path)
    for role in ['evaluation_only','context']:
        b['sources'][0]['role']=role
        with pytest.raises(ValueError,match='non-conditioning'):compile_input(b,allowed_targets=['constraint'])


def test_changed_source_and_missing_lineage_refused(tmp_path):
    b=bundle(tmp_path);p=tmp_path/'source.txt';p.write_text('modified')
    with pytest.raises(ValueError,match='source bytes'):compile_input(b,allowed_targets=['constraint'])
    b['sources'][0].update(identity(p));b['observations'][0]['basis_ids']=['missing']
    with pytest.raises(ValueError,match='lineage'):compile_input(b,allowed_targets=['constraint'])


def test_cycles_and_unsupported_consumption_refused(tmp_path):
    b=bundle(tmp_path)
    with pytest.raises(ValueError,match='unsupported parameter'):compile_input(b,allowed_targets=[])
    b['observations'][0]['basis_ids']=['hypothesis']
    with pytest.raises(ValueError,match='cyclic'):compile_input(b,allowed_targets=['constraint'])


def test_every_parameter_requires_a_consumer_or_explicit_defer(tmp_path):
    c=compile_input(bundle(tmp_path),allowed_targets=['constraint'])
    with pytest.raises(ValueError,match='consumption'):consumption_receipt(c,consumed_targets=[])
    assert consumption_receipt(c,consumed_targets=[],deferred={'constraint':'baseline only uses post hoc assessment'})['deferred']
    assert consumption_receipt(c,consumed_targets=['constraint'])['consumed_targets']==['constraint']
    with pytest.raises(ValueError):consumption_receipt(c,consumed_targets=['constraint'],deferred={'constraint':'both'})


def test_unknown_activity_cannot_be_converted_to_zero(tmp_path):
    b=bundle(tmp_path);o=b['observations'][0];o.update(kind='activity',data={'molecule_id':'A','target':'T','endpoint':'Kd','assay':'MST',
        'unit':'uM','relation':'=','status':'unknown','value':0})
    with pytest.raises(ValueError,match='numeric label'):compile_input(b,allowed_targets=['constraint'])
    o['data']['value']=None;compile_input(b,allowed_targets=['constraint'])


def test_duplicate_targets_rejected_and_compilation_does_not_mutate(tmp_path):
    b=bundle(tmp_path);before=copy.deepcopy(b);compile_input(b,allowed_targets=['constraint']);assert b==before
    b['bindings'].append({**b['bindings'][0],'id':'different'})
    with pytest.raises(ValueError,match='duplicate'):compile_input(b,allowed_targets=['constraint'])


def test_evaluation_label_cannot_be_laundered_by_changing_binding_use(tmp_path):
    b=bundle(tmp_path);b['sources'][0]['role']='evaluation_only';b['bindings'][0]['use']='evaluation'
    # The consumer owns parameter semantics; a producer cannot relabel a force
    # parameter as an evaluator output to bypass visibility checks.
    with pytest.raises(ValueError,match='consumer contract'):
        compile_input(b,allowed_targets={'constraint':'constraint'})
