"""Real test acquisition followed by a separate, prediction-gated counterfactual command."""
import argparse
import json
from pathlib import Path
import time
from etalon.active import CampaignStore
from etalon.boundary.prism_v1 import CLITransport
from etalon.research import ResearchJournal
from etalon.research.pool import PoolClient
from etalon.research.transfer import load_policy
from etalon.research.online import online_run,complete_counterfactual


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('run','complete-counterfactual'))
    for name in ('database','policy','manifest','input-freeze','root','output-dir','producer-source','python','request-root'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if not a.database.is_file():raise FileNotFoundError('existing unique R4 ledger required')
    store=CampaignStore(a.database);spec,_=store.configuration()
    if spec.budget!=1200 or spec.cost_unit!='wall_second':raise ValueError('wrong scientific ledger budget/unit')
    client=PoolClient(CLITransport(str(a.python.absolute()),str(a.producer_source.resolve()),str(a.request_root.resolve()),timeout=330))
    function=online_run if a.command=='run' else complete_counterfactual
    wall,cpu=time.perf_counter(),time.process_time()
    result=function(ResearchJournal(store),client,load_policy(a.policy),manifest_path=a.manifest,
        input_freeze_path=a.input_freeze,root=a.root,output_dir=a.output_dir)
    print(json.dumps({'schema':result['schema'],'budget':store.balance(),'output_dir':str(a.output_dir.absolute()),
        'consumer_whole_call_wall_second':time.perf_counter()-wall,'consumer_cpu_second':time.process_time()-cpu,
        'cost_scope':'wall includes waiting for producer; not an additional scientific charge'}))


if __name__=='__main__':main()
