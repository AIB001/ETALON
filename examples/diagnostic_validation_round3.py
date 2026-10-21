"""Attach an explicitly released structure report to a COPY of a frozen campaign.

No scientific executor is called, and no parent decision or phi policy is updated.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from etalon.active import CampaignStore
from etalon.active.schema import canonical, finite
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, ResearchJournal
from etalon.research.validation import attach_decision_validation


def attach(*, database: Path, report: Path, release: Path, decision_sha256: str, output: Path) -> dict:
    # New directory only: failed attempts and archived evidence are never overwritten.
    output.mkdir(parents=True, exist_ok=False)
    original = file_identity(database)
    raw = json.loads(report.read_text(encoding='utf-8'))
    if raw['scope'] != 'retrospective_structure_only' or raw['affinity_validated'] is not False:
        raise ValueError('this adapter only admits retrospective structure validation')
    release_text = release.read_text(encoding='utf-8')
    if decision_sha256 not in release_text:
        raise ValueError('release does not name the frozen decision')
    groups = {}
    for row in raw['predictions']:
        # Group labels are execution directories, not guessed chemical identities.
        key = Path(row['path']).parent.name
        modes = groups.setdefault(key, {})
        for mode in row['modes']:
            value = mode['heavy_rmsd_angstrom']
            finite(value, 'heavy atom RMSD', minimum=0)
            modes.setdefault(mode['altloc'], []).append(value)
    if not groups:
        raise ValueError('no validation predictions')
    protocol = {key: raw[key] for key in ('method', 'manifest_sha256', 'baseline_sha256',
                                         'reference_sha256', 'matched_ca', 'producer')}
    protocol['registration_scope'] = 'released evaluator provenance; not a prospective protocol registration'
    comparison = {
        'scope': raw['scope'], 'affinity_validated': False,
        'receptor_ca_rmsd_angstrom': raw['receptor_ca_rmsd_angstrom'],
        'groups': {key: {mode: {'values_angstrom': values, 'min': min(values), 'max': max(values),
                              'slots': len(values)} for mode, values in modes.items()}
                   for key, modes in groups.items()},
        'interpretation': 'preparation-dependent structural agreement; no affinity or causal attribution',
        'uncertainty': None, 'slot_independence': False,
        'historical_verifier_cost': raw['cost'],
        'cost_scope': 'external verifier report retained; not charged again by attachment',
    }
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(output / 'research.sqlite') as target:
            source.backup(target)
    journal = ResearchJournal(CampaignStore(output / 'research.sqlite'))
    balance = journal.store.balance()
    artifacts = []
    for source, name in ((report, 'independent-structure.json'), (release, 'release.md')):
        destination = (output / name).resolve()
        destination.write_bytes(source.read_bytes())
        artifact = Artifact(str(destination), **file_identity(destination),
                            source_kind='existing_real_artifact', role='independent_validation',
                            bindings={'decision_sha256': decision_sha256})
        journal.record(artifact); artifacts.append(artifact.id)
    receipt = attach_decision_validation(journal, decision_sha256=decision_sha256,
                                        evidence=(artifacts[0],), release_evidence=artifacts[1],
                                        protocol=protocol, comparison=comparison)
    events_before = len(journal.store.events())
    assert attach_decision_validation(journal, decision_sha256=decision_sha256,
                                     evidence=(artifacts[0],), release_evidence=artifacts[1],
                                     protocol=protocol, comparison=comparison) == receipt
    assert len(journal.store.events()) == events_before
    assert journal.store.balance() == balance and file_identity(database) == original
    result = {'validation': receipt, 'original_database': str(database.resolve()),
              'original_database_identity': original, 'original_database_unchanged': True,
              'balance': balance, 'replay_added_events': 0, 'new_producer_executions': 0,
              'active_observations': len(journal.store.observations()),
              'exposure': {'reference_sha256': raw['reference_sha256'],
                           'validated_decision_sha256': decision_sha256,
                           'subsequent_use': 'seen_development_feedback_only'}}
    (output / 'attachment.json').write_text(canonical(result) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'report', 'release', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--decision-sha256', required=True)
    result = attach(**vars(parser.parse_args()))
    print(json.dumps({'validation_id': result['validation']['id'],
                      'new_producer_executions': 0, 'promotion': 'none'}))
