"""Explicit later-round release of formerly independent evidence as seen development."""
import json
from pathlib import Path
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .schema import Artifact


def register_seen_labels(journal,path,labels,split):
    release=labels.get('seen_development_release')
    if not release:return None,{}
    if (release.get('purpose')!='former_independent_validation_now_seen'
            or release.get('new_split_sha256')!=split.sha256 or not release.get('tasks')):
        raise StateError('seen release must explicitly bind the new development split and purpose')
    parents=set();lineage={}
    for task,source in release['tasks'].items():
        if task not in split.tasks or split.tasks[task]['role'] not in {'train','calibration'} or task not in labels['tasks']:
            raise StateError('formerly seen task cannot enter new independent test')
        old=journal.get(source['source_taskrows_id'])
        if old['role']!='test' or task not in old['executions']:
            raise StateError('seen release source must be the original independent dataset')
        with journal.store.connection() as db:
            artifacts=journal._artifacts(db,old['evidence'])
        matches=[a for a in artifacts if a.role=='independent_validation' and a.sha256==source['source_label_sha256']]
        if len(matches)!=1:raise StateError('source label must retain independent_validation role and exact hash')
        original=json.loads(Path(matches[0].path).read_text())
        if original['tasks'][task]!=labels['tasks'][task]:
            raise StateError('seen release changes scientific labels; explicit data amendment required')
        parents.add(matches[0].id)
        lineage[task]={'source_taskrows_id':source['source_taskrows_id'],
            'source_label_artifact_id':matches[0].id,'original_role':'test',
            'new_role':split.tasks[task]['role'],'original_executions':old['executions'][task]}
    body={'schema':'etalon-seen-development-release/1','purpose':release['purpose'],
        'new_split_sha256':split.sha256,'lineage':lineage,'release_file':file_identity(path),
        'independent_validation_reusable':False,'original_records_unchanged':True}
    identifier='seenrelease-'+digest(body)
    with journal.store.connection(write=True) as db:journal._put(db,identifier,body)
    artifact=Artifact(str(Path(path).absolute()),**file_identity(path),source_kind='existing_real_artifact',
        role='diagnostic',parents=tuple(sorted(parents)),bindings={'kind':'explicitly_released_seen_development_labels',
            'seen_release_id':identifier,'development_split_sha256':split.sha256,'former_role':'independent_validation',
            'future_use':'seen_development_only'})
    return journal.record(artifact),lineage
