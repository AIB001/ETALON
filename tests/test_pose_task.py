"""Counterexamples for pose inputs and selective reuse (no scientific claims)."""
import copy
import pytest
from etalon.research.pose_task import compile_task, select_pose_branch
from etalon.research.scientific_input import identity


def task(tmp_path):
    sources = []
    for name in ('receptor', 'ligand', 'settings'):
        p = tmp_path / name
        p.write_text('fixture ' + name)
        sources.append(dict(id=name, path=str(p), **identity(p), locator='fixture', role='conditioning', scope='fixture'))
    values = {'receptor': str(tmp_path/'receptor'), 'ligand': str(tmp_path/'ligand'),
              'box': {'center': [0, 0, 0], 'size': [20, 20, 20]}, 'seed': 7, 'num_modes': 10, 'exhaustiveness': 8}
    return dict(schema='etalon-pose-task/1', task_id='fixture', case_id='fixture', question='fixture',
                split='development', method={'engine': 'fixture'},
                output_contract={'poses':'SDF','scores':'typed-with-direction','receipt':'request-and-file-identities','failure':'retained'},
                input=dict(schema='etalon-scientific-input/1', task_id='fixture', scope='fixture', subject={'id':'fixture'},
                           sources=sources, observations=[], interpretations=[], bindings=[
                           dict(id=k, target=k, use='constraint' if k in ('receptor','ligand','box') else 'selection',
                                value=v, basis_ids=[k if k in ('receptor','ligand') else 'settings'], rationale='fixture')
                           for k,v in values.items()]))


def test_execution_contract_rejects_hidden_labels_unbound_files_and_unknown_parameters(tmp_path):
    original = task(tmp_path)
    compiled = compile_task(original)
    assert 'reference' not in compiled and len(compiled['consumption']['consumed_targets']) == 6
    bad = copy.deepcopy(original)
    bad['input']['sources'][0]['role'] = 'evaluation_only'
    with pytest.raises(ValueError, match='non-conditioning'):
        compile_task(bad)
    bad = copy.deepcopy(original)
    bad['input']['bindings'][0]['value'] = str(tmp_path/'other')
    with pytest.raises(ValueError, match='conditioning source'):
        compile_task(bad)
    bad = copy.deepcopy(original)
    bad['input']['bindings'][2]['target'] = 'unknown'
    with pytest.raises(ValueError, match='unsupported parameter'):
        compile_task(bad)
    (tmp_path/'ligand').write_text('modified fixture')
    with pytest.raises(ValueError, match='bytes changed'):
        compile_task(original)


def test_related_or_conflicting_experience_does_not_promote():
    adopt = {'ref': {'fixture':1}, 'experience': {'outcome':'adopt'}}
    assert select_pose_branch({'matches':[], 'related':[adopt]})['selected'] == 'empirical'
    assert select_pose_branch({'matches':[adopt]})['selected'] == 'cnn'
    retain = {'ref': {'fixture':2}, 'experience': {'outcome':'retain'}}
    assert select_pose_branch({'matches':[adopt,retain]})['selected'] == 'empirical'
