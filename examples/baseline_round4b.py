"""Freeze/execute retained B, then separately acquire P diagnostics after predictions."""
import argparse,json,time
from pathlib import Path
from etalon.active import CampaignStore
from etalon.boundary.prism_v1 import CLITransport
from etalon.research import ResearchJournal
from etalon.research.pool import PoolClient
from etalon.research.online import _write_once
from etalon.research.retained_baseline import freeze_retained_baseline,run_retained_baseline

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('freeze','run','complete-counterfactual'))
    for name in ('database','config','manifest','input-freeze','root','output-dir'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('producer-source','python','request-root'):p.add_argument('--'+name,type=Path)
    a=p.parse_args()
    if not a.database.is_file():raise FileNotFoundError('existing original R4 ledger required')
    store=CampaignStore(a.database);spec,_=store.configuration()
    if spec.budget!=1200 or spec.cost_unit!='wall_second':raise ValueError('wrong shared ledger budget/unit')
    journal=ResearchJournal(store);a.output_dir.mkdir(parents=True,exist_ok=True)
    wall=time.perf_counter()
    if a.command=='freeze':
        policy,plan,_,_=freeze_retained_baseline(journal,config_path=a.config,manifest_path=a.manifest,input_freeze_path=a.input_freeze)
        _write_once(a.output_dir/'policy-frozen.json',policy)
        _write_once(a.output_dir/'plan-frozen.json',{'policy_sha256':policy['policy_sha256'],'plan_id':plan})
        result={'policy_sha256':policy['policy_sha256'],'plan_id':plan,'new_scientific_executions':0}
    else:
        if not all((a.python,a.producer_source,a.request_root)):raise ValueError('actual producer interpreter/source/request-root required')
        client=PoolClient(CLITransport(str(a.python.absolute()),str(a.producer_source.resolve()),str(a.request_root.resolve()),timeout=330))
        result=run_retained_baseline(journal,client,config_path=a.config,manifest_path=a.manifest,
            input_freeze_path=a.input_freeze,root=a.root,output_dir=a.output_dir,
            complete_counterfactual=a.command=='complete-counterfactual')
    print(json.dumps({'command':a.command,'policy_sha256':result['policy_sha256'],'budget':store.balance(),
        'consumer_wall_including_producer_wait':time.perf_counter()-wall,'output_dir':str(a.output_dir.absolute()),
        'scope':'retained baseline; precompiled preparation attribution and actual online scores are separate'}))
