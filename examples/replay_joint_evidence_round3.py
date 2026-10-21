"""Verify a new consumer against old real receipts on a new SQLite backup only.

Producer dispatch is forbidden. Original database, decisions, predictions and
old metadata bodies must remain unchanged; new feedback records may be appended
to the backup and must replay idempotently.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.boundary.prism_v1 import CLITransport,file_identity
from etalon.research import ResearchJournal
from etalon.research.pose import PoseClient,execute


class ReadReceiptsOnly(PoseClient):
    def run(self,*args,**kwargs):
        raise AssertionError('scientific redispatch forbidden in compatibility replay')


def metadata(path):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        return dict(db.execute('SELECT key,body FROM metadata'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('database','initialization','predictions','validation-result','release','producer-source','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--python',default=sys.executable)
    p.add_argument('--admission',type=Path,action='append',required=True)
    a=p.parse_args();wall=time.perf_counter();cpu=time.process_time()
    a.output.mkdir(parents=True,exist_ok=False)
    before_database=file_identity(a.database);before=metadata(a.database)
    backup=a.output/'research.sqlite'
    with sqlite3.connect(a.database.resolve().as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(backup) as target:source.backup(target)
    journal=ResearchJournal(CampaignStore(backup));balance=journal.store.balance();events=len(journal.store.events())
    prediction=json.loads(a.predictions.read_text());decision=prediction['decision_sha256']
    client=ReadReceiptsOnly(CLITransport(str(Path(a.python).absolute()),str(a.producer_source.resolve()),
                                        str((a.output/'unused-requests').resolve()),timeout=120))
    replayed=[]
    for path in a.admission:
        old=json.loads(path.read_text());result_path=Path(old['result_path'])
        request=json.loads((result_path.parent/'request.json').read_text())
        receipt=execute(journal,client,request,root=result_path.parent.parent,task_id=old['task_id'],
                        stage=old['stage'],source_kind='existing_real_artifact',
                        decision_sha256=decision if old['stage']=='validation' else None)
        assert receipt['replayed'] and receipt['result_sha256']==old['result_sha256']
        replayed.append({'execution_id':receipt['execution_id'],'status':receipt['producer_status'],
                         'result_sha256':receipt['result_sha256']})
    assert journal.store.balance()==balance and len(journal.store.events())==events
    if not {'FAILED','COMPLETED'}<={r['status'] for r in replayed}:
        raise ValueError('include one failed and one completed historical validation action')
    driver=Path(__file__).with_name('joint_revision_round3.py')
    spec=importlib.util.spec_from_file_location('joint_driver_compatibility',driver)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=SimpleNamespace(database=backup,initialization=a.initialization,predictions=a.predictions,
                         validation_result=a.validation_result,release=a.release,output=a.output/'feedback.json')
    first=module.feedback(args);first_events=len(journal.store.events())
    args.output=a.output/'feedback-repeat.json';second=module.feedback(args)
    assert first==second and len(journal.store.events())==first_events
    after=metadata(backup)
    assert all(after.get(k)==v for k,v in before.items())
    assert metadata(a.database)==before and file_identity(a.database)==before_database
    assert journal.store.balance()==balance
    result={'schema':'etalon-consumer-legacy-replay/1','python':sys.executable,
        'consumer_import':sys.modules[execute.__module__].__file__,
        'original_database':{'path':str(a.database.resolve()),**before_database},
        'original_metadata_preserved':len(before),'new_metadata_records':sorted(set(after)-set(before)),
        'receipt_replays':replayed,'receipt_replay_added_events':0,
        'feedback_first_added_events':first_events-events,'feedback_second_added_events':0,
        'budget':balance,'new_producer_executions':0,'dispatch_guard':'ReadReceiptsOnly.run always raises',
        'controller_state':first['controller_transition']['research_state'],
        'legacy_step_lookup':[s.get('admission_lookup') for s in first['controller_transition']['archive']['step_records']],
        'local_postprocessing_overhead':{'wall_second':time.perf_counter()-wall,'cpu_second':time.process_time()-cpu}}
    (a.output/'report.json').write_text(canonical(result)+'\n',encoding='utf-8')
    print(json.dumps({'original_metadata_preserved':len(before),'replayed':len(replayed),
                      'new_producer_executions':0,'state':result['controller_state']}))


if __name__=='__main__':main()
