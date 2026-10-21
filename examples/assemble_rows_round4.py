"""Admit real pool receipts and coordinator-released losses to the shared research journal."""
import argparse
import json
from pathlib import Path
import time
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.boundary.prism_v1 import file_identity
from etalon.research import ResearchJournal
from etalon.research.rows import assemble_rows
from etalon.research.transfer import load_policy


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('database','config','input-freeze','label-release','oracle-script','admission-map','output','report'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--role',required=True,choices=('train','calibration','test'))
    p.add_argument('--policy',type=Path);p.add_argument('--test-release',type=Path)
    a=p.parse_args()
    if not a.database.is_file():raise FileNotFoundError('existing R4 ledger required')
    if a.output.exists() or a.report.exists():raise FileExistsError('use new immutable row/report paths')
    wall,cpu=time.perf_counter(),time.process_time()
    rows,report=assemble_rows(ResearchJournal(CampaignStore(a.database)),config_path=a.config,
        input_freeze_path=a.input_freeze,label_release_path=a.label_release,oracle_path=a.oracle_script,
        admission_map=json.loads(a.admission_map.read_text()),role=a.role,
        policy=load_policy(a.policy) if a.policy else None,
        test_release=json.loads(a.test_release.read_text()) if a.test_release else None)
    for path,value in [(a.output,rows),(a.report,report)]:
        with path.open('x',encoding='utf-8') as f:f.write(canonical(value)+'\n')
    print(json.dumps({'tasks':len(rows),'output':str(a.output.absolute()),**file_identity(a.output),
        'wall_second':time.perf_counter()-wall,'cpu_second':time.process_time()-cpu,'new_scientific_executions':0}))


if __name__=='__main__':main()
