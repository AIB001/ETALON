"""Literal note selection fixtures; no scientific execution or relevance model."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.active.schema import canonical
from etalon.research.notes import read_entry, save_progress
from test_research_notes import case


def _publish(case, tmp_path):
    journal, note, original = case
    note.update(status='waiting', pending_handles=['recorded-job-1'],
        authorization={'allowed': 'read only'}, other_pauses={'R4b': 'user-paused'},
        next_action='Compare the scoped failure with the ordinary control.',
        invalid_when=['New chemical state'], lesson='The cheap control is still retained.',
        knowledge={f'finding-{i:03}': {'title': f'Finding {i}', 'lesson': f'Scoped result {i}',
                                   'rows': list(range(500))} for i in range(40)})
    note['knowledge']['a/b~c'] = {'title': 'Escaped stable key', 'lesson': None}
    note['knowledge']['a-rule'] = {'rule': 'Compare ordinary controls under the same costs.'}
    note['knowledge']['finding-000']['rows'] = list(range(5000))
    entry = tmp_path/'entry.json'
    rid = save_progress(journal, entry, note)
    return journal, note, original, entry, rid


def test_index_contains_continuation_and_pages_titles_without_claiming_bodies(case, tmp_path):
    _, note, _, entry, rid = _publish(case, tmp_path)
    first = read_entry(entry, view='index', max_bytes=4096, limit=5)
    assert len(canonical(first).encode()) <= 4096
    assert first['record_id'] == rid
    for key in ('question', 'summary', 'conditions', 'status', 'pending_handles', 'next_action', 'invalid_when'):
        assert first['knowledge'][key] == note[key]
    assert first['controls']['authorization'] == note['authorization']
    assert first['controls']['other_pauses'] == {'R4b': 'user-paused'}
    assert first['coverage']['knowledge'] == 'partial'
    assert first['coverage']['originals'] == 'not_read'
    assert first['coverage']['counter_count'] == first['coverage']['support_count'] == 1
    assert all('/knowledge/' in row['pointer'] and 'rows' not in row for row in first['index']['items'])
    assert first['index']['items'][0]['preview'] == note['knowledge']['a-rule']['rule']
    assert '/knowledge' not in first['followup']['select']['selectors']
    second = read_entry(first['details'], **first['followup']['next_page'], max_bytes=4096)
    assert not {r['pointer'] for r in first['index']['items']} & {r['pointer'] for r in second['index']['items']}
    assert second['index']['total_items'] == first['index']['total_items']
    counters = read_entry(first['details'], **first['followup']['counterevidence'])
    assert counters['selected'][0]['items'][0]['value'] == note['counterevidence'][0]
    assert counters['selected'][0]['body_complete']
    assert counters['originals_verified'] is None  # Reference delivery is not original reading.


def test_batch_select_exact_items_escaped_keys_and_collection_pages(case, tmp_path):
    _, note, _, entry, _ = _publish(case, tmp_path)
    batch = read_entry(entry, view='selected', selectors=[
        '/lesson', '/knowledge/finding-008/lesson', '/knowledge/a~1b~0c/lesson'])
    assert [row['value'] for row in batch['selected']] == [note['lesson'], 'Scoped result 8', None]
    page = read_entry(batch['details'], view='selected', selectors=['/knowledge/finding-008/rows'], limit=3)
    row = page['selected'][0]
    assert [r['value'] for r in row['items']] == [0, 1, 2]
    assert row['total_items'] == 500 and row['omitted_items'] == 497 and not row['body_complete']
    following = read_entry(page['details'], **row['next_read'])['selected'][0]
    assert [r['value'] for r in following['items']] == [3, 4, 5]
    broad = read_entry(entry, view='selected', selectors=['/knowledge'], limit=5, max_bytes=4096)
    assert len(canonical(broad).encode()) <= 4096
    assert any(r.get('body_omitted') for r in broad['selected'][0]['items'])
    assert broad['selected'][0]['next_offset'] is not None


@pytest.mark.parametrize('selectors', [[], ['/absent'], ['/knowledge/missing'], ['/knowledge/a~2b'],
                                      ['/lesson', '/lesson'], [{'bad': 'type'}]])
def test_invalid_selectors_are_explicit_errors(case, tmp_path, selectors):
    _, _, _, entry, _ = _publish(case, tmp_path)
    with pytest.raises(ValueError):
        read_entry(entry, view='selected', selectors=selectors)


def test_pinned_selection_survives_new_head_but_rejects_changed_original(case, tmp_path):
    journal, note, original, entry, rid = _publish(case, tmp_path)
    first = read_entry(entry, view='index')
    new = save_progress(journal, entry, {**note, 'lesson': 'A later interpretation'})
    assert new != rid
    selected = read_entry(first['details'], view='selected', selectors=['/lesson'])
    assert selected['record_id'] == rid and selected['selected'][0]['value'] == note['lesson']
    assert read_entry(entry, view='selected', selectors=['/lesson'])['selected'][0]['value'] == 'A later interpretation'
    original.write_text('changed original')
    assert read_entry(first['details'], view='index')['coverage']['originals'] == 'not_read'
    with pytest.raises(ValueError, match='original changed'):
        read_entry(first['details'], view='selected', selectors=['/lesson'], verify_originals=True)
    with pytest.raises(ValueError, match='original changed'):
        read_entry(first['details'])


def test_selection_rechecks_note_identity(case, tmp_path):
    journal, _, _, entry, rid = _publish(case, tmp_path)
    first = read_entry(entry, view='index')
    Path(journal.get(rid)['path']).write_text('{}')
    with pytest.raises(Exception, match='artifact hash/size mismatch'):
        read_entry(first['details'], view='selected', selectors=['/lesson'])


def test_abbreviated_continuation_remains_explicitly_selectable(case, tmp_path):
    journal, note, _, entry, _ = _publish(case, tmp_path)
    limitations = [f'Limitation {i}: verify input-state compatibility.' for i in range(30)]
    save_progress(journal, entry, {**note, 'invalid_when': limitations, 'summary': 'Observed boundary. ' * 60})
    page = read_entry(entry, view='index', max_bytes=4096)
    assert {'/summary', '/invalid_when'} <= set(page['coverage']['abbreviated_fields'])
    assert page['knowledge']['invalid_when']['preview'][0]['preview'] == limitations[0]
    selected = read_entry(page['details'], view='selected', selectors=['/invalid_when/29'])
    assert selected['selected'][0]['value'] == limitations[29]


def test_index_omits_delivered_fields_but_retains_abbreviations_and_evidence(case, tmp_path):
    journal, note, _ = case
    note.update(status='waiting', pending_handles=['job-1'], next_action='Inspect the result.',
        authorization={'allowed': 'read only'}, other_pauses={'R4b': 'user-paused'},
        summary='A deliberately long result summary. ' * 30,
        invalid_when=[f'Changed condition {i} requires review.' for i in range(20)],
        result_read={'selector': {'json': ['/rows/0/diagnosis', '/cost']}},
        knowledge={'ordinary_control': {'rule': 'Retain the ordinary control.'}})
    entry = tmp_path/'entry.json'
    save_progress(journal, entry, note, publication_id='fixture-publication')
    page = read_entry(entry, view='index', max_bytes=4096)
    inventory = page['index']
    assert inventory['inventory_policy'] == 'undelivered-fields/1'
    assert inventory['next_offset'] is None
    pointers = {item['pointer'] for item in inventory['items']}
    assert pointers == {'/knowledge/ordinary_control', '/summary', '/invalid_when',
                        '/result_read', '/evidence', '/counterevidence'}
    for key in ('question', 'conditions', 'status', 'outcome', 'pending_handles', 'next_action'):
        assert page['knowledge'][key] == note[key]
        assert '/'+key not in pointers
    assert page['controls']['authorization'] == note['authorization']
    selected = read_entry(page['details'], view='selected', selectors=['/publication_id'])
    assert selected['selected'][0]['value'] == 'fixture-publication'


def _drain_selection(entry, args):
    queue, seen, pages, delivered = [args], set(), [], {}
    while queue:
        request = queue.pop(0)
        assert canonical(request) not in seen, 'continuation must make progress'
        seen.add(canonical(request))
        page = read_entry(entry, **request)
        assert len(canonical(page).encode('utf-8')) <= args['max_bytes']
        pages.append(page)
        if page['coverage']['undelivered_selector_count']:
            queue.append(page['followup']['select'])
        for row in page['selected']:
            if 'value' in row:
                delivered[row['pointer']] = row['value']
            if row.get('next_read'):
                queue.append(row['next_read'])
            for item in row.get('items', []):
                if 'value' in item:
                    delivered[item['pointer']] = item['value']
                if item.get('next_read'):
                    queue.append(item['next_read'])
        assert all(request['max_bytes'] == args['max_bytes'] for request in queue)
        assert len(pages) <= 10, 'ordinary batch paging must not fragment into many calls'
    return pages, delivered


def test_native_five_selector_combination_pages_without_dropping_counter_scope(case, tmp_path):
    journal, note, _ = case
    # Same selector order and bounds as native/run-03's failed call; local refs
    # remain fixtures, with ten ordinary counter rows of similar serialized size.
    note.update(counterevidence=[{**note['evidence'][0], 'context': f'counter {i}: '+('x'*90)} for i in range(10)],
        evidence=note['evidence']*3, conditions={f'condition{i}': 'existing frozen condition' for i in range(5)},
        limits=['No independent transfer claim; retain scope and ordinary controls.']*5,
        decision='Inspect the saved geometry before another intervention.', other_pauses={'R4b': 'user-paused'})
    entry = tmp_path/'entry.json'; save_progress(journal, entry, note)
    args = dict(view='selected', selectors=['/counterevidence', '/conditions', '/limits', '/evidence', '/decision'],
                offset=0, limit=40, max_bytes=4096)
    pages, delivered = _drain_selection(entry, args)
    first = pages[0]
    assert first['coverage']['undelivered_selector_count'] > 0
    assert not first['coverage']['selected_body_complete']
    assert first['coverage']['counter_count'] == 10 and first['coverage']['support_count'] == 3
    assert set(first['followup']['select']['selectors']) == set(args['selectors']) - {r['pointer'] for r in first['selected']}
    assert len(pages) <= 4
    for key in ('counterevidence', 'evidence', 'limits'):
        assert [delivered[f'/{key}/{i}'] for i in range(len(note[key]))] == note[key]
    assert {key:delivered['/conditions/'+key] for key in note['conditions']} == note['conditions']
    assert delivered['/decision'] == note['decision']
    assert not any(item.get('body_omitted') for page in pages for row in page['selected'] for item in row.get('items', []))


def test_long_pointer_batch_reserves_its_continuation_and_atomic_limit_is_explicit(case, tmp_path):
    journal, note, _ = case
    keys = ['field'+str(i)+'x'*160 for i in range(6)]
    note.update({key: 'bounded scalar body '*14 for key in keys})
    note.update(large_scalar='x'*5000)
    too_long = 'pointer'+'x'*5000
    note[too_long] = 'small value'
    entry = tmp_path/'entry.json'; save_progress(journal, entry, note)
    args = dict(view='selected', selectors=['/'+key for key in keys], max_bytes=4096)
    pages, delivered = _drain_selection(entry, args)
    assert len(pages) > 1 and any(not p['coverage']['selected_body_complete'] for p in pages)
    assert {key:delivered['/'+key] for key in keys} == {key:note[key] for key in keys}
    with pytest.raises(ValueError, match='selector parameters exceed budget'):
        read_entry(entry, view='selected', selectors=['/'+too_long], max_bytes=4096)
    with pytest.raises(ValueError, match='selected value or selector parameters exceed budget'):
        read_entry(entry, view='selected', selectors=['/large_scalar'], max_bytes=4096)


def test_context_mcp_and_cli_expose_the_same_pinned_selectors(case, tmp_path):
    from etalon.mcp.evidence import register
    from etalon.research.research_context import build_research_context
    class Collector:
        def __init__(self): self.tools = {}
        def tool(self):
            def add(fn): self.tools[fn.__name__] = fn; return fn
            return add
    journal, note, _, entry, rid = _publish(case, tmp_path)
    direct = build_research_context(entry, view='index', max_bytes=4096)
    collector = Collector(); register(collector)
    read = collector.tools['etalon_research_context']
    indexed = json.loads(read(str(entry), view='summary', max_bytes=4096, view_options={'note_view': 'index'}))
    assert indexed['ok'] and indexed['details'] == direct['details']
    selected = json.loads(read(indexed['details'], view='summary', view_options={
        'note_view': 'selected', 'selectors': ['/lesson', '/knowledge/finding-004/lesson']}))
    assert selected['ok'] and [r['value'] for r in selected['selected']] == [note['lesson'], 'Scoped result 4']
    command = [sys.executable, str(Path(__file__).resolve().parents[1]/'examples/read_research_work.py'),
               str(entry), '--branch', 'anchor', '--view', 'index', '--limit', '2']
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    initial = json.loads(completed.stdout)
    budget_flag = initial['followup']['cli'].index('--max-bytes')
    assert initial['followup']['cli'][budget_flag + 1] == '4096'
    save_progress(journal, entry, {**note, 'lesson': 'Later head'})
    follow = subprocess.run(initial['followup']['cli'], capture_output=True, text=True, check=True)
    assert json.loads(follow.stdout)['record_id'] == rid
    exact = subprocess.run(command[:3] + ['--branch', 'anchor', '--record-id', rid,
        '--view', 'selected', '--select', '/lesson', '--select', '/knowledge/finding-004/lesson'],
        capture_output=True, text=True, check=True)
    assert [r['value'] for r in json.loads(exact.stdout)['selected']] == [note['lesson'], 'Scoped result 4']


def test_cli_budget_includes_unicode_argv_and_counter_pages(case, tmp_path, monkeypatch):
    from examples.read_research_work import read
    from etalon.runtime.files import file_identity
    journal, note, original = case
    note['counterevidence'] = [{**note['evidence'][0], 'context': f'反证 {i} '+('受条件约束'*16)} for i in range(10)]
    note['decision'] = '保留普通对照'
    entry = tmp_path/('项目'*35)/'当前入口.json'
    save_progress(journal, entry, note)
    identity = file_identity(original)
    queue = [dict(view='selected',selectors=['/counterevidence','/decision'],max_bytes=4096,limit=40)]
    delivered, seen = {}, set()
    while queue:
        args = queue.pop(0)
        assert canonical(args) not in seen
        seen.add(canonical(args))
        page = read(entry,'anchor',**args)
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode('utf-8')) <= 4096
        assert page['followup']['cli']
        if page['coverage']['undelivered_selector_count']:
            queue.append(page['followup']['select'])
        for row in page['selected']:
            if row.get('next_read'):
                queue.append(row['next_read'])
            if 'value' in row:
                assert row['pointer'] not in delivered
                delivered[row['pointer']] = row['value']
            for item in row.get('items',[]):
                assert 'value' in item and item['pointer'] not in delivered
                delivered[item['pointer']] = item['value']
        assert len(seen) < 15
    assert [delivered[f'/counterevidence/{i}'] for i in range(10)] == note['counterevidence']
    assert delivered['/decision'] == note['decision']
    assert file_identity(original) == identity


def test_current_brief_retains_limits_read_recipes_and_pinned_definitions(case,tmp_path):
    from etalon.research.notes import read_current, find_notes
    journal,note,_ = case
    note.update(lesson='Useful only at this condition', limits=['No independent stability evidence'],
        knowledge='扩展观测'*10000, state='paused',
        definitions={'metric:dual_contact':'a'*64}, scientific_result={'profile_digest':'a'*64,'observations':{'count':3}})
    note['counterevidence'][0]['context'] = 'Negative ordinary control remains informative'
    entry = tmp_path/'entry.json'; rid=save_progress(journal,entry,note)
    entry_body=json.loads(entry.read_text()); entry_body['definitions']=note['definitions']
    entry.write_text(json.dumps(entry_body),encoding='utf-8')
    page=read_current(entry,max_bytes=4096)
    assert len(canonical(page).encode('utf-8')) <= 4096
    assert page['knowledge']['limits']==note['limits']
    assert '/knowledge' in page['coverage']['omitted_fields']
    assert page['controls']['state']=='paused'
    assert page['definitions']==page['details']['definitions']==note['definitions']
    assert page['references']['counterevidence']['items'][0]['value']==note['counterevidence'][0]
    assert page['coverage']['source_bodies']=='not_delivered' and page['originals_verified'] is None
    assert read_entry(page['details'],view='selected',selectors=['/lesson'])['record_id']==rid
    with pytest.raises(ValueError,match='override'):
        read_current(entry,definitions={})
    with pytest.raises(ValueError,match='override'):
        read_current(entry,definitions={'metric:dual_contact':'b'*64})
    matches=find_notes(journal,task_id=note['task_id'],conditions=note['conditions'],definitions={'metric:dual_contact':'b'*64})
    assert not matches['matches'] and matches['related'][0]['definitions']['mismatched']==['metric:dual_contact']
    selected=read_current(entry,recipe={'view':'selected','selectors':['/lesson']},max_bytes=4096)
    assert selected['selected'][0]['value']==note['lesson'] and selected['controls']['state']=='paused'
    with pytest.raises(ValueError,match='recipe'):
        read_current(entry,recipe={'view':'selected','selectors':['/lesson'],'branch_id':'other'})


def test_current_explicit_entry_recipe_is_shared_by_cli_and_mcp(case,tmp_path):
    from examples.read_research_work import read
    from etalon.research.notes import read_current
    from etalon.mcp.evidence import register
    class Collector:
        def __init__(self): self.tools={}
        def tool(self):
            def add(fn): self.tools[fn.__name__]=fn; return fn
            return add
    journal,note,_=case;note['lesson']='Exact host selection';entry=tmp_path/'entry.json'
    save_progress(journal,entry,note)
    body=json.loads(entry.read_text());body['read_recipe']={'view':'selected','selectors':['/lesson']}
    entry.write_text(json.dumps(body),encoding='utf-8')
    sdk=read_current(entry,max_bytes=4096)
    cli=read(entry,'anchor',max_bytes=4096)
    collector=Collector();register(collector)
    mcp=json.loads(collector.tools['etalon_research_context'](str(entry),view='summary',max_bytes=4096))
    assert sdk['selected']==cli['selected']==mcp['selected']
    assert sdk['details']==cli['details']==mcp['details']


def test_real_cli_packet_budget_includes_utf8_receipt_and_argv(case,tmp_path):
    journal,note,_=case
    note['counterevidence']=[{**note['evidence'][0],'context':f'条件反证 {i} '+('有限范围'*20)} for i in range(10)]
    entry=tmp_path/'当前入口.json';save_progress(journal,entry,note)
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'examples/read_research_work.py'),
        str(entry),'--branch','anchor','--view','selected','--select','/counterevidence','--packet','--max-bytes','4096']
    completed=subprocess.run(command,capture_output=True,check=True)
    assert len(completed.stdout)<=4096
    envelope=json.loads(completed.stdout.decode('utf-8'))
    packet=envelope['packet']
    assert packet['selected'][0]['items'][0]['value']==note['counterevidence'][0]
    assert packet['selected'][0]['next_read']
    assert packet['followup']['cli']
