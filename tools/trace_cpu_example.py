"""Observe the unmodified CPU example and run a separately labelled rejection probe.

Usage: python tools/trace_cpu_example.py --workspace runs/cpu-trace
No model, acquisition policy, or upstream scientific source is changed. The observer
adds read-only plans around each real round. The gate probe is a separate campaign.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.metadata
import io
import json
import platform
import runpy
import subprocess
import sys
from pathlib import Path

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore
from etalon.active.adapters import MOLECULAR_REPRESENTATION, molecular_candidates
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose


def summarize_state(state):
    model, choices, reason = ActiveCampaign._plan_snapshot(state)
    predictions = {}
    if model is not None:
        ids = sorted(state['candidates'])
        for endpoint in sorted(state['endpoints']):
            mean, sd = model.predict(ids, endpoint)
            predictions[endpoint] = {
                key: {'mean': float(m), 'sd': float(s)}
                for key, m, s in zip(ids, mean, sd, strict=True)
            }
    return {
        'model': model.snapshot() if model else None,
        'training_actions': model.training_action_ids if model else [],
        'reason': reason,
        'balance': state['balance'],
        'choices': [{'candidate': c.candidate_id, 'endpoint': c.endpoint_id,
                     'score': c.score, 'evidence': c.evidence} for c in choices],
        'predictions': predictions,
    }


def artifacts(observation):
    provenance = observation['result']['provenance']
    if 'run' not in provenance:
        return []
    screen = Screen(provenance['workspace'])
    output = []
    for stage in provenance['run']['stages']:
        artifact_id = stage.get('artifact_id')
        if not artifact_id:
            continue
        contracts = {}
        for contract in ('parent/v1', 'property/v1', 'source_record/v1', 'decision/v1'):
            try:
                contracts[contract] = screen.read(artifact_id, contract_id=contract)
            except KeyError:
                pass
        output.append({'stage': stage, 'contracts': contracts})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    root.mkdir(parents=True, exist_ok=False)
    trace = []
    original = ActiveCampaign.run_round

    def observed_round(campaign):
        before = summarize_state(campaign.store.snapshot())
        outcome = original(campaign)
        after_state = campaign.store.snapshot()
        after = summarize_state(after_state)
        action_ids = set(outcome['actions'])
        observations = [o for o in campaign.store.observations() if o['action_id'] in action_ids]
        item = {'before': before, 'outcome': outcome, 'observations': observations, 'after': after}
        if any(o['admitted'] and o['result']['candidate_id'] == 'ethanol' for o in after_state['observations']):
            counterfactual = copy.deepcopy(after_state)
            # Offline diagnostic only. Keep actual attempts and costs unchanged, and
            # withhold ethanol labels from fitting/reliability. Never write this state.
            for o in counterfactual['observations']:
                if o['result']['candidate_id'] == 'ethanol':
                    o['admitted'] = False
            item['offline_without_ethanol_labels'] = summarize_state(counterfactual)
        trace.append(item)
        (root / 'round-trace.json').write_text(json.dumps(trace, indent=2), encoding='utf-8')
        return outcome

    ActiveCampaign.run_round = observed_round
    previous_argv = sys.argv
    captured = io.StringIO()
    try:
        sys.argv = ['component_learning.py', '--workspace', str(root / 'components')]
        with contextlib.redirect_stdout(captured):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'examples/component_learning.py'), run_name='__main__')
    finally:
        ActiveCampaign.run_round = original
        sys.argv = previous_argv
    (root / 'example-output.json').write_text(captured.getvalue(), encoding='utf-8')
    store = CampaignStore(root / 'components/campaign.sqlite')
    assert len(store.observations(admitted_only=True)) == 16, 'Example exited without 16 admitted CPU observations'
    ethanol = [o for o in store.observations() if o['result']['candidate_id'] == 'ethanol']
    (root / 'ethanol-artifacts.json').write_text(json.dumps([
        {'observation': o, 'artifacts': artifacts(o)} for o in ethanol
    ], indent=2), encoding='utf-8')

    # Explicit counterexample: an early valid molecular-weight readout followed
    # by a caller-authored MW >= 50 Da gate. This is NOT the official example.
    config = compose('ethanol-rejection-probe', [
        {'id': 'measure', 'title': 'Compute molecular weight', 'criteria': [
            component('properties', 'features.rdkit_properties@0.1.0')]},
        {'id': 'selection', 'title': 'Require MW at least 50 Da', 'criteria': [
            component('mw_gate', 'chemistry.rdkit_property_range_gate@0.1.0', settings={'mw_min': 50})]},
    ])
    recipe = CascadeRecipe.freeze(config, Readout('properties', 'property/v1', 'mw'))
    endpoint = recipe.endpoint('mw-gated', target='diagnostic-only', quantity='molecular_weight', units='Da', cost=1)
    probe_store = CampaignStore(root / 'rejection/campaign.sqlite')
    probe_store.configure(CampaignSpec(endpoint.id, 2, 'demonstration_quotes', MOLECULAR_REPRESENTATION, batch_size=1), [endpoint])
    candidates, rejected = molecular_candidates({'ethanol': 'CCO'})
    assert not rejected
    probe_store.add_candidates(candidates)
    probe = ActiveCampaign(probe_store, CascadeExecutor(root / 'rejection/calculations', {endpoint.id: recipe}))
    probe.run_round()
    observation, = probe_store.observations()
    probe_after = summarize_state(probe_store.snapshot())
    assert observation['admitted'] is False
    assert observation['result']['provenance']['failure_code'] == 'CANDIDATE_FILTERED'
    assert probe_after['model']['training_size'] == 0
    assert probe_store.balance()['spent'] == 1
    (root / 'rejection-probe.json').write_text(json.dumps({
        'description': 'Separate real CPU run with an explicit MW >= 50 Da gate',
        'observation': observation, 'artifacts': artifacts(observation), 'after': probe_after,
    }, indent=2), encoding='utf-8')
    metadata = {
        'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'python': sys.version, 'platform': platform.platform(),
        'packages': {name: importlib.metadata.version(name) for name in
                     ('etalon', 'rdkit', 'numpy', 'scipy', 'pydantic', 'pyarrow')},
        'official_state': store.status(), 'actions': store.actions(),
        'feature_width': len(next(iter(store.candidates().values())).features),
        'limits': ['CPU properties only; no docking, MD, FEP, LLM, or bioactivity validation',
                   'MolWt and MolLogP are already input descriptors; this is an integration demonstration',
                   'Costs are declared demonstration quotes, not measured money or CPU time'],
    }
    patch = subprocess.check_output(['git', 'diff', '--', 'asset/molcascade/src/molcascade'])
    (root / 'local-windows-compat.patch').write_bytes(patch)
    metadata['local_windows_compat_patch_sha256'] = hashlib.sha256(patch).hexdigest()
    metadata['upstream_assets_modified'] = bool(patch)
    (root / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps({'workspace': str(root), 'state': store.status(),
                      'ethanol_observations': len(ethanol), 'rejection_probe_verified': True}, indent=2))


if __name__ == '__main__':
    main()
