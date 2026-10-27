"""Research records remain optional consumers of actual execution outputs."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import original, record_note, publish_note, read_entry, find_notes
from etalon.research.notes import save_progress


@pytest.fixture
def case(tmp_path):
    store=CampaignStore(tmp_path/'research.sqlite')
    store.configure(CampaignSpec('x',0,'wall_second','fixture'),
        [Endpoint('x','diagnostic','recorded','count','fixture/1',0,queryable=False)])
    result=tmp_path/'result.json';result.write_text('{"retained":0}')
    note=dict(task_id='fixture',branch_id='anchor',question='Which anchor?',summary='Neither retained',
        conditions={'target':'fixture','endpoint':'contacts'},outcome='retain',
        evidence=[original(result)],counterevidence=[original(result)])
    return ResearchJournal(store),note,result


def test_one_record_replays_and_retrieves_negative_without_graph(case,tmp_path):
    journal,note,result=case
    rid=record_note(journal,note,tmp_path/'notes')
    assert record_note(journal,note,tmp_path/'notes')==rid
    with journal.store.connection() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'research:%'").fetchone()[0]==1
    entry=tmp_path/'ENTRY.json';publish_note(entry,journal,rid)
    assert read_entry(entry)['originals_verified']==1
    queried=find_notes(journal,task_id='fixture',conditions=note['conditions'])
    assert queried['matches'][0]['note']['outcome']=='retain'
    assert not find_notes(journal,task_id='fixture',conditions={'target':'new-target'})['matches']
    assert not find_notes(journal,task_id='fixture',conditions={**note['conditions'],'unknown':'x'})['matches']
    code="from etalon.research.notes import read_entry; import sys,json; r=read_entry(sys.argv[1]); assert 'etalon.research.graph_memory' not in sys.modules; print(json.dumps(r))"
    completed=subprocess.run([sys.executable,'-c',code,str(entry)],capture_output=True,text=True,check=True)
    assert json.loads(completed.stdout)['record_id']==rid
    result.write_text('{"retained":999}')
    with pytest.raises(ValueError,match='original changed'):read_entry(entry)


def test_entry_failure_can_be_repaired_without_science_and_keeps_old_notes(case,tmp_path,monkeypatch):
    from etalon.research import notes
    journal,note,result=case;entry=tmp_path/'ENTRY.json'
    rid=record_note(journal,note,tmp_path/'notes');publish_note(entry,journal,rid)
    update={**note,'summary':'New explanation, old result retained','supersedes':[rid]}
    revised=record_note(journal,update,tmp_path/'notes')
    save=notes._save
    monkeypatch.setattr(notes,'_save',lambda *a,**k:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):publish_note(entry,journal,revised)
    assert read_entry(entry)['record_id']==rid
    monkeypatch.setattr(notes,'_save',save);publish_note(entry,journal,revised)
    assert read_entry(entry)['record_id']==revised
    assert len(find_notes(journal,task_id='fixture',conditions=note['conditions'])['matches'])==2
    with pytest.raises(ValueError,match='entry changed'):publish_note(entry,journal,rid)


def test_existing_context_api_reads_note_without_graph(case,tmp_path,monkeypatch):
    from etalon.research import research_context as context
    journal,note,_=case;rid=record_note(journal,note,tmp_path/'notes');entry=tmp_path/'ENTRY.json'
    publish_note(entry,journal,rid)
    monkeypatch.setattr(context,'build_research_graph',lambda *a,**k:(_ for _ in ()).throw(AssertionError('graph traversed')))
    result=context.build_research_context(entry)
    assert result['note']=={**note,'schema':'etalon-research-note/1'}


def test_existing_mcp_reads_note_and_one_hop_locator(case,tmp_path):
    from etalon.mcp.evidence import register
    class Collector:
        def __init__(self): self.tools={}
        def tool(self):
            def add(fn): self.tools[fn.__name__]=fn; return fn
            return add
    journal,note,_=case;rid=record_note(journal,note,tmp_path/'notes');entry=tmp_path/'ENTRY.json'
    publish_note(entry,journal,rid)
    locator=tmp_path/'CURRENT.json';locator.write_text(json.dumps({'entry':'ENTRY.json'}))
    collector=Collector();register(collector);read=collector.tools['etalon_research_context']
    for source in [str(entry),{'entry':str(entry)},str(locator)]:
        response=json.loads(read(source,conditions=note['conditions']))
        assert response['ok'] and response['record_id']==rid
    assert not json.loads(read(str(entry),conditions={'target':'different'}))['ok']


def test_existing_hook_delivers_real_note_write_and_delta(case,tmp_path):
    from etalon import research_hooks as hooks
    journal,note,_=case;entry=tmp_path/'ENTRY.json'
    note['knowledge']=['A stable, scoped rule. '*150]
    rid=record_note(journal,note,tmp_path/'notes');publish_note(entry,journal,rid)
    def event(name,number):
        return {'hook_event_name':name,'session_id':'note-session','event_id':str(number),
                **({'tool_name':'mcp__etalon__etalon_research_context','tool_use_id':str(number),'tool_input':{}} if name=='PreToolUse' else {})}
    first=hooks.handle_event(str(entry),event('SessionStart',1),host='codex',state_dir=tmp_path/'hooks')
    assert first['ok']
    updated={**note,'summary':'New evidence changes the next question','supersedes':[rid]}
    new=record_note(journal,updated,tmp_path/'notes');publish_note(entry,journal,new)
    second=hooks.handle_event(str(entry),event('PreToolUse',2),host='codex',state_dir=tmp_path/'hooks')
    delivery=second['receipt']['injection']['delivery']
    assert second['ok']
    assert delivery['mode']=='delta'
    payload=json.loads(second['host_output']['hookSpecificOutput']['additionalContext'].split('\n',1)[1])
    assert 'knowledge' not in payload['context_delta']['set']
    assert payload['context_delta']['set']['summary']==updated['summary']
    third=hooks.handle_event(str(entry),event('PreToolUse',3),host='codex',state_dir=tmp_path/'hooks')
    assert third['ok'] and third['receipt']['injection']['delivery']['mode']=='unchanged'


def test_project_heads_replay_and_branch_updates_survive_interrupted_publication(case,tmp_path,monkeypatch):
    from etalon.research import notes
    journal,note,_=case; entry=tmp_path/'entry.json'
    a=save_progress(journal,entry,note)
    b=save_progress(journal,entry,{**note,'branch_id':'other','summary':'A different open question'})
    assert read_entry(entry)['branch_id']=='other'
    update={**note,'summary':'An informative negative result'}
    c=save_progress(journal,entry,update,activate=False)
    assert read_entry(entry)['record_id']==b
    assert read_entry(entry,branch_id='anchor')['record_id']==c
    assert save_progress(journal,entry,update,activate=False)==c
    # A failed pointer replacement leaves a recoverable record, not a rerun.
    saved=notes._save
    def fail(path,*args,**kwargs):
        if Path(path)==entry: raise OSError('publication interrupted')
        return saved(path,*args,**kwargs)
    monkeypatch.setattr(notes,'_save',fail)
    with pytest.raises(OSError):save_progress(journal,entry,{**update,'summary':'Third observation'})
    monkeypatch.setattr(notes,'_save',saved)
    d=save_progress(journal,entry,{**update,'summary':'Third observation'})
    assert read_entry(entry)['record_id']==d
    assert read_entry(entry,branch_id='other')['record_id']==b
    with pytest.raises(ValueError,match='entry changed'):publish_note(entry,journal,a)


def test_knowledge_is_small_and_original_verification_is_explicit(case,tmp_path,monkeypatch):
    from etalon.research import notes
    journal,note,result=case;entry=tmp_path/'entry.json'
    note.update(raw_debug=list(range(10000)),other_pauses={'R4b':'user-paused'},lesson='Do not optimize this geometry objective alone',
                invalid_when='Different molecular state',residuals=['Unknown escape mechanism'])
    rid=save_progress(journal,entry,note)
    def forbidden(*args):raise AssertionError('routine continuation reread originals')
    monkeypatch.setattr(notes,'_verify',forbidden)
    brief=read_entry(entry,view='knowledge')
    assert brief['knowledge']['lesson']==note['lesson']
    assert brief['controls']['other_pauses']=={'R4b':'user-paused'}
    assert brief['coverage']['counter_count']==1 and brief['coverage']['originals']=='not_read'
    assert brief['originals_verified'] is None and 'raw_debug' not in json.dumps(brief)
    assert len(json.dumps(brief))<3000
    monkeypatch.undo()
    assert read_entry(brief['details'])['originals_verified']==1
    result.write_text('changed original')
    assert read_entry(entry,view='knowledge')['record_id']==rid
    with pytest.raises(ValueError,match='original changed'):read_entry(brief['details'])


def test_project_catalog_pages_exact_heads_and_rejects_changed_cursor(case,tmp_path):
    from etalon.research.resource_catalog import build_resource_catalog
    journal,note,_=case;entry=tmp_path/'entry.json'
    save_progress(journal,entry,note)
    save_progress(journal,entry,{**note,'branch_id':'second'})
    first=build_resource_catalog(entry=entry,limit=1)
    second=build_resource_catalog(entry=entry,limit=1,cursor=first['next_cursor'])
    assert second['next_cursor'] is None
    assert first['items'][0]['branch_id']!=second['items'][0]['branch_id']
    save_progress(journal,entry,{**note,'summary':'Changed knowledge'})
    with pytest.raises(ValueError,match='changed'):build_resource_catalog(entry=entry,cursor=first['next_cursor'])


def test_replaying_producer_does_not_erase_later_interpretation(case,tmp_path):
    journal,note,_=case;entry=tmp_path/'entry.json'
    producer=save_progress(journal,entry,note,publication_id='request-1:result')
    interpreted=save_progress(journal,entry,{**note,'summary':'New mechanism interpretation after the producer'})
    assert save_progress(journal,entry,note,publication_id='request-1:result')==producer
    assert read_entry(entry)['record_id']==interpreted
    with pytest.raises(ValueError,match='different content'):
        save_progress(journal,entry,{**note,'summary':'changed result'},publication_id='request-1:result')
