"""Record independent R4 feedback after frozen online predictions and released test evaluation."""
import argparse
from pathlib import Path
import json
import time
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.research import ResearchJournal
from etalon.research.transfer import load_policy
from etalon.research.feedback import attach_transfer_feedback


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('database','policy','rows','predictions','independent-release','calibration-report','seen-audit','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--assay-source-audit',type=Path)
    p.add_argument('--transfer-audit',type=Path,required=True)
    a=p.parse_args()
    if not a.database.is_file():raise FileNotFoundError('existing R4 ledger required')
    if a.output.exists():raise FileExistsError(a.output)
    wall,cpu=time.perf_counter(),time.process_time()
    result=attach_transfer_feedback(ResearchJournal(CampaignStore(a.database)),load_policy(a.policy),
        rows_path=a.rows,predictions_path=a.predictions,independent_release_path=a.independent_release,
        calibration_report_path=a.calibration_report,seen_audit_path=a.seen_audit,
        assay_source_audit_path=a.assay_source_audit,transfer_audit_path=a.transfer_audit)
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(result)+'\n')
    print(json.dumps({'state':result['research_state'],'promotion':'none','new_producer_executions':0,
        'wall_second':time.perf_counter()-wall,'cpu_second':time.process_time()-cpu}))
