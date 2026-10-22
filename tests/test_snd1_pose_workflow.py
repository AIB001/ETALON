"""Contract fixtures for the optional molecular recipe, not research results."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip('prism.design.chem')
pytest.importorskip('rdkit')
from rdkit import Chem
from rdkit.Chem import AllChem

spec=importlib.util.spec_from_file_location('snd1_recipe_test',Path(__file__).parents[1]/'examples/snd1_pose_workflow.py')
recipe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


def test_graph_applicability_requires_both_coverages_and_neutral_connected_chemistry():
    template=Chem.AddHs(Chem.MolFromSmiles('COc1ccc(Cl)cc1S(=O)(=O)Nc1cccn2ncnc12'))
    assert AllChem.EmbedMolecule(template,randomSeed=7)>=0
    template=Chem.RemoveHs(template)
    same=Chem.MolFromSmiles(Chem.MolToSmiles(template))
    assert recipe.mapping(template,same)['applicable']
    aspirin=recipe.mapping(template,Chem.MolFromSmiles('CC(=O)Oc1ccccc1C(=O)O'))
    assert not aspirin['applicable']
    assert min(aspirin['coverage_target_template'])<.6
    # Exact geometric similarity does not permit silently ignoring a second component.
    disconnected=Chem.MolFromSmiles(Chem.MolToSmiles(template)+'.O')
    assert not recipe.mapping(template,disconnected)['applicable']


def test_changed_inputs_refused_before_creating_an_execution(tmp_path):
    inp=tmp_path/'input';inp.mkdir()
    (inp/'template.sdf').write_text('original fixture bytes')
    recipe.write(inp/'input.json',{'files':{'template.sdf':recipe.pin(inp/'template.sdf')}})
    (inp/'template.sdf').write_text('changed fixture bytes')
    with pytest.raises(ValueError,match='changed input'):
        recipe.generate(inp,tmp_path/'execution','fixed_core',[1])
    assert not (tmp_path/'execution').exists()


def test_interrupted_execution_never_silently_restarts(tmp_path):
    inp=tmp_path/'input';inp.mkdir();recipe.write(inp/'input.json',{'files':{}})
    out=tmp_path/'execution';out.mkdir()
    recipe.write(out/'request.json',{'input':recipe.pin(inp/'input.json'),'method':'free_align','seeds':[1],'runtime':recipe.runtime()})
    with pytest.raises(RuntimeError,match='pending'):
        recipe.generate(inp,out,'free_align',[1])
    assert not (out/'result.json').exists()


def test_later_negative_experience_supersedes_prior_adoption():
    path=Path(__file__).parents[1]/'examples/research_snd1_improvement.py'
    spec=importlib.util.spec_from_file_location('snd1_driver_test',path)
    driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    old={'source_id':'fixture','record_id':'old','record_digest':'a'}
    new={'source_id':'fixture','record_id':'new','record_digest':'b'}
    positive={'ref':old,'experience':{'outcome':'adopt','supersedes':[]}}
    negative={'ref':new,'experience':{'outcome':'retain','supersedes':[old]}}
    assert driver.choose_from_experience({'matches':[positive]},True)=='fixed_core'
    assert driver.choose_from_experience({'matches':[positive,negative]},True)=='free_align'
    assert driver.choose_from_experience({'matches':[positive]},False)=='free_align'
    # Conflicting unsuperseded advice must not be silently ordered by retrieval rank.
    negative['experience']['supersedes']=[]
    with pytest.raises(ValueError,match='conflicting'):
        driver.choose_from_experience({'matches':[positive,negative]},True)
