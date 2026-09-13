"""The network a relative method needs, and what a diverse shortlist does to it.

FEP scores differences, so a set of molecules is not an input to it. Handing a relative method a
shortlist produces numbers rather than an error message, which is the failure this module exists to
turn into a refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.fep.network import MIN_CORE_FRACTION, Edge, design, mapping_quality, observations

#: A constructed congeneric series: one scaffold, single-substituent differences. What FEP is for.
SERIES = {
    "ref": "c1ccc(Nc2nccc(-c3cccnc3)n2)cc1",
    "methyl": "Cc1ccc(Nc2nccc(-c3cccnc3)n2)cc1",
    "ethyl": "CCc1ccc(Nc2nccc(-c3cccnc3)n2)cc1",
    "chloro": "Clc1ccc(Nc2nccc(-c3cccnc3)n2)cc1",
}

#: Three unrelated chemotypes. What a diversity-selecting funnel delivers.
DIVERSE = {
    "indolinone": "O=C1Nc2ccccc2C1=Cc1ccc[nH]1",
    "thienopyrimidine": "c1ccc(-c2cc3ncncc3s2)cc1",
    "piperazine_amide": "O=C(c1ccc(/C=C/c2n[nH]c3ccccc23)cc1)N1CCNCC1",
}


# -- the mapping observable -----------------------------------------------


def test_a_congeneric_pair_maps_through_most_of_the_molecule() -> None:
    edges = {(e.left, e.right): e for e in mapping_quality(SERIES)}
    pair = edges.get(("ref", "methyl")) or edges[("methyl", "ref")]

    assert pair.core_fraction > 0.9
    assert pair.perturbed_atoms <= 2
    assert pair.usable


def test_unrelated_chemotypes_do_not_map() -> None:
    """The case PRISM's Cartesian mapper constructs without complaint."""

    for edge in mapping_quality(DIVERSE):
        assert edge.core_fraction < MIN_CORE_FRACTION, (edge.left, edge.right, edge.core_fraction)
        assert not edge.usable


def test_the_core_fraction_is_against_the_larger_molecule() -> None:
    """Against the smaller one, growing a fragment into a drug would look like a perfect edge."""

    edge = Edge("small", "large", core_atoms=10, left_heavy=10, right_heavy=40)

    assert edge.core_fraction == 0.25
    assert edge.perturbed_atoms == 30
    assert not edge.usable


# -- the network ----------------------------------------------------------


def test_a_series_yields_a_spanning_tree_with_no_error_estimate() -> None:
    network = design(SERIES, references=("ref",))

    assert network.edge_count == len(SERIES) - 1
    assert network.cycles == 0
    assert network.unreachable == ()
    assert any("closes no cycles" in note for note in network.notes)


def test_each_extra_edge_buys_one_hysteresis_check() -> None:
    """The only error estimate in this pipeline that is a measurement rather than an assumption."""

    forest = design(SERIES, references=("ref",), cycle_edges=0)
    with_cycles = design(SERIES, references=("ref",), cycle_edges=2)

    assert with_cycles.cycles == 2
    assert with_cycles.edge_count == forest.edge_count + 2
    assert any("hysteresis checks" in note for note in with_cycles.notes)


def test_a_diverse_shortlist_leaves_molecules_unreachable() -> None:
    """What a funnel selecting for diversity and scaffold novelty delivers to a relative method."""

    network = design(DIVERSE, references=("indolinone",))

    assert set(network.unreachable) == set(DIVERSE)
    assert network.components == ()
    assert any("no usable edge to anything" in note for note in network.notes)
    assert any("selected for diversity" in note for note in network.notes)


def test_a_component_with_no_reference_yields_differences_only() -> None:
    molecules = {**SERIES, **DIVERSE}
    # No reference inside the series component.
    network = design(molecules, references=("indolinone",))

    assert network.unanchored
    assert any("no reference compound" in note for note in network.notes)


def test_the_rejected_pairs_are_counted_and_explained() -> None:
    network = design({**SERIES, **DIVERSE}, references=("ref",))
    note = next(n for n in network.notes if "candidate pairs map through a common core below" in n)

    assert "Cartesian distance" in note
    assert "no quality gate" in note


def test_a_stricter_threshold_rejects_more() -> None:
    lenient = design(SERIES, references=("ref",), min_core_fraction=0.5)
    strict = design(SERIES, references=("ref",), min_core_fraction=0.99)

    assert strict.edge_count < lenient.edge_count


# -- the fault it makes computable ----------------------------------------


def test_the_mapping_fault_becomes_evaluable_on_a_designed_network() -> None:
    """It was unevaluable everywhere: a mapping is a property of an edge, not of a record."""

    network = design({**SERIES, **DIVERSE}, references=("ref",))
    observed = observations(network, list(SERIES) + list(DIVERSE))
    fired = {o.code for o in observed if o.fired}

    assert all(o.code == "F_FEP_MAPPING_DEGENERATE" for o in observed)
    assert all(o.evaluable for o in observed)
    assert fired == {"F_FEP_MAPPING_DEGENERATE"}
    in_series = next(o for o in observed if o.detail.startswith("present"))
    assert "usable edge" in in_series.detail


def test_an_unparseable_molecule_is_simply_absent_rather_than_crashing() -> None:
    network = design({**SERIES, "broken": "not a molecule"}, references=("ref",))

    assert "broken" in network.unreachable
    assert network.edge_count == len(SERIES) - 1


def test_an_empty_input_designs_an_empty_network() -> None:
    network = design({})

    assert network.edge_count == 0
    assert network.components == ()
    with pytest.raises(StopIteration):
        next(iter(network.unreachable))


def test_the_fep_command_signals_unreachable_molecules(tmp_path: Path) -> None:
    """Exit 1 so a pipeline script notices before committing GPU-days to a relative calculation."""

    import csv

    from etalon.__main__ import main

    path = tmp_path / "library.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "smiles"])
        for name, smiles in {**SERIES, **DIVERSE}.items():
            writer.writerow([name, smiles])

    # The diverse chemotypes cannot be reached, so this signals.
    assert main(["fep", str(path), "--reference", "ref"]) == 1
    # A threshold low enough to relate everything does not.
    assert main(["fep", str(path), "--reference", "ref", "--core", "0.01"]) == 0


def test_the_fep_command_refuses_a_library_it_cannot_read(tmp_path: Path) -> None:
    from etalon.__main__ import main

    path = tmp_path / "empty.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")

    assert main(["fep", str(path)]) == 2
