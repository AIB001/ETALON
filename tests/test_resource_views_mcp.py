"""Cross-module optional entry views; fixtures do not measure scientific benefit."""
import json

from etalon.mcp import evidence, methods
from etalon.research.evidence_refs import file_identity
from test_mcp import Collector
from test_research_graph_memory import graph_case
from test_research_continuation import case
from test_evaluation_contract import setup


def registered():
    collector = Collector()
    evidence.register(collector)
    methods.register(collector)
    return collector.tools


def test_existing_entry_supports_bounded_inventory_and_exact_branch(graph_case):
    t, c = registered(), graph_case
    identity = file_identity(c['store'].path)
    summary = json.loads(t['etalon_research_context'](str(c['entry']), view='summary'))
    assert summary['ok']
    catalog = json.loads(t['etalon_research_context'](summary['read_context'],
        view='resources', limit=2, max_bytes=65536,
        view_options={'skill_refs': ['research-index']}))
    assert catalog['ok'] and catalog['schema'] == 'etalon-resource-catalog/1'
    assert len(catalog['items']) <= 2 and catalog['automatic_adoption'] is False
    if catalog['next_cursor']:
        next_page = json.loads(t['etalon_research_context'](summary['read_context'],
            view='resources', limit=2, max_bytes=65536, cursor=catalog['next_cursor'],
            view_options={'skill_refs': ['research-index']}))
        assert next_page['ok']
    branch = json.loads(t['etalon_research_context'](summary['read_context'],
        view='branch', branch_id='fixed-core', max_bytes=65536))
    assert branch['ok'] and branch['branch']['branch_id'] == 'fixed-core'
    assert branch['continuation']['authority_state'] == 'paused'
    assert branch['automatic_activation'] is False
    assert file_identity(c['store'].path) == identity and not c['store'].actions()


def test_view_options_do_not_silently_change_scope_or_trigger_work(graph_case):
    call = registered()['etalon_research_context']
    entry = str(graph_case['entry'])
    for args in [
        {'view': 'resources', 'view_options': {'execute': True}},
        {'view': 'resources', 'conditions': {'target': 'other'}},
        {'view': 'resources', 'policy': {'anything': 'broaden'}},
        {'view': 'branch', 'branch_id': 'not-a-branch'},
        {'view': 'branch', 'branch_id': 'fixed-core', 'view_options': {'activate': True}},
        {'view': 'summary', 'branch_id': 'fixed-core'},
    ]:
        assert not json.loads(call(entry, **args))['ok']
    assert not graph_case['store'].actions()


def test_optional_evaluation_reuses_originals_without_adoption(case):
    t = registered()
    contract, workspace = setup(case, max_error=1)
    identity = file_identity(case[0].store.path)
    result = json.loads(t['etalon_method_evaluate'](contract=contract, workspace=str(workspace)))
    assert result['ok'] and result['status'] == 'criteria_failed'
    assert result['adoption'] == result['execution'] == 'not_performed'
    assert result['freeze_status'] == 'retrospective_not_prospective'
    assert file_identity(case[0].store.path) == identity and not case[0].store.actions()
    assert not json.loads(t['etalon_method_evaluate'](contract=contract, workspace='relative.json'))['ok']
