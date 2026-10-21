"""Audit real frozen proxy outcomes; refuse to fit chemical probabilities from one task."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from etalon.active import CampaignStore
from etalon.active.schema import canonical, digest
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, Coupling, Experiment, Observation, ResearchJournal
from etalon.research.calibration import OutcomeRule, bind_outcome


def audit(database: Path, frozen_decision: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    frozen = json.loads(frozen_decision.read_text(encoding='utf-8'))
    sha = frozen['freeze_sha256']
    if digest({k: v for k, v in frozen.items() if k != 'freeze_sha256'}) != sha:
        raise ValueError('frozen source changed')
    journal = ResearchJournal(CampaignStore(database))
    if journal.get('decision-' + sha) != frozen:
        raise ValueError('decision not in this campaign')
    before = journal.store.balance()
    artifact = Artifact(str(frozen_decision.resolve()), **file_identity(frozen_decision),
                        source_kind='existing_real_artifact', role='diagnostic',
                        bindings={'decision_sha256': sha})
    journal.record(artifact)
    outcomes = []
    for condition in frozen['preparation_conditions']:
        action = condition['wire_action_id']
        probe = 'proxy-' + action
        ex = Experiment(frozen['task_id'], 'reanalysis', {'condition_action_id': action},
                        (artifact.id,), 'descriptive-added-oxygen-pairs/1', sha + action,
                        ('mean_pairs',), Coupling((artifact.id,), (), (),
                                                 ('correlated fixed-core slots, not independent tasks',)))
        journal.record(ex)
        counts = condition['slot_counts']
        obs = Observation(ex.id, 'measured', {'mean_pairs': sum(counts)/len(counts)},
                          {'mean_pairs': 'heavy_atom_pairs_within_3.5_angstrom'}, (artifact.id,))
        journal.record(obs)
        rule = OutcomeRule(probe, ex.method_id, 'mean_pairs', obs.units['mean_pairs'],
                           1, 'below_one', 'at_least_one')
        outcomes.append(bind_outcome(journal, obs.id, rule, task_id=ex.task_id,
                                     expected_bindings={'condition_action_id': action}))
    result = {'schema': 'etalon-calibration-readiness/1', 'decision_sha256': sha,
              'observed_proxy_outcomes': outcomes,
              'rule_timing': 'post hoc development projection; not a prospectively tested selection rule',
              'independent_development_tasks': 1, 'independent_pose_replicates': 0,
              'calibration_status': 'insufficient_paired_tasks_and_decision_losses',
              'model_id': None, 'posterior': None, 'expected_gain': None,
              'next_q': None, 'phi_transfer': 'not_evaluated',
              'required_inputs': ['new frozen validation task', 'independent development task groups',
                                  'paired probe outcomes and all candidate losses under fixed J',
                                  'prospectively frozen outcome bins and capability costs'],
              'new_producer_executions': 0, 'budget_unchanged': journal.store.balance() == before}
    output.write_text(canonical(result) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'frozen-decision', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    result = audit(**vars(parser.parse_args()))
    print(json.dumps({'calibration_status': result['calibration_status'], 'model_id': None}))
