"""Execute one new PRISM pose/scoring request through a shared staged campaign."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from etalon.active import CampaignSpec,CampaignStore,Endpoint
from etalon.active.schema import canonical
from etalon.boundary.prism_v1 import CLITransport
from etalon.research import ResearchJournal
from etalon.research.pose import PoseClient,execute
from etalon.research.controller import next_segment,record_segment


def interpreter_path(value):
    """Keep a venv launcher identity even when it is a symlink to base Python."""
    return str(Path(value).absolute())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('request','producer-source','request-root','root','database','output'):
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--python',default=sys.executable);p.add_argument('--task',required=True)
    p.add_argument('--stage',choices=('smoke','development','validation'),required=True)
    p.add_argument('--decision-sha256');p.add_argument('--budget',type=float)
    p.add_argument('--controller-id');p.add_argument('--segment-id')
    p.add_argument('--source-kind',choices=('fixture','existing_real_artifact'),required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    request=json.loads(a.request.read_text(encoding='utf-8'))
    if not a.database.exists():
        if a.budget is None or not 0<a.budget<=1200:raise ValueError('explicit shared campaign budget <=1200 required')
        store=CampaignStore(a.database)
        store.configure(CampaignSpec('unmeasured',a.budget,'wall_second','research-pose-evidence/1'),
            [Endpoint('unmeasured',a.task,'not_measured','count','research-pose-evidence/1',0,queryable=False,requires_handoff=False)])
    else:
        store=CampaignStore(a.database)
        if a.budget is not None and a.budget!=store.configuration()[0].budget:raise ValueError('cannot replace existing budget')
        if store.configuration()[0].budget>1200:raise ValueError('scientific campaign exceeds authorized total')
    # Preserve a venv's executable symlink: realpath selects the base interpreter.
    client=PoseClient(CLITransport(python=interpreter_path(a.python),cwd=str(a.producer_source.resolve()),
        request_root=str(a.request_root.resolve()),timeout=request['budget']['wall_seconds']+120))
    journal=ResearchJournal(store)
    if bool(a.controller_id)!=bool(a.segment_id):raise ValueError('controller and segment must be supplied together')
    if a.controller_id:
        step=next_segment(journal,a.controller_id,decision_sha256=a.decision_sha256)
        if step['status']!='query' or step['next']['id']!=a.segment_id:raise ValueError('controller does not authorize this next segment')
        from etalon.active.schema import digest
        if step['next']['request_sha256']!=digest(client.bind(request)['semantic']):raise ValueError('wrong controller request')
    receipt=execute(journal,client,request,root=a.root,task_id=a.task,stage=a.stage,
                    source_kind=a.source_kind,decision_sha256=a.decision_sha256)
    if a.controller_id:
        record_segment(journal,a.controller_id,a.segment_id,receipt,decision_sha256=a.decision_sha256)
    receipt.update(python=sys.executable,imported_consumer=sys.modules[execute.__module__].__file__)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(receipt)+'\n')
    print(json.dumps({'output':str(a.output),'status':receipt['producer_status'],'budget':receipt['budget']}))


if __name__=='__main__':main()
