"""Bounded field selection still validates the exact published original."""
import json
import pytest

from etalon.research.evidence import read_evidence, EvidenceError
from etalon.research.evidence_refs import file_identity, make_ref
from test_evidence_query import publish


def source(tmp_path, value):
    path = tmp_path/'result.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    record = {'path': str(path), **file_identity(path), 'role': 'diagnostic',
              'source_kind': 'existing_real_artifact'}
    workspace = publish(tmp_path, [{'record_id': 'result', 'record_type': 'artifact',
                                   'visibility': 'released', 'record': record}])
    return path, workspace, make_ref('released', 'result', record)


def test_batched_pointers_pages_and_counter_read_keep_identity(tmp_path):
    data = {'rows': [{'seed': i, 'score': i/10} for i in range(60)],
            'cost': None, 'arrays': ['large '*10000], 'counterevidence': ['disagree'],
            'a/b': {'x~y': 7}}
    path, ws, ref = source(tmp_path, data)
    result = read_evidence(ws, ref, view='artifact', selector={
        'json': ['/rows', '/cost', '/absent', '/counterevidence', '/a~1b/x~0y']}, limit=2)
    rows, cost, missing, counters, escaped = result['selection']['items']
    assert rows['value']==data['rows'][:2] and rows['next_offset']==2
    assert cost['status']=='selected' and cost['value'] is None
    assert missing['status']=='missing' and 'value' not in missing
    assert counters['value']==['disagree'] and escaped['value']==7
    assert 'large ' not in json.dumps(result)
    assert result['verification']['artifact']=='verified'
    assert result['selection']['unselected_content']=='not_delivered'
    next_page=read_evidence(ws,result['ref'],view='artifact',selector={'json':['/rows']},offset=2,limit=2)
    assert next_page['selection']['items'][0]['value']==data['rows'][2:4]
    # Even a changed unselected array invalidates the original, not just its projection.
    path.write_text(json.dumps({**data,'arrays':['different']}),encoding='utf-8')
    with pytest.raises(EvidenceError,match='mismatch'):
        read_evidence(ws,ref,view='artifact',selector={'json':['/cost']})


def test_large_projection_guides_to_child_and_bytes_still_work(tmp_path):
    _,ws,ref=source(tmp_path,{'array':['x'*100000], 'choice': 'saved'})
    with pytest.raises(EvidenceError,match='child pointers'):
        read_evidence(ws,ref,view='artifact',selector={'json':['/array']})
    page=read_evidence(ws,ref,view='artifact',selector='bytes',limit=20)
    assert page['fragment']['next_offset']==20
    assert read_evidence(ws,ref,view='artifact',selector={'json':['/choice']})['selection']['items'][0]['value']=='saved'
    with pytest.raises(EvidenceError,match='artifact view'):
        read_evidence(ws,ref,view='record',selector={'json':['/choice']})


@pytest.mark.parametrize('overhead', [0, 353, 701])
def test_wrapped_unicode_pages_deliver_every_counter_once(tmp_path, overhead):
    from etalon.active.schema import canonical
    data = {'counters': [{'context': f'反证 {i} '+('有限范围' * 35)} for i in range(12)],
            'cost': None, 'definition': 'observed_count / all_candidates',
            'extra': {'b': '未读取原件', 'a': '保留阴性结果'}}
    path, ws, ref = source(tmp_path, data)
    identity = file_identity(path)
    wrap = lambda payload: json.dumps({'ok': True, 'transport': '变'*overhead, 'data': payload},
        ensure_ascii=False,separators=(',',':'))
    queue = [dict(view='artifact', selector={'json': ['/counters', '/cost', '/missing', '/definition', '/extra']},
                  limit=40, max_bytes=4096)]
    delivered, seen, count = {}, set(), 0
    while queue:
        request = queue.pop(0)
        key = canonical(request)
        assert key not in seen, 'continuation must progress'
        seen.add(key)
        page = read_evidence(ws, ref, **request, response_envelope=wrap)
        assert len(wrap(page).encode('utf-8')) <= 4096
        assert page['verification']['artifact'] == 'verified'
        assert page['ref']['sha256'] == identity['sha256']
        for row in page['selection']['items']:
            if row['pointer'] == '/counters':
                for index, value in enumerate(row['value'], row['offset']):
                    pointer = '/counters/'+str(index)
                    assert pointer not in delivered
                    delivered[pointer] = value
            else:
                assert row['pointer'] not in delivered
                delivered[row['pointer']] = row
        queue.extend(page['selection']['next_reads'])
        count += 1
        assert count <= 20
    assert count > 1
    assert [delivered['/counters/'+str(i)] for i in range(12)] == data['counters']
    assert delivered['/cost']['value'] is None
    assert delivered['/missing']['status'] == 'missing' and 'value' not in delivered['/missing']
    assert delivered['/extra']['value'] == data['extra']
    assert file_identity(path) == identity


def test_mcp_selection_measures_ok_wrapper(tmp_path):
    from etalon.mcp.evidence import register
    from etalon.active.schema import canonical
    class Collector:
        def __init__(self): self.tools = {}
        def tool(self):
            def add(fn): self.tools[fn.__name__] = fn; return fn
            return add
    _, ws, ref = source(tmp_path, {'rows': ['中文'*150 for _ in range(12)]})
    collector = Collector(); register(collector)
    result = collector.tools['etalon_evidence_read'](str(ws), ref, view='artifact',
        selector={'json':['/rows']}, max_bytes=4096)
    page = json.loads(result)
    assert page['ok']
    assert len(result.encode('utf-8')) <= 4096
    assert page['selection']['next_reads']


def test_atomic_value_larger_than_remaining_wrapper_budget_is_explicit(tmp_path):
    _, ws, ref = source(tmp_path, {'body': '反证'*300})
    with pytest.raises(EvidenceError, match='child pointers'):
        read_evidence(ws, ref, view='artifact', selector={'json':['/body']}, max_bytes=4096,
            response_envelope=lambda payload: {'transport':'变'*901,'data':payload})


def test_raw_original_byte_pages_also_fit_actual_wrapper(tmp_path):
    import base64
    from etalon.active.schema import canonical
    path, ws, ref = source(tmp_path, {'body':'反证'*2500})
    original=path.read_bytes();chunks=[];offset=0
    wrapper=lambda payload: {'ok':True,'variable_metadata':'long'*200,**payload}
    while offset is not None:
        page=read_evidence(ws,ref,view='artifact',selector='bytes',limit=32768,offset=offset,
            max_bytes=4096,response_envelope=wrapper)
        assert len(canonical(wrapper(page)).encode('utf-8'))<=4096
        chunks.append(base64.b64decode(page['fragment']['content']))
        assert page['verification']['artifact']=='verified'
        offset=page['fragment']['next_offset']
    assert b''.join(chunks)==original
