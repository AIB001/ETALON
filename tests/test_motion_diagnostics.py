"""Physical-coordinate fixtures for motion separation, PBC, and coupling."""
import numpy as np
import pytest

from etalon.rescore.motion import (factorial_contacts, minimal_reset_summary,
                                   reset_coordinates, reset_family, rigid_fit)


def test_family_shares_fit_without_aliasing_or_changing_operations(monkeypatch):
    import etalon.rescore.motion as motion
    base, ligand, core, *_ = fixture()
    changed = base.copy()
    changed[ligand] += [.4, .2, .1]
    changed[-1] += [.1, -.3, .2]
    masks = [5, 2, 7, 0, 2]
    expected = [reset_coordinates(changed, base, ligand, core, mask,
                                  return_operation=True) for mask in masks]
    calls = []
    fit = motion.rigid_fit
    def counted(*args):
        calls.append(1)
        return fit(*args)
    monkeypatch.setattr(motion, 'rigid_fit', counted)
    actual = reset_family(changed, base, ligand, core, masks, return_operations=True)
    assert len(calls) == 1
    for (coords, operation), (want, op) in zip(actual, expected):
        np.testing.assert_array_equal(coords, want)
        assert operation == op
    actual[1][0][0] = 100
    assert not np.array_equal(actual[1][0], actual[-1][0])
    np.testing.assert_array_equal(changed[0], base[0])


def fixture():
    # Three alignment atoms, donor/H, three core atoms, ligand acceptor.
    xyz = np.array([[0, 3, 0], [3, 3, 0], [0, 3, 3], [0, 0, 0],
                    [1, 0, 0], [2, 2, 0], [3, 2, 0], [2, 2, 1], [2.8, 0, 0.]])
    return xyz, [5, 6, 7, 8], [5, 6, 7], [0, 1, 2], [[3, 4, 8]], ["anchor"]


@pytest.mark.parametrize("factor", [1, 2, 4])
def test_single_factor_motion_is_separated_and_complete_reset_recovers(factor):
    base, ligand, core, align, triples, labels = fixture()
    changed = base.copy()
    ids = ligand if factor == 1 else [8] if factor == 2 else [3, 4]
    changed[ids, 0] += 2 if factor != 4 else -2
    result = factorial_contacts([base, changed], [np.eye(3)*40]*2,
                                ligand, core, align, triples, labels)
    assert not result["hit"][1, 0, 0]
    assert result["hit"][1, factor, 0]
    summary = minimal_reset_summary(result["hit"][:, :, 0])
    assert summary["minimal_mask_counts"][str(factor)] == 1
    assert summary["classes"]["unique_single"] == 1
    np.testing.assert_allclose(reset_coordinates(changed, base, ligand, core, 7), base)


def test_common_rotation_translation_and_individual_periodic_images_are_removed():
    base, ligand, core, align, triples, labels = fixture()
    r = np.array([[0., 1, 0], [-1, 0, 0], [0, 0, 1]])
    moved = base @ r+[4, 2, 3]
    moved[[4, 6, 8], 0] += 40
    result = factorial_contacts([base, moved], [np.eye(3)*40]*2,
                                ligand, core, align, triples, labels)
    assert result["hit"].all()
    assert np.max(result["max_native_error"]) < 1e-7
    assert result["metrics"]["core_translation_A"][1] < 1e-10


def test_two_small_separations_need_joint_reset():
    base, ligand, core, align, triples, labels = fixture()
    moved = base.copy()
    moved[ligand, 0] += .3
    moved[[3, 4], 0] -= .3
    result = factorial_contacts([base, moved], [np.eye(3)*40]*2,
                                ligand, core, align, triples, labels)
    summary = minimal_reset_summary(result["hit"][:, :, 0])
    assert summary["classes"]["coupled_only"] == 1
    assert summary["minimal_mask_counts"]["5"] == 1


def test_partial_rescue_is_preserved_when_reference_does_not_hold_contact():
    # Nonmonotone geometry: report possibilities rather than a causal fraction.
    hit = np.zeros((2, 8), bool)
    hit[0, 1] = True
    summary = minimal_reset_summary(hit)
    assert summary["classes"]["unique_single"] == 1
    assert summary["classes"]["unresolved_by_reference"] == 1
    assert summary["full_reset_fails_but_partial_rescues"] == 1


def test_collinear_alignment_is_rejected():
    with pytest.raises(ValueError, match="non-collinear"):
        rigid_fit(np.zeros((3, 3)), np.zeros((3, 3)))


def test_residue_endpoint_retains_alternative_frozen_triple():
    base, ligand, core, align, triples, labels = fixture()
    base = np.vstack([base, [5., 0, 0]])
    moved = base.copy()
    moved[8, 0], moved[9, 0] = 5., 2.9
    result = factorial_contacts([base, moved], [np.eye(3)*40]*2,
        [*ligand, 9], core, align, [*triples, [3, 4, 9]], [*labels, "anchor"])
    assert result["distance"][1, 0, 0] > 3
    assert result["hit"][:, 0, 0].all()
    assert minimal_reset_summary(result["hit"][:, :, 0])["lost_frames"] == 0
