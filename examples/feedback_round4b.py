"""Independently released R4b evaluation and root-reviewed same-H feedback."""
import argparse,json
from pathlib import Path
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.research import ResearchJournal
from etalon.research.baseline_feedback import evaluate_retained,attach_retained_feedback

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('evaluate','attach-feedback'))
    for name in ('database','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('policy','predictions','diagnostics','label-release','independent-release','oracle-script','evaluation','coordinator-audit','seen-gain-audit','support-replay','support-replay-driver'):
        p.add_argument('--'+name,type=Path)
    a=p.parse_args()
    if not a.database.is_file():raise FileNotFoundError('existing original ledger required')
    if a.output.exists():raise FileExistsError(a.output)
    journal=ResearchJournal(CampaignStore(a.database))
    if a.command=='evaluate':
        if not all((a.policy,a.predictions,a.diagnostics,a.label_release,a.independent_release,a.oracle_script)):
            raise ValueError('all original frozen inputs and independent releases required')
        result=evaluate_retained(journal,policy_path=a.policy,predictions_path=a.predictions,diagnostics_path=a.diagnostics,
            label_release_path=a.label_release,independent_release_path=a.independent_release,oracle_path=a.oracle_script)
    else:
        if not a.evaluation or not a.coordinator_audit:raise ValueError('original evaluation and coordinator review required')
        result=attach_retained_feedback(journal,evaluation_path=a.evaluation,coordinator_audit_path=a.coordinator_audit,
            seen_gain_audit_path=a.seen_gain_audit,support_replay_path=a.support_replay,
            support_replay_driver_path=a.support_replay_driver)
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(result)+'\n')
    print(json.dumps({'schema':result['schema'],'promotion':'none','new_producer_executions':0}))
