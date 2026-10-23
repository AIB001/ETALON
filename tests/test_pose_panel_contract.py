"""A changed file cannot be rebound as a new request under a frozen panel."""
import importlib.util,json
from pathlib import Path
import pytest
from etalon.research.evidence_refs import file_identity


def test_panel_requires_the_original_conditioning_bytes(tmp_path):
    path=Path(__file__).parents[1]/'examples/research_posebusters.py'
    spec=importlib.util.spec_from_file_location('pose_pilot_contract',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    case=module.Case.__new__(module.Case);case.root=tmp_path;case.protocol={'engine':{'name':'fixture'}}
    folder=tmp_path/'inputs/case';folder.mkdir(parents=True)
    (folder/'receptor.pdb').write_text('fixture receptor')
    (folder/'ligand.sdf').write_text('fixture ligand')
    (folder/'settings.json').write_text(json.dumps({'box':{'center':[0,0,0],'size':[24,24,24]}}))
    row={'id':'case','smiles':'CC','split':'validation','inputs':{p.name:file_identity(p) for p in folder.iterdir()}}
    assert case.task(row,41)['case_id']=='case'
    (folder/'ligand.sdf').write_text('different conditioning input')
    with pytest.raises(ValueError,match='frozen conditioning input changed'):case.task(row,41)
