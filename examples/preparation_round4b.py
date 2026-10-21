"""Original-ledger preparation reservation/receipt admission; no new budget."""
import argparse,json,subprocess
from pathlib import Path
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.research import ResearchJournal
from etalon.research.preparation import admit_preparation

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('database','request','result','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--reservation',type=float)
    p.add_argument('--command-json',type=Path,help='optional actual PRISM preparer argv; read-only admission otherwise')
    a=p.parse_args()
    if not a.database.is_file():raise FileNotFoundError('existing shared ledger required')
    if a.output.exists():raise FileExistsError(a.output)
    command=json.loads(a.command_json.read_text()) if a.command_json else None
    if command is not None and (not isinstance(command,list) or not command or any(not isinstance(v,str) for v in command)):
        raise ValueError('preparer argv must be a nonempty string array')
    def invoke(request,result):subprocess.run(command,check=True)
    value=admit_preparation(ResearchJournal(CampaignStore(a.database)),request_path=a.request,result_path=a.result,
        reservation=a.reservation,invoke=invoke if command is not None else None)
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(value)+'\n')
    print(json.dumps({'admission_id':value['id'],'status':value['status'],'execution_id':value['execution_id']}))
