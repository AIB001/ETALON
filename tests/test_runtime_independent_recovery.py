import json
import subprocess
import sys
from pathlib import Path
import pytest
from etalon.runtime.reuse import execute_or_reuse,request_identity


def test_explicit_recovery_reads_output_without_second_execution(tmp_path):
    req=request_identity(operation='fixture',implementation='1',environment='test',inputs={'x':1},parameters={})
    output=tmp_path/'native.json';calls=[]
    def execute():
        calls.append('execute');output.write_text('{"value":7}')
        raise SystemExit('controller exit after native output')
    with pytest.raises(SystemExit):execute_or_reuse(tmp_path,req,execute)
    with pytest.raises(RuntimeError,match='unresolved'):execute_or_reuse(tmp_path,req,execute)
    result=execute_or_reuse(tmp_path,req,execute,recover=lambda:{'result':json.loads(output.read_text()),'artifacts':[output]})
    assert result['receipt']['status']=='complete'
    assert result['receipt']['execution_cost']['wall_seconds'] is None
    assert execute_or_reuse(tmp_path,req,execute)['reused'] and calls==['execute']


def test_runtime_and_scientific_algorithms_do_not_import_research():
    code='import etalon.runtime.native, etalon.runtime.reuse, etalon.rescore.poses, etalon.generate.route; import sys; assert not any(n == "etalon.research" or n.startswith("etalon.research.") for n in sys.modules)'
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)
