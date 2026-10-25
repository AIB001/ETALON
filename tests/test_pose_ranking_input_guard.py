import importlib.util
from pathlib import Path
import pytest


def test_target_residue_hydrogen_is_rejected_without_editing_source(tmp_path):
    script=Path(__file__).resolve().parents[1]/'examples/validate_pose_ranking.py'
    spec=importlib.util.spec_from_file_location('ranking_input_guard_test',script)
    driver=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    receptor=tmp_path/'receptor.pdb'
    for record in ('ATOM  ','HETATM'):
        line=record+' 4347 H161 Q2Q A 901      21.887  15.006  20.060  1.00 17.74      B    H  \n'
        receptor.write_text(line,encoding='ascii')
        before=receptor.read_bytes()
        with pytest.raises(ValueError,match='target component Q2Q atoms remain'):
            driver.guard_target_component_absence(receptor,target_component_id='Q2Q')
        assert receptor.read_bytes()==before
        assert driver.guard_target_component_absence(receptor,target_component_id='ABC')['target_named_atom_count']==0
