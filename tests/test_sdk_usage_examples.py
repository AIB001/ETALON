"""Run product reference examples against real SDK, including useful counterexamples."""
from pathlib import Path
import re

import pytest


REFERENCES = Path(__file__).parents[1] / '.claude/skills/etalon-campaign/references'


def run_example(filename, marker):
    text = (REFERENCES / filename).read_text(encoding='utf-8')
    pattern = rf'<!-- runnable-example: {re.escape(marker)} -->\s*```python\n(.*?)\n```'
    snippets = re.findall(pattern, text, flags=re.DOTALL)
    assert len(snippets) == 1, 'one complete runnable example required for this marker'
    namespace = {'__name__': '__sdk_reference_example__'}
    exec(compile(snippets[0], str(REFERENCES / filename), 'exec'), namespace)
    return namespace


def test_finite_reference_constructs_roundtrips_and_stops_on_real_unsupported_feedback():
    scope = run_example('finite-diagnostics.md', 'finite-diagnostics')
    assert scope['restored'].id == scope['model'].id
    assert scope['advice']['program']['kind'] == 'query'
    assert scope['advice']['program']['quoted_incremental_cost'] == pytest.approx(.1)
    assert scope['support']['status'] == 'model_challenge' and scope['support']['posterior'] == {}
    assert scope['next_choice'] == 'stop_inference' and scope['following_advice'] is None
    assert scope['summary']['actual_execution_cost'] is None
    assert scope['summary']['local_processing_cost'] is None
    exact = scope['exact_program']
    # Same model under insufficient budget stops; the snippet does not invent funds.
    assert exact(scope['model'], budget=.09, max_depth=1, cost_weight=1)['program']['kind'] == 'stop'
    # Known feedback instead permits a useful terminal decision, without another query.
    result = exact(scope['model'], observed={'q': 'high'}, budget=0, max_depth=0, cost_weight=1)
    assert result['program']['kind'] == 'stop' and result['program']['decision'] == 'change'
    with pytest.raises(ValueError, match='unknown probe'):
        scope['condition'](scope['model'], {'different_probe': 'low'})


def test_paired_reference_can_run_without_journal_and_rejects_false_independence():
    scope = run_example('paired-models-and-segments.md', 'paired-models')
    assert scope['summary']['decision'] == 'keep'
    assert scope['summary']['suggested_kind'] == 'stop'
    assert scope['summary']['evidence_admitted'] is False
    assert scope['summary']['heldout_gain'] is None
    assert scope['summary']['actual_execution_cost'] is None
    inputs = scope['model_inputs']
    rows = inputs['rows']
    correlated = [rows[0], dict(rows[1], independence_group=rows[0]['independence_group'])]
    with pytest.raises(ValueError, match='correlated'):
        scope['empirical_model'](**(inputs | {'rows': correlated}))
    with pytest.raises(ValueError, match='disjoint'):
        scope['empirical_model'](**(inputs | {'heldout_tasks': ('dev-a',)}))
    unsupported = scope['condition'](scope['model'], {'q': 'not_evaluated'})
    assert unsupported['status'] == 'model_challenge' and unsupported['posterior'] == {}
