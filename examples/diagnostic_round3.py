"""Run one producer-owned CPU diagnostic through ETALON's shared budget and research journal.

Use an actual execution-host Python and frozen PRISM source. The request must already
be bound by prism.diagnostic.contract.bind. This driver never reads a held-out answer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical
from etalon.boundary.prism_v1 import CLITransport
from etalon.data.design import _json
from etalon.research import ResearchJournal
from etalon.research.producer import DiagnosticClient, execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('request','producer-source','request-root','root','database','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--task',required=True)
    parser.add_argument('--budget',type=float,help='explicit wall_second budget when creating a new campaign')
    parser.add_argument('--source-kind',choices=('fixture','existing_real_artifact'),required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    request=_json(args.request)
    if not args.database.exists():
        if args.budget is None or args.budget<=0: raise ValueError('new diagnostic campaign requires an explicit positive budget')
        store=CampaignStore(args.database)
        store.configure(CampaignSpec('not_measured',args.budget,'wall_second','research-evidence/1'),
            [Endpoint('not_measured',args.task,'not_measured','count','research-evidence/1',0,
                      queryable=False,requires_handoff=False)])
    else:
        store=CampaignStore(args.database)
        if args.budget is not None and store.configuration()[0].budget!=args.budget:
            raise ValueError('existing budget cannot be silently replaced')
    client=DiagnosticClient(CLITransport(python=str(Path(args.python).resolve()),cwd=str(args.producer_source.resolve()),
        request_root=str(args.request_root.resolve()),timeout=request['budget']['wall_seconds']+120))
    result=execute(ResearchJournal(store),client,request,root=args.root,task_id=args.task,source_kind=args.source_kind)
    result.update(budget=store.balance(),python=sys.executable,
        imported_modules={'etalon':__import__('etalon').__file__,
                          'consumer':sys.modules[execute.__module__].__file__})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as out:out.write(canonical(result)+'\n')
    print(json.dumps({'report':str(args.output),'experiment_id':result['experiment_id'],
                     'producer_status':result['producer_status'],'scientific_judgment':'unresolved','budget':store.balance()}))


if __name__=='__main__':main()
