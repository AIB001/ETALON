import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
from record_method_comparison import archive


def test_normal_negative_comparison_closes_and_replays_without_fake_holdout(tmp_path):
    paths={}
    for key in ['problem','protocol','result','cost']:
        p=tmp_path/(key+'.json');p.write_text(json.dumps({'fixture':True,'kind':key}))
        paths[key]=str(p)
    method=dict(name='fixture-base',entrypoint='fixture.method',change_summary='Fixture version',
                parameters={'kind':'fixture'})
    spec=dict(**paths,task_id='fixture',source_id='fixture-comparison',scope='Fixture only',
        branch_id='method',conditions={'target':'fixture','endpoint':'fixture'},initial_configuration='fixture-sha',
        base=method,candidate={**method,'name':'fixture-candidate','parameters':{'kind':'changed-fixture'}},
        expected_change='Fixture improvement',question='Fixture comparison?',
        metrics=[dict(name='fixture-quality',family='regression',unit='count',
                      denominator='fixture attempts',missing_policy='unknown')],
        conclusion='Fixture negative result',outcome='retain',lesson='Retain the baseline in this fixture',
        invalid_when='Any real task',reconsider_when='New fixture evidence',capability_id='fixture.method')
    first=archive(spec,tmp_path/'journal')
    assert first['outcome']=='retain' and first['applicable_experiences']==1
    before=(tmp_path/'journal/research.sqlite').read_bytes()
    second=archive(spec,tmp_path/'journal')
    assert first==second and before==(tmp_path/'journal/research.sqlite').read_bytes()
    # New rounds reuse a stable project entry even if their output directories differ.
    newer=archive({**spec,'conclusion':'Second measured result'},tmp_path/'round-two',entry=first['entry'])
    assert not (tmp_path/'round-two/research.sqlite').exists()
    assert newer['entry']==first['entry'] and newer['note_id']!=first['note_id']
    archive(spec,tmp_path/'old-retry',entry=first['entry'])
    from etalon.research.notes import read_entry
    assert read_entry(first['entry'])['record_id']==newer['note_id']
    # The same writer continues a portable entry without reopening its old path
    # or mutating the sealed snapshot.
    from etalon.research.handoff import plan_research_handoff, export_research_handoff
    from etalon.research.notes import entry_value, entry_journal, read_note
    from etalon.runtime.files import file_identity
    copied=tmp_path/'copied'
    export_research_handoff(plan_research_handoff(first['entry']),copied)
    snapshot=entry_journal(entry_value(copied/'entry.json')).store.path
    identity=file_identity(snapshot)
    continued=archive({**spec,'conclusion':'Portable new result'},tmp_path/'portable-result',entry=copied/'entry.json')
    assert read_entry(copied/'entry.json')['record_id']==continued['note_id']
    assert file_identity(snapshot)==identity
    j=entry_journal(entry_value(copied/'entry.json'))
    assert read_note(j,first['note_id'])['note']['outcome']=='retain'
    assert not (tmp_path/'portable-result/research.sqlite').exists()
