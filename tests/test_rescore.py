"""A learned rescorer over real poses, and the half of it the GPU actually accelerates.

findings/0006 prices rescoring as the largest lever a campaign has, and the obvious reading -- that a
rescorer is a GPU inference job -- is wrong for this architecture. The model is 39,041 parameters and
runs at millions of poses per second on either device; the featuriser runs at 927 per second on one
core. Most of this file is about that inversion and about the two correctness traps under it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.rescore.features import (
    ELEMENTS,
    SHELLS,
    Pose,
    feature_names,
    featurize_pose,
    featurize_poses,
    featurize_poses_on_gpu,
    load_receptor,
    radius_for,
    read_pose,
)
from etalon.rescore.model import MIN_SAMPLES_PER_FEATURE, Rescorer, resolve_device

#: A two-atom receptor written out as a PDB, so the geometry of a feature is checkable by hand.
_RECEPTOR_PDB = """\
ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C
ATOM      2  O   ALA A   1       0.000   0.000   3.500  1.00  0.00           O
ATOM      3  H   ALA A   1       0.000   0.000   1.000  1.00  0.00           H
END
"""


def _cuda_present() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def _torch_present() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


#: Without CUDA, ``featurize_poses_on_gpu`` returns ``featurize_poses(...)`` at its second line, so a
#: test comparing the two paths compares NumPy with NumPy and passes for the wrong reason. It used to
#: say "skipped without CUDA" in its docstring and carry no marker, which is how a tautology sat in
#: the suite advertising itself as a parity check.
requires_cuda = pytest.mark.skipif(
    not _cuda_present(), reason="no CUDA device: the GPU entry point falls through to NumPy"
)
requires_torch = pytest.mark.skipif(not _torch_present(), reason="torch is not installed")


@pytest.fixture
def receptor(tmp_path: Path):
    path = tmp_path / "receptor.pdb"
    path.write_text(_RECEPTOR_PDB, encoding="utf-8")
    return load_receptor(path)


def _pose(points: list[tuple[float, float, float]], elements: list[str], name: str = "p") -> Pose:
    from etalon.rescore.features import _element_slot

    return Pose(
        coordinates=np.asarray(points, dtype=np.float32),
        element_index=np.asarray([_element_slot(e) for e in elements], dtype=np.int64),
        parent_id=name,
        atoms=len(points),
    )


# -- the featurisation ----------------------------------------------------


def test_hydrogens_are_not_counted(receptor) -> None:
    """A docked pose's hydrogens were placed by whatever added them."""

    assert receptor.atoms == 2


def test_a_contact_lands_in_the_shell_its_distance_names(receptor) -> None:
    """One ligand carbon 3.5 A from a protein carbon: one count in the 3-4 A shell."""

    features = featurize_pose(_pose([(0.0, 0.0, 3.5)], ["C"]), receptor)
    names = feature_names()
    by_name = dict(zip(names, features, strict=True))

    assert by_name["C-C@3-4A"] == 1.0
    # And the same atom is 0 A from the protein oxygen, which is below the first shell edge and
    # therefore a clash rather than a contact: dropped here, caught by a validity check.
    assert by_name["C-O@2-3A"] == 0.0
    assert features.sum() == 1.0


def test_an_element_outside_the_table_is_pooled_not_dropped(receptor) -> None:
    """A silently ignored selenium is a feature vector describing a different molecule."""

    features = featurize_pose(_pose([(0.0, 0.0, 3.5)], ["Se"]), receptor)
    by_name = dict(zip(feature_names(), features, strict=True))

    assert by_name["other-C@3-4A"] == 1.0
    assert "Se" not in ELEMENTS


def test_the_first_shell_starts_above_zero() -> None:
    """Anything closer is a clash, and a clash belongs in a validity check rather than a score."""

    assert SHELLS[0] == 2.0


# -- the radius trap ------------------------------------------------------


