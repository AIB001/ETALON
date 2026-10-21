"""A venv launcher must survive path normalization; resolving changes its imports."""
import importlib.util
from pathlib import Path
import pytest


def test_preserve_virtual_environment_python_symlink(tmp_path):
    path=Path(__file__).parents[1]/'examples/pose_round3.py'
    spec=importlib.util.spec_from_file_location('pose_driver',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    base=tmp_path/'base-python';base.write_text('fixture executable')
    launcher=tmp_path/'venv'/'bin'/'python';launcher.parent.mkdir(parents=True)
    try:launcher.symlink_to(base)
    except OSError as error:pytest.skip('platform does not permit test symlinks: '+str(error))
    assert Path(module.interpreter_path(launcher))==launcher
    assert Path(module.interpreter_path(launcher))!=launcher.resolve()
