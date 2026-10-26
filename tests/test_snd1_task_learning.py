import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('snd1_task_learning', Path(__file__).parents[1] / 'examples/snd1_task_learning.py')
method = importlib.util.module_from_spec(spec)
spec.loader.exec_module(method)


def test_population_weights_preserve_rare_state_occupancy():
    states = np.r_[np.zeros(990, dtype=int), np.ones(10, dtype=int)]
    indices, weights, record = method.select_frames(states, total=96)
    assert len(indices) == len(set(indices)) == 96
    assert np.sum(weights) == pytest.approx(1)
    assert np.dot(weights, states[indices]) == pytest.approx(.01)
    assert record['allocation'][1] == 10


def test_variance_allocation_never_spends_on_empty_state():
    counts = [0, 4, 500, 3, 0, 600]
    allocation = method.allocate(counts, 96, [.25, .25, .05, 0, 4, .01])
    assert allocation.sum() == 96
    assert np.all(allocation <= counts)
    assert allocation[0] == allocation[4] == 0
    assert allocation[2] > allocation[5]


def test_comparison_has_identical_nonzero_reference_denominator():
    reference = dict(residue_occupancy=[.01, 0, .4], any_occupancy=.4, prepared_pocket_occupancy=.2)
    estimate = dict(residue_occupancy=[0, .2, .2], any_occupancy=.3, prepared_pocket_occupancy=.1)
    result = method.comparison(reference, estimate)
    assert result['eligible_residue_count'] == 2
    assert result['mean_absolute_residue_occupancy_error'] == pytest.approx(.105)
    assert result['persistent_motif_recall'] == 1


def test_topology_bonds_are_explicit(tmp_path):
    path = tmp_path / 'molecule.itp'
    path.write_text('[ atoms ]\n1 N 1 MOL N1 1 -0.2 14.0\n2 H 1 MOL H1 2 0.2 1.0\n[ bonds ]\n1 2 1\n')
    atoms, bonds = method.topology_sections(path)
    assert len(atoms) == 2
    assert bonds == [(0, 1)]


def test_exact_bound_keeps_boundary_and_missing_residues():
    keep = method.exact_candidate_mask(['A', 'B', 'C', 'missing'], ['A', 'B', 'C'],
                                       [[.2, .35009, .36], [.5, .9, .4]])
    assert keep.tolist() == [True, True, False, True]


def test_unknown_or_invalid_distance_cannot_exclude_a_triple():
    keep = method.exact_candidate_mask(['A', 'B', 'C'], ['A', 'B', 'C'],
                                       [[np.nan, np.inf, -.1], [.5, .9, .4]])
    assert keep.tolist() == [True, True, True]