def test_truncating_the_receptor_at_too_small_a_radius_changes_the_features(tmp_path: Path) -> None:
    """The trap, measured: an 18 A radius against a 12 A outer shell changed counts by up to 17.

    Atoms beyond the last shell *from a ligand atom* contribute nothing. Atoms beyond it from the
    pocket *centre* do not, because a ligand atom near the edge reaches further.
    """

    lines = [
        f"ATOM  {index:5d}  CA  ALA A{index:4d}    {0.0:8.3f}{0.0:8.3f}{z:8.3f}"
        "  1.00  0.00           C"
        for index, z in enumerate(np.arange(0.0, 30.0, 1.0), start=1)
    ]
    path = tmp_path / "rod.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")

    pose = _pose([(0.0, 0.0, 10.0)], ["C"])
    centre = (0.0, 0.0, 0.0)

    full = featurize_pose(pose, load_receptor(path))
    lossless = featurize_pose(pose, load_receptor(path, pocket_centre=centre, radius=radius_for([pose], centre)))
    truncated = featurize_pose(pose, load_receptor(path, pocket_centre=centre, radius=12.0))

    assert np.abs(lossless - full).max() == 0.0
    assert np.abs(truncated - full).max() > 0.0


def test_the_lossless_radius_covers_the_ligand_plus_the_outer_shell() -> None:
    pose = _pose([(0.0, 0.0, 9.0)], ["C"])

    assert radius_for([pose], (0.0, 0.0, 0.0)) >= 9.0 + SHELLS[-1]


def test_an_empty_receptor_is_refused_rather_than_scored(tmp_path: Path) -> None:
    """An all-zero feature vector is something a model scores without complaint."""

    path = tmp_path / "empty.pdb"
    path.write_text("END\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no heavy atoms"):
        load_receptor(path)


def test_a_pocket_centre_with_nothing_near_it_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "receptor.pdb"
    path.write_text(_RECEPTOR_PDB, encoding="utf-8")

    with pytest.raises(ValueError, match="within"):
        load_receptor(path, pocket_centre=(500.0, 500.0, 500.0), radius=5.0)


def test_an_unparseable_molblock_returns_none_rather_than_zeros() -> None:
    """A zero vector standing in for a failed parse is a pose a model scores confidently."""

    assert read_pose("this is not a molblock") is None


# -- the two paths agree --------------------------------------------------


@requires_cuda
def test_the_gpu_path_is_bit_identical_to_the_numpy_one(receptor) -> None:
    """Verified on real poses at every size tested: maximum difference zero."""

    poses = [
        _pose([(0.0, 0.0, 3.5), (1.0, 0.0, 4.2)], ["C", "N"], "a"),
        _pose([(0.0, 0.5, 2.5)], ["O"], "b"),
        _pose([(0.0, 0.0, 7.0), (0.0, 1.0, 7.0), (1.0, 1.0, 7.0)], ["C", "C", "S"], "c"),
    ]

    numpy_features, numpy_ids = featurize_poses(poses, receptor)
    gpu_features, gpu_ids = featurize_poses_on_gpu(poses, receptor)

    assert numpy_ids == gpu_ids == ("a", "b", "c")
    assert np.abs(numpy_features - gpu_features).max() == 0.0


@requires_torch
def test_the_two_libraries_bin_a_shell_edge_the_same_way() -> None:
    """The invariant behind the parity claim, pinned without needing a GPU.

    ``np.digitize`` and ``torch.bucketize`` spell their convention in opposite directions: numpy's
    ``right=False`` is ``bins[i-1] <= x < bins[i]``, torch's ``right=False`` is
    ``boundaries[i-1] < x <= boundaries[i]``. The GPU featuriser took torch's default and so put
    every distance landing exactly on a shell edge one shell lower, and kept a contact at exactly
    12 A that numpy dropped.

    It never showed up in the parity test because float32 coordinates make an exact hit rare and
    that test used distances that were not on an edge. This one is the edges and nothing else, and
    it runs on CPU -- ``bucketize`` needs no device -- so the convention stays pinned in a suite that
    has no GPU.
    """

    import torch

    edges = np.asarray(SHELLS, dtype=np.float32)
    on_edge = np.asarray([*SHELLS, 1.9, 3.5, 12.1], dtype=np.float32)

    numpy_shell = np.digitize(on_edge, SHELLS) - 1
    torch_shell = (
        torch.bucketize(
            torch.as_tensor(on_edge), torch.as_tensor(edges), right=True
        ).numpy()
        - 1
    )

    assert np.array_equal(numpy_shell, torch_shell)
    # And the wrong spelling really does disagree, so this test fails if the fix is reverted.
    wrong = torch.bucketize(torch.as_tensor(on_edge), torch.as_tensor(edges)).numpy() - 1
    assert not np.array_equal(numpy_shell, wrong)


