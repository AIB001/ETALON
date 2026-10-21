"""Seen-group query outcome versus scientific improvement; no producer dispatch."""
import argparse
import json
from pathlib import Path
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.research import ResearchJournal
from etalon.research.rows import config_objects,verify_dataset
from etalon.research.transfer import checked_rows
from etalon.research.transfer_checks import outcome_improvement_diagnostic


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','rows','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--database',type=Path)
    p.add_argument('--role',choices=('train','calibration'),required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    config=json.loads(a.config.read_text());split,catalog=config_objects(config)
    rows=checked_rows(json.loads(a.rows.read_text()),split,a.role,catalog)
    if any(r['source_kind']=='measured' for r in rows):
        if not a.database or not a.database.is_file():raise ValueError('actual rows require existing original journal')
        verify_dataset(ResearchJournal(CampaignStore(a.database)),rows,role=a.role,split_sha256=split.sha256)
    result=outcome_improvement_diagnostic(rows,catalog,cost_weight=.001)
    result.update(split_sha256=split.sha256,source_role=a.role,policy_fitted=False)
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(result)+'\n')
    print(json.dumps({'output':str(a.output.absolute()),'new_scientific_executions':0,'policy_fitted':False}))
