"""Run/admit fixed-pool resources into the existing R4 CampaignStore; never creates a second budget."""
import argparse
import json
from pathlib import Path
import sys
import time

from etalon.active import CampaignStore
from etalon.active.schema import canonical, digest
from etalon.boundary.prism_v1 import CLITransport, file_identity
from etalon.research import ResearchJournal
from etalon.research.pool import PoolClient, execute
from etalon.research.transfer import load_policy, record_policy


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    run=sub.add_parser('execute')
    for name in ('request','root','producer-source','python','request-root'):
        run.add_argument('--'+name,type=Path,required=True)
    run.add_argument('--task-id',required=True)
    run.add_argument('--stage',required=True,choices=('seen','train','calibration','test'))
    run.add_argument('--resource-id',required=True)
    run.add_argument('--source-kind',default='new_execution',choices=('new_execution','existing_real_artifact','fixture'))
    run.add_argument('--read-only-admission',action='store_true')
    run.add_argument('--frozen-plan-id')
    freeze=sub.add_parser('freeze-test-plan')
    freeze.add_argument('--policy',type=Path,required=True)
    freeze.add_argument('--request-map',type=Path,required=True,help='semantic request SHA256 -> task ID')
    for cmd in (run,freeze):
        cmd.add_argument('--database',type=Path,required=True)
        cmd.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not a.database.is_file(): raise FileNotFoundError('use the existing unique R4 ledger: '+str(a.database))
    if a.output.exists(): raise FileExistsError(a.output)
    store=CampaignStore(a.database);spec,_=store.configuration()
    if spec.budget!=1200 or spec.cost_unit!='wall_second':raise ValueError('R4 requires the authorized shared1200 wall_second store')
    journal=ResearchJournal(store);wall,cpu=time.perf_counter(),time.process_time()
    if a.command=='execute':
        client=PoolClient(CLITransport(str(a.python.absolute()),str(a.producer_source.resolve()),
            str(a.request_root.resolve()),timeout=330))
        result=execute(journal,client,json.loads(a.request.read_text()),root=a.root,task_id=a.task_id,
            stage=a.stage,resource_id=a.resource_id,source_kind=a.source_kind,
            allow_dispatch=not a.read_only_admission,frozen_plan_id=a.frozen_plan_id)
    else:
        policy=load_policy(a.policy);record_policy(journal,policy)
        requests=json.loads(a.request_map.read_text())
        if not requests or any(len(k)!=64 or policy['split']['tasks'][v]['role']!='test' for k,v in requests.items()):
            raise ValueError('test plan must bind exact semantic hashes to frozen test task IDs')
        body={'schema':'etalon-pool-test-plan/1','policy_sha256':policy['policy_sha256'],
              'split_sha256':policy['split_sha256'],'requests':requests,
              'request_map':{'path':str(a.request_map.absolute()),**file_identity(a.request_map)}}
        identifier='poolplan-'+digest(body)
        with store.connection(write=True) as db:journal._put(db,identifier,body)
        result={**body,'id':identifier}
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(result)+'\n')
    print(json.dumps({'output':str(a.output.absolute()),**file_identity(a.output),'budget':store.balance(),
        'python':sys.executable,'script':file_identity(__file__),
        'consumer_whole_call_wall_second':time.perf_counter()-wall,'consumer_cpu_second':time.process_time()-cpu,
        'cost_scope':'consumer call wall INCLUDES producer waiting; not an additional scientific charge'}))


if __name__=='__main__':main()