@requires_cuda
def test_a_contact_exactly_on_a_shell_edge_lands_in_the_same_shell(receptor) -> None:
    """The regression the convention mismatch would have produced, end to end.

    The receptor's first heavy atom sits at the origin, so a ligand atom at ``(0, 0, d)`` is exactly
    ``d`` away from it. Every shell edge is tried.
    """

    poses = [_pose([(0.0, 0.0, float(edge))], ["C"], f"edge-{edge:g}") for edge in SHELLS]

    numpy_features, _ = featurize_poses(poses, receptor)
    gpu_features, _ = featurize_poses_on_gpu(poses, receptor)

    assert np.abs(numpy_features - gpu_features).max() == 0.0


@requires_cuda
def test_poses_of_different_lengths_are_padded_without_leaking(receptor) -> None:
    """The padding sits far from the protein and is masked; a pad must contribute nothing."""

    short = _pose([(0.0, 0.0, 3.5)], ["C"], "short")
    long = _pose([(0.0, 0.0, 3.5)] + [(50.0, 50.0, 50.0)] * 9, ["C"] + ["C"] * 9, "long")

    batched, _ = featurize_poses_on_gpu([short, long], receptor)
    alone, _ = featurize_poses_on_gpu([short], receptor)

    assert np.abs(batched[0] - alone[0]).max() == 0.0
    assert batched[0].sum() == batched[1].sum() == 1.0


# -- the model ------------------------------------------------------------


def test_fitting_is_refused_with_fewer_complexes_than_features() -> None:
    """28 samples against 87 features reproduces any labels and predicts the mean out of fold."""

    rng = np.random.default_rng(0)
    features = rng.normal(0, 1, (28, 87)).astype(np.float32)

    with pytest.raises(ValueError, match="not enough to fit anything"):
        Rescorer().fit(features, rng.normal(0, 1, 28), drop_empty_columns=False)
    assert MIN_SAMPLES_PER_FEATURE == 1.0


def test_empty_columns_are_dropped_and_the_kept_set_is_recorded() -> None:
    """600 columns down to under a hundred on real poses, which is the difference from a refusal."""

    rng = np.random.default_rng(1)
    features = np.zeros((400, 600), dtype=np.float32)
    features[:, :40] = rng.normal(0, 1, (400, 40))

    model = Rescorer(epochs=5).fit(features, rng.normal(0, 1, 400))

    assert model.kept_columns == tuple(range(40))


def test_a_mismatched_column_count_is_refused_rather_than_reshaped() -> None:
    rng = np.random.default_rng(2)
    features = rng.normal(0, 1, (200, 40)).astype(np.float32)
    model = Rescorer(epochs=5).fit(features, rng.normal(0, 1, 200), drop_empty_columns=False)

    with pytest.raises(ValueError, match="Refused rather than reshaped"):
        model.predict(rng.normal(0, 1, (5, 17)).astype(np.float32))


def test_mismatched_labels_are_refused() -> None:
    rng = np.random.default_rng(3)

    with pytest.raises(ValueError, match="silent truncation"):
        Rescorer().fit(rng.normal(0, 1, (200, 40)).astype(np.float32), rng.normal(0, 1, 7))


def test_throughput_reports_the_device_it_actually_used() -> None:
    """A rescorer quietly on the CPU has a throughput figure wrong by two orders of magnitude."""

    rng = np.random.default_rng(4)
    features = rng.normal(0, 1, (2000, 40)).astype(np.float32)
    model = Rescorer(epochs=5).fit(features, rng.normal(0, 1, 2000), drop_empty_columns=False)

    measured = model.measure_throughput(features, repeats=1)

    assert measured.device == resolve_device()
    assert measured.per_second > 0
    assert measured.as_dict()["hours_for_750k"] >= 0


def test_the_stage_it_produces_refuses_to_invent_a_correlation() -> None:
    """The planner refuses a ranking stage with no measured correlation, which is correct."""

    rng = np.random.default_rng(5)
    features = rng.normal(0, 1, (2000, 40)).astype(np.float32)
    model = Rescorer(epochs=5).fit(features, rng.normal(0, 1, 2000), drop_empty_columns=False)
    measured = model.measure_throughput(features, repeats=1)

    stage = model.as_stage(measured)
    with_number = model.as_stage(measured, spearman=0.51)

    assert stage.spearman is None
    assert "has not been measured" in stage.source
    assert with_number.spearman == 0.51
    # And its cost is the measured throughput rather than a guess.
    assert stage.gpu_hours == measured.gpu_hours_per_molecule
    assert "correlated with docking's" in stage.systematic_caveat
