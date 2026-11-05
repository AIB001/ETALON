"""A computed MM-PBSA energy must not be read as a missing one.

``read_binding_energy`` understood exactly one layout: ``DELTA TOTAL = -35.2 +/- 2.1``.
Current gmx_MMPBSA writes a fixed-width table with no ``=`` and no ``+/-``, so the pattern
matched nothing and the function returned ``None`` -- the same answer it gives when the
calculation has not run. Downstream that is not a cosmetic difference: the admissibility
layer withholds a measurement with no expensive value and records the reason, so a finished
calculation would have been filed as one that produced no result.

The fixture here is not synthetic. It is the 6,837-byte output of this project's own
pipeline: 1,000 BRICS-recombined ligands screened through MolCascade against 1IEP, the
best redock-consistent pose built with amber14sb/GAFF2/AM1-BCC, 0.5 ns of production MD,
then gmx_MMPBSA driven by ``run_mmpbsa``. The ``=`` reader scored zero matches on it.

What these tests do not establish: that the energy is meaningful. That run saved two frames,
so every deviation column in the fixture is 0.00 and the total is a single-frame number.
Parsing it correctly and trusting it are different claims.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest

from etalon.boundary.simulate import read_binding_energy

FIXTURE = Path(__file__).parent / "fixtures" / "mmpbsa" / "tabular-FINAL_RESULTS_MMPBSA.dat"


@pytest.fixture
def tabular(tmp_path):
    shutil.copy(FIXTURE, tmp_path / "FINAL_RESULTS_MMPBSA.dat")
    return tmp_path


def test_the_real_table_output_of_this_pipeline_is_read(tabular) -> None:
    energy = read_binding_energy(tabular)

    assert energy is not None, "a finished calculation was read as a missing one"
    assert energy.total_kcal_mol == pytest.approx(15.79)
    assert energy.spread_kcal_mol == pytest.approx(0.0)
    assert energy.source == tabular / "FINAL_RESULTS_MMPBSA.dat"


def test_a_component_name_containing_a_space_keeps_it(tabular) -> None:
    """``Δ1-4 VDW`` must not lose its tail to the first number group."""

    components = read_binding_energy(tabular).components
    assert "Δ1-4 VDW" in components
    assert "Δ1-4 EEL" in components
    assert components["ΔVDWAALS"] == pytest.approx(-73.27)
    assert components["ΔEEL"] == pytest.approx(-131.51)
    assert components["ΔEPB"] == pytest.approx(181.58)


def test_the_standard_deviation_column_is_the_one_taken(tmp_path) -> None:
    """Column three (``SD``), not column two (``SD(Prop.)``).

    ``BindingEnergy`` defines ``spread`` as the deviation across one trajectory's frames.
    ``SD(Prop.)`` is propagated from the component deviations and answers a different
    question, so taking it because it sits first would change what the number means without
    changing its name. The columns are given distinct values here precisely so a swap fails.
    """

    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(
        "Energy Component       Average     SD(Prop.)         SD   SEM(Prop.)        SEM\n"
        "ΔVDWAALS                -40.00          9.90       1.10         9.91       1.11\n"
        "ΔTOTAL                  -35.20          9.80       2.10         9.81       2.11\n",
        encoding="utf-8")

    energy = read_binding_energy(tmp_path)
    assert energy.total_kcal_mol == pytest.approx(-35.20)
    assert energy.spread_kcal_mol == pytest.approx(2.10), "SD(Prop.) was taken instead of SD"


def test_the_equals_layout_still_reads_exactly_as_before(tmp_path) -> None:
    """The table reader runs only when the ``=`` form is absent, so PRISM-readable files
    are still read by the identical pattern and the two cannot report different numbers."""

    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(
        "VDWAALS     = -42.1 +/- 1.5\n"
        "EEL         = -15.3 +/- 3.2\n"
        "DELTA TOTAL = -35.2 +/- 2.1\n",
        encoding="utf-8")

    energy = read_binding_energy(tmp_path)
    assert energy.total_kcal_mol == pytest.approx(-35.2)
    assert energy.spread_kcal_mol == pytest.approx(2.1)
    assert energy.components["VDWAALS"] == pytest.approx(-42.1)
    assert "ΔTOTAL" not in energy.components


def test_two_method_sections_are_refused_rather_than_mixed(tmp_path) -> None:
    """A file may carry both GB and PB results and this API has no method selector.

    Taking one total while the components came from the other would mix estimands, so the
    table reader refuses for the same reason the ``=`` reader does.
    """

    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(
        "GENERALIZED BORN:\n"
        "Energy Component       Average     SD(Prop.)         SD   SEM(Prop.)        SEM\n"
        "ΔTOTAL                  -35.20          0.00       2.10         0.00       0.00\n"
        "POISSON BOLTZMANN:\n"
        "Energy Component       Average     SD(Prop.)         SD   SEM(Prop.)        SEM\n"
        "ΔTOTAL                  -28.40          0.00       1.80         0.00       0.00\n",
        encoding="utf-8")

    assert read_binding_energy(tmp_path) is None


def test_a_negative_spread_or_a_nan_is_refused(tmp_path) -> None:
    header = ("Energy Component       Average     SD(Prop.)         SD   SEM(Prop.)        SEM\n")
    for body in ("ΔTOTAL                  -35.20          0.00      -2.10         0.00       0.00\n",
                 "ΔTOTAL                     nan          0.00       2.10         0.00       0.00\n"):
        (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(header + body, encoding="utf-8")
        assert read_binding_energy(tmp_path) is None, body


def test_a_table_with_no_total_is_not_a_result(tmp_path) -> None:
    (tmp_path / "FINAL_RESULTS_MMPBSA.dat").write_text(
        "Energy Component       Average     SD(Prop.)         SD   SEM(Prop.)        SEM\n"
        "ΔVDWAALS                -40.00          0.00       1.10         0.00       0.00\n",
        encoding="utf-8")
    assert read_binding_energy(tmp_path) is None


def test_a_missing_file_is_still_none(tmp_path) -> None:
    assert read_binding_energy(tmp_path) is None


def test_every_parsed_number_is_finite(tabular) -> None:
    energy = read_binding_energy(tabular)
    assert math.isfinite(energy.total_kcal_mol)
    assert math.isfinite(energy.spread_kcal_mol)
    assert all(math.isfinite(value) for value in energy.components.values())
