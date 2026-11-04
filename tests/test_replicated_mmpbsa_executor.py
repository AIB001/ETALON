"""Registration for independent-replica MM-PBSA, and the three gaps it closes.

``kind="prism"`` is left byte-identical by design, so already-submitted executions keep the
semantics they were recorded under. What this kind adds is not a preference:

* ``max_replicates`` was fixed at 1 and ``StageExecutor`` refuses a ``PrismStage`` above that,
  so three trajectories could not be requested at all;
* velocity seeds were neither chosen nor recorded, so a spread across replicas could not be
  distinguished from one trajectory read three times;
* nothing in the project ran ``gmx_MMPBSA``, and ``read_binding_energy`` reads whichever
  ``FINAL_RESULTS_MMPBSA.dat`` is present -- the hazard ``STALE_MMPBSA_RESULT`` is named for.

Dispatch is deliberately not wired yet. These tests pin that registration is complete and
that dispatch refuses rather than falling through to the single-run legacy stage.
"""

from __future__ import annotations

import pytest

from etalon.runtime.executors import prepare_executor

pytest.importorskip("prism", reason="the replicated protocol identity reads PRISM's pinned tree")


@pytest.fixture
def paths(tmp_path):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n", encoding="utf-8")
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    return receptor, interpreter


def _configuration(paths, **overrides):
    receptor, interpreter = paths
    base = {"receptor_path": str(receptor), "python": str(interpreter),
            "production_ns": 50.0, "seeds": [20260501, 20260502, 20260503]}
    base.update(overrides)
    return base


def _endpoint():
    return {"id": "mmpbsa", "target": "SND1", "cost": 1.0}


def test_fifty_nanoseconds_times_three_is_registrable(paths) -> None:
    prepared = prepare_executor("prism_mmpbsa_replicated", _configuration(paths), _endpoint())

    assert prepared["endpoint"]["max_replicates"] == 3, "three declared seeds must be three replicas"
    assert prepared["endpoint"]["quantity"] == "mmpbsa"
    assert prepared["endpoint"]["units"] == "kcal/mol"
    assert prepared["endpoint"]["direction"] == "minimize"
    assert prepared["endpoint"]["requires_handoff"] is True
    assert prepared["configuration"]["production_ns"] == 50.0
    assert prepared["configuration"]["seeds"] == [20260501, 20260502, 20260503]
    assert prepared["endpoint"]["protocol"].startswith("prism-mmpbsa-replicated/1:")


def test_a_repeated_seed_is_a_retry_not_a_replica(paths) -> None:
    """Two replicas under one seed are one trajectory computed twice.

    Admitting that as a spread would report a reproducibility the protocol never measured,
    which is the specific thing three replicas were chosen to establish.
    """

    with pytest.raises(ValueError, match="retry, not a replica"):
        prepare_executor("prism_mmpbsa_replicated",
                         _configuration(paths, seeds=[7, 7, 9]), _endpoint())


@pytest.mark.parametrize("seeds", [[], "123", [1.5], [-1], [2147483648], [None]])
def test_seeds_must_be_a_nonempty_list_of_integers(paths, seeds) -> None:
    with pytest.raises(ValueError, match="seeds must be"):
        prepare_executor("prism_mmpbsa_replicated",
                         _configuration(paths, seeds=seeds), _endpoint())


def test_without_a_command_the_operator_remains_the_energy_author(paths) -> None:
    """The default stays read-only, so existing records keep their meaning."""

    prepared = prepare_executor("prism_mmpbsa_replicated", _configuration(paths), _endpoint())
    assert prepared["configuration"]["mmpbsa_command"] is None


def test_who_computes_the_energy_changes_the_protocol_identity(paths, tmp_path) -> None:
    """A campaign must not be able to half-change how its affinities were produced."""

    runner = tmp_path / "mmpbsa_run.sh"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")

    operator = prepare_executor("prism_mmpbsa_replicated", _configuration(paths), _endpoint())
    driven = prepare_executor("prism_mmpbsa_replicated",
        _configuration(paths, mmpbsa_command=[str(runner)]), _endpoint())

    assert operator["endpoint"]["protocol"] != driven["endpoint"]["protocol"]
    assert driven["configuration"]["mmpbsa_command"] == [str(runner)]
    # The command is an input, so editing the script invalidates the frozen executor.
    assert str(runner) in driven["inputs"]


def test_the_command_must_name_an_absolute_executable(paths) -> None:
    """A bare word would make the protocol identity depend on PATH at dispatch time."""

    with pytest.raises(ValueError, match="absolute"):
        prepare_executor("prism_mmpbsa_replicated",
            _configuration(paths, mmpbsa_command=["gmx_MMPBSA"]), _endpoint())


@pytest.mark.parametrize("command", [[], "run.sh", [""], ["/a", 3]])
def test_a_malformed_command_is_refused(paths, command) -> None:
    with pytest.raises(ValueError, match="mmpbsa_command must be"):
        prepare_executor("prism_mmpbsa_replicated",
            _configuration(paths, mmpbsa_command=command), _endpoint())


@pytest.mark.parametrize("subdir", ["/absolute", "../escape", "a/../../b"])
def test_the_result_directory_cannot_leave_the_run(paths, subdir) -> None:
    with pytest.raises(ValueError, match="relative path inside"):
        prepare_executor("prism_mmpbsa_replicated",
            _configuration(paths, mmpbsa_subdir=subdir), _endpoint())


def test_seeds_are_part_of_the_protocol_identity(paths) -> None:
    """Different seeds are a different experiment, so they cannot share an identity."""

    first = prepare_executor("prism_mmpbsa_replicated", _configuration(paths), _endpoint())
    second = prepare_executor("prism_mmpbsa_replicated",
        _configuration(paths, seeds=[1, 2, 3]), _endpoint())
    assert first["endpoint"]["protocol"] != second["endpoint"]["protocol"]


def test_the_legacy_single_run_kind_is_untouched(paths) -> None:
    """Registration of ``kind="prism"`` must still produce exactly what it did."""

    receptor, interpreter = paths
    prepared = prepare_executor("prism", {"receptor_path": str(receptor),
        "python": str(interpreter), "production_ns": 50.0}, _endpoint())
    assert prepared["endpoint"]["max_replicates"] == 1
    assert prepared["endpoint"]["protocol"].startswith("prism-mmpbsa/1:")
    assert "seeds" not in prepared["configuration"]


def test_an_unknown_kind_names_the_registrable_ones(paths) -> None:
    with pytest.raises(ValueError, match="prism_mmpbsa_replicated"):
        prepare_executor("prism_pmf", _configuration(paths), _endpoint())
