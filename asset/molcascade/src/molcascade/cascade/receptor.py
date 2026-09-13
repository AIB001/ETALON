"""Repair the operator's PDB once, before any engine forms an opinion of it.

A crystal structure is not a model of a protein; it is a model of the density
that was resolved.  Long flexible side chains routinely have none, so the
depositor stops at CB and the file that reaches a docking run is a protein with
a dozen truncated residues in it.  Every consumer then handles that differently
and none of them says so: meeko refuses to build a receptor at all, GNINA and
KarmaDock parse the atoms that are there and dock against a protein missing part
of its surface, and PoseBusters measures clashes against the same holes.

The way past meeko's refusal used to be meeko's own ``--allow_bad_res``, and
that flag does not tolerate an incomplete residue -- it *deletes* it, backbone
included.  On STK17B that removed twelve residues from a 278-residue chain and
nothing downstream could tell.  So the operator's only documented route to a
working receptor was a silent truncation, which is the exact failure the target
module was written to prevent.

This module removes the *reason* instead of the residue.  PDBFixer rebuilds the
missing side-chain atoms from rotamer templates, waters and co-crystallised
junk are dropped, and selenomethionine and friends are converted back to what
they stand in for -- after which meeko matches every template and deletes
nothing.

Two rules bound what it will do, and they are the difference between this and
``--allow_bad_res``:

*Nothing is invented.*  A residue absent from the model entirely -- an
unresolved loop -- is reported and left absent.  Rebuilding a nine-residue loop
from a template produces coordinates that look exactly as authoritative as
measured ones and are not.  Missing *atoms* in a residue that is present are a
different case: the residue's identity is known, the rotamer is constrained by
its neighbours, and leaving it truncated is itself a claim that the side chain
is not there.

*Nothing is removed silently.*  Every deletion, replacement and rebuild is
counted and named in :class:`PreparationReport`, which becomes part of the
target notes the run prints and records.  Metals are kept by default for the
same reason: a catalytic zinc deleted as a "non-standard residue" changes every
score in the tier and shows up in none of them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from molcascade.cascade.models import ReceptorPreparation
from molcascade.errors import ConfigError

#: Kept when heterogens are removed, because deleting one is a change of
#: chemistry rather than of housekeeping.  Structural and catalytic ions sit in
#: the site and coordinate the ligand -- zinc in HDACs, matrix
#: metalloproteinases and carbonic anhydrases, magnesium in kinases' ATP site --
#: and a docking run against a stripped pocket is not a weaker experiment but a
#: different one.  Matched on the element of a single-atom residue, so a
#: zinc-containing cofactor is still treated as a cofactor.
METAL_ELEMENTS = frozenset({"ZN", "MG", "MN", "FE", "CA", "CU", "CO", "NI", "NA", "K", "CD", "HG"})

#: Solvent under the names structures actually use.  ``HOH`` is what the PDB
#: prescribes and what OpenMM writes; the rest arrive from simulation-derived
#: and older files, and a water not recognised as one would survive as a
#: "cofactor" sitting in the pocket.
WATER_RESIDUES = frozenset({"HOH", "WAT", "DOD", "H2O", "SOL", "TIP", "TIP3", "TIP4"})

#: A receptor is one structure, not a trajectory: the same ceiling the docking
#: adapters apply to the file they read.
_MAX_RECEPTOR_BYTES = 256 * 1024 * 1024

#: Above this, in nanometres, two consecutive residues are not bonded to each
#: other.  A peptide bond is 0.133 nm; the margin is wide because the question
#: here is "is anything missing between these two", and the answers are 0.13 and
#: something in the nanometre range, with nothing in between to discriminate.
_PEPTIDE_BOND_LIMIT_NM = 0.25


@dataclass(frozen=True, slots=True)
class PreparationReport:
    """Everything the repair changed, in enough detail to argue with.

    This exists because "the receptor was prepared" is not a reproducible
    statement.  Which waters, which heterogens, which side chains, and -- the
    entry that matters most -- which gaps were found and deliberately left
    alone.  Rendered into the target notes, so it is printed at the top of a
    run and recorded with it.
    """

    residues_in: int
    residues_out: int
    waters_removed: int = 0
    #: ``(residue name, count)``, sorted, so a report names PO4 and NAG rather
    #: than reporting "9 heterogens removed".
    heterogens_removed: tuple[tuple[str, int], ...] = ()
    metals_kept: tuple[str, ...] = ()
    #: ``(label, standard name)`` -- MSE at A:34 became MET.
    nonstandard_replaced: tuple[tuple[str, str], ...] = ()
    #: ``(label, atom names)`` -- the repair this module exists for.
    atoms_rebuilt: tuple[tuple[str, tuple[str, ...]], ...] = ()
    terminals_capped: tuple[str, ...] = ()
    #: Found by PDBFixer and *not* built.  Named so nobody mistakes a run
    #: against a discontinuous chain for a run against a whole one.
    gaps_not_built: tuple[str, ...] = ()
    skipped: bool = False

    def notes(self) -> tuple[str, ...]:
        """One line per kind of change, and nothing for a kind that had none."""

        if self.skipped:
            return ("receptor preparation skipped: the structure is docked exactly as supplied",)
        lines = [f"receptor prepared: {self.residues_in} residues in, {self.residues_out} out"]
        if self.waters_removed:
            lines.append(f"  waters removed: {self.waters_removed}")
        if self.heterogens_removed:
            named = ", ".join(f"{name} x{count}" for name, count in self.heterogens_removed)
            lines.append(f"  heterogens removed: {named}")
        if self.metals_kept:
            lines.append(f"  metals kept: {', '.join(self.metals_kept)}")
        if self.nonstandard_replaced:
            named = ", ".join(f"{a}->{b}" for a, b in self.nonstandard_replaced)
            lines.append(f"  non-standard residues replaced: {named}")
        if self.atoms_rebuilt:
            named = "; ".join(f"{label} {','.join(atoms)}" for label, atoms in self.atoms_rebuilt)
            count = len(self.atoms_rebuilt)
            lines.append(f"  side-chain atoms rebuilt ({count} residues): {named}")
        if self.terminals_capped:
            lines.append(f"  chain terminals capped: {', '.join(self.terminals_capped)}")
        if self.gaps_not_built:
            lines.append(
                "  unresolved and left unbuilt (not modelled, not invented): "
                + ", ".join(self.gaps_not_built)
            )
        if not self.atoms_rebuilt and not self.gaps_not_built:
            lines.append("  no residue was incomplete; nothing was rebuilt")
        return tuple(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "residues_in": self.residues_in,
            "residues_out": self.residues_out,
            "waters_removed": self.waters_removed,
            "heterogens_removed": [list(entry) for entry in self.heterogens_removed],
            "metals_kept": list(self.metals_kept),
            "nonstandard_replaced": [list(entry) for entry in self.nonstandard_replaced],
            "atoms_rebuilt": [[label, list(atoms)] for label, atoms in self.atoms_rebuilt],
            "terminals_capped": list(self.terminals_capped),
            "gaps_not_built": list(self.gaps_not_built),
            "skipped": self.skipped,
        }

    @classmethod
    def from_json(cls, payload: Any) -> PreparationReport:
        if not isinstance(payload, dict):
            raise ValueError("a preparation report is a JSON object")
        return cls(
            residues_in=int(payload["residues_in"]),
            residues_out=int(payload["residues_out"]),
            waters_removed=int(payload.get("waters_removed", 0)),
            heterogens_removed=tuple(
                (str(name), int(count)) for name, count in payload.get("heterogens_removed", ())
            ),
            metals_kept=tuple(str(item) for item in payload.get("metals_kept", ())),
            nonstandard_replaced=tuple(
                (str(label), str(standard))
                for label, standard in payload.get("nonstandard_replaced", ())
            ),
            atoms_rebuilt=tuple(
                (str(label), tuple(str(atom) for atom in atoms))
                for label, atoms in payload.get("atoms_rebuilt", ())
            ),
            terminals_capped=tuple(str(item) for item in payload.get("terminals_capped", ())),
            gaps_not_built=tuple(str(item) for item in payload.get("gaps_not_built", ())),
            skipped=bool(payload.get("skipped", False)),
        )


def recipe_digest(options: ReceptorPreparation) -> str:
    """A short, stable name for one recipe, used to key the cache.

    The prepared structure is a function of the input bytes *and* the options,
    so keying on the input alone would hand a run that kept its waters the file
    made by a run that removed them.
    """

    canonical = json.dumps(options.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def prepare_receptor_pdb(
    receptor: Path,
    *,
    options: ReceptorPreparation,
    workspace: Path,
) -> tuple[Path, PreparationReport]:
    """Clean and repair the receptor, returning the prepared file and what changed.

    Cached by the input's digest and the recipe's, next to the PDBQT derived
    from it, so re-running a cascade does not re-prepare and editing the
    receptor cannot leave a stale prepared copy wearing the same name.  The
    report is cached beside the structure rather than recomputed, so a cache hit
    still says what was removed instead of degrading to "reused from cache".
    """

    from molcascade.plugins.builtin.docking.common import structure_digest

    _, digest = structure_digest(
        str(receptor),
        code="DOCKING_RECEPTOR_UNREADABLE",
        hint=(
            "This is the receptor for the docking tier, read once here so a run "
            "cannot discover it is unreadable three tiers deep."
        ),
        limit_bytes=_MAX_RECEPTOR_BYTES,
    )
    stem = f"prepared-{recipe_digest(options)}"
    directory = workspace / "targets" / digest
    destination = directory / f"{stem}.pdb"
    sidecar = directory / f"{stem}.json"
    if destination.exists() and sidecar.exists():
        try:
            cached = PreparationReport.from_json(json.loads(sidecar.read_text("utf-8")))
        except (OSError, ValueError, KeyError):
            # A truncated sidecar is not a reason to refuse the run; it is a
            # reason to prepare again, which is deterministic and cheap.
            pass
        else:
            return destination, cached

    fixer, module = _open(receptor)
    report = _repair(fixer, module, options=options)
    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / f"{stem}.pdb.partial"
    try:
        with partial.open("w", encoding="utf-8") as stream:
            # keepIds is not optional: without it every chain and residue is
            # renumbered from one, and 'LYS43' in this report -- and in every
            # contact analysis afterwards -- would name a different residue than
            # the literature and the operator's own notes do.
            module.PDBFile.writeFile(fixer.topology, fixer.positions, stream, keepIds=True)
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    sidecar.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    return destination, report


def _open(receptor: Path) -> tuple[Any, Any]:
    """Load the structure, turning both failure modes into one sentence each."""

    try:
        from openmm import app
        from pdbfixer import PDBFixer
    except ImportError as error:
        raise ConfigError(
            "pdbfixer is not installed, so the receptor cannot be prepared",
            code="DOCKING_RECEPTOR_REPAIR_UNAVAILABLE",
            hint=(
                "MolCascade repairs the receptor before docking it -- crystal "
                "structures routinely stop a side chain at CB, and every engine "
                "in the tier handles that differently and silently. Install it "
                "with 'pip install pdbfixer' (that and OpenMM are about 15 MB) "
                'or with the docking extra, pip install "molcascade[docking]". '
                "To dock the structure exactly as supplied, pass "
                "'--no-receptor-prepare' -- but note that meeko will then refuse "
                "any structure with an incomplete residue in it."
            ),
            context={"receptor_path": str(receptor), "error": str(error)},
        ) from error
    try:
        fixer = PDBFixer(filename=str(receptor))
    except Exception as error:
        raise ConfigError(
            f"pdbfixer could not read {receptor.name} as a PDB structure",
            code="DOCKING_RECEPTOR_REPAIR_FAILED",
            hint=(
                "This is the receptor the docking tier reads. A truncated file, "
                "an mmCIF wearing a .pdb suffix, and a structure with no ATOM "
                "records all arrive here. Pass '--no-receptor-prepare' to dock "
                "it unrepaired."
            ),
            context={
                "receptor_path": str(receptor),
                "error_type": type(error).__name__,
                "error": str(error)[:1000],
            },
        ) from error
    return fixer, app


def _repair(fixer: Any, module: Any, *, options: ReceptorPreparation) -> PreparationReport:
    """PDBFixer's canonical order, with the two deviations this module is for.

    The order is not interchangeable.  Non-standard residues are converted
    *before* heterogens are removed, or selenomethionine gets deleted as a
    heterogen instead of becoming the methionine it stands for.  Heterogens go
    *before* the atom rebuild, so no rotamer is built into something about to be
    deleted.  And ``missingResidues`` is read before it is cleared, because
    reporting an unresolved loop needs the list that clearing it throws away.
    """

    residues_in = sum(1 for _ in fixer.topology.residues())

    fixer.findMissingResidues()
    gaps = _describe_gaps(fixer) + _find_chain_breaks(fixer.topology, fixer.positions)
    # Deviation one: found, reported, and not built.
    fixer.missingResidues = {}

    fixer.findNonstandardResidues()
    replaced = tuple(
        (_label(residue), str(standard)) for residue, standard in fixer.nonstandardResidues
    )
    fixer.replaceNonstandardResidues()

    # Deviation two: PDBFixer's own removeHeterogens has no whitelist and takes
    # the metals with it.
    waters, heterogens, metals = _remove_heterogens(fixer, module, options=options)

    fixer.findMissingAtoms()
    rebuilt = tuple(
        sorted(
            (_label(residue), tuple(atom.name for atom in atoms))
            for residue, atoms in fixer.missingAtoms.items()
            if atoms
        )
    )
    capped = tuple(sorted(_label(residue) for residue in fixer.missingTerminals))
    fixer.addMissingAtoms()

    return PreparationReport(
        residues_in=residues_in,
        residues_out=sum(1 for _ in fixer.topology.residues()),
        waters_removed=waters,
        heterogens_removed=heterogens,
        metals_kept=metals,
        nonstandard_replaced=replaced,
        atoms_rebuilt=rebuilt,
        terminals_capped=capped,
        gaps_not_built=gaps,
    )


def _remove_heterogens(
    fixer: Any,
    module: Any,
    *,
    options: ReceptorPreparation,
) -> tuple[int, tuple[tuple[str, int], ...], tuple[str, ...]]:
    """Delete solvent and co-crystallised matter, never a metal.

    Written out rather than delegated to ``fixer.removeHeterogens`` for the one
    reason that matters scientifically: that method keeps a fixed set of
    polymer residue names and deletes everything else, so a catalytic zinc goes
    the same way as a sulfate ion.
    """

    from pdbfixer.pdbfixer import dnaResidues, proteinResidues, rnaResidues

    polymer = set(proteinResidues) | set(dnaResidues) | set(rnaResidues) | {"UNK", "N"}
    doomed = []
    waters = 0
    removed: dict[str, int] = {}
    metals: list[str] = []
    for residue in fixer.topology.residues():
        name = residue.name.strip().upper()
        if name in polymer:
            continue
        if name in WATER_RESIDUES:
            if not options.keep_waters:
                doomed.append(residue)
                waters += 1
            continue
        if _is_metal(residue):
            metals.append(_label(residue))
            continue
        if options.keep_heterogens:
            continue
        doomed.append(residue)
        removed[name] = removed.get(name, 0) + 1
    if doomed:
        modeller = module.Modeller(fixer.topology, fixer.positions)
        modeller.delete(doomed)
        fixer.topology = modeller.topology
        fixer.positions = modeller.positions
    return waters, tuple(sorted(removed.items())), tuple(metals)


def _is_metal(residue: Any) -> bool:
    """A lone ion, identified by its element rather than its residue name.

    By element because the name is not dependable -- the same zinc is ZN in one
    deposition and a chain of its own in another -- and single-atom because a
    metal *inside* a cofactor (the iron in a haem) belongs to the cofactor and
    should share its fate.
    """

    atoms = list(residue.atoms())
    if len(atoms) != 1:
        return False
    element = getattr(atoms[0], "element", None)
    symbol = getattr(element, "symbol", None)
    return isinstance(symbol, str) and symbol.upper() in METAL_ELEMENTS


def _describe_gaps(fixer: Any) -> tuple[str, ...]:
    """Name each unresolved stretch by the residues it sits between.

    ``missingResidues`` is keyed by ``(chain index, position among the residues
    that *are* present)``, which is unreadable in a report.  Translating it into
    "A:187-195 (9 residues)" is the whole point: an operator who is told that
    can decide whether the gap is anywhere near the site, and one who is told
    "1 missing region" cannot.
    """

    chains = list(fixer.topology.chains())
    described: list[str] = []
    for (chain_index, position), names in sorted(fixer.missingResidues.items()):
        if chain_index >= len(chains):
            continue
        chain = chains[chain_index]
        residues = list(chain.residues())
        count = len(names)
        if position == 0:
            where = f"before {residues[0].id}" if residues else "at the start"
            described.append(f"{chain.id}: {count} residue(s) {where} (N-terminal, unresolved)")
            continue
        if position >= len(residues):
            where = f"after {residues[-1].id}" if residues else "at the end"
            described.append(f"{chain.id}: {count} residue(s) {where} (C-terminal, unresolved)")
            continue
        described.append(
            f"{chain.id}:{_between(residues[position - 1].id, residues[position].id)} "
            f"({count} residues: {'-'.join(names)})"
        )
    return tuple(described)


def _find_chain_breaks(topology: Any, positions: Any) -> tuple[str, ...]:
    """Find unresolved stretches without needing the file to declare a sequence.

    ``findMissingResidues`` compares the model against SEQRES, so on a structure
    stripped of its header -- which is most structures that reach a docking run,
    including the one this module was written against -- it reports nothing and
    a nine-residue hole passes unmentioned.  A break is visible in the
    coordinates regardless: consecutive residues of one chain whose peptide bond
    is not a bond.

    Both signals are read because each misses a case the other catches.  The
    C-N distance finds a break in a renumbered file, where the numbering is
    continuous across a hole; the numbering finds one where a disordered
    stretch was deleted but the flanks happen to have drifted close together.
    """

    described: list[str] = []
    for chain in topology.chains():
        residues = list(chain.residues())
        for first, second in pairwise(residues):
            carbon = _atom(first, "C")
            nitrogen = _atom(second, "N")
            if carbon is None or nitrogen is None:
                continue
            if _distance_nm(positions, carbon, nitrogen) <= _PEPTIDE_BOND_LIMIT_NM:
                continue
            described.append(
                f"{chain.id}:{_between(first.id, second.id)} unresolved between "
                f"{first.name}{first.id} and {second.name}{second.id} "
                "(chain break, left as a break)"
            )
    return tuple(described)


def _atom(residue: Any, name: str) -> Any:
    for atom in residue.atoms():
        if atom.name == name:
            return atom
    return None


def _distance_nm(positions: Any, first: Any, second: Any) -> float:
    left, right = positions[first.index], positions[second.index]
    return float(
        sum((left[axis] - right[axis]).value_in_unit(left.unit) ** 2 for axis in range(3)) ** 0.5
    )


def _between(before: str, after: str) -> str:
    """The numbering of the gap itself, when the flanks are plain integers."""

    try:
        first, last = int(before) + 1, int(after) - 1
    except (TypeError, ValueError):
        return f"{before}..{after}"
    if last < first:
        return f"{before}..{after}"
    return str(first) if last == first else f"{first}-{last}"


def _label(residue: Any) -> str:
    """``A:LYS43`` -- the chain, the residue name and the author's numbering."""

    chain = getattr(residue, "chain", None)
    chain_id = getattr(chain, "id", "?")
    return f"{chain_id}:{residue.name}{residue.id}"


__all__ = [
    "METAL_ELEMENTS",
    "WATER_RESIDUES",
    "PreparationReport",
    "prepare_receptor_pdb",
    "recipe_digest",
]
