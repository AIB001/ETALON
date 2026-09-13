"""Design the edge network a relative free energy calculation actually needs.

Every other tier in a funnel scores molecules. FEP scores *differences*, so it operates on edges
between pairs and a set of molecules is not an input to it. Handing a relative method a shortlist
and asking for a ranking is a category error that produces numbers rather than an error message,
which is why this module exists and why its first job is to refuse.

The refusal has a measured cause. On the twelve most potent molecules of the real STK17B panel, 61
of the 66 possible pairs share a common core smaller than half the larger molecule, with a median of
0.21 and a minimum of zero. Those twelve are not a congeneric series; they are twelve different
chemotypes that happen to bind the same kinase. A relative calculation across such a pair is a near
total double annihilation -- the "relative" transformation destroys and rebuilds most of the
molecule -- and PRISM's mapper is Cartesian distance with a 0.6 nm cutoff and no quality gate, so it
will happily construct one.

There is a structural tension here worth naming rather than engineering around. A campaign that
screens 750,000 molecules from five generative models and selects for diversity and scaffold novelty
-- which is what the earlier tiers and the acquisition layer are deliberately doing -- delivers
survivors that are diverse by construction. Those are exactly the molecules relative FEP cannot
relate to each other. The two halves of the pipeline are pulling in opposite directions, and the
honest output is a set of small connected components plus a list of singletons, each of which needs
an absolute method or a reference compound of its own.

What the network buys when it does exist is an error estimate nobody has to pay extra for. A spanning
tree over a component connects every molecule to a reference with the fewest edges and provides no
validation at all. Each edge beyond a spanning tree closes one independent cycle, and the sum of
free energy differences around a cycle must be zero: the deviation is hysteresis, and it is a direct
measurement of the calculation's own error on this system rather than an assumed error bar. So the
cost of confidence is countable in edges, which is the form a budget decision can use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: Minimum share of the larger molecule's heavy atoms that the common core must cover for an edge to
#: be treated as relative. A convention, and a generous one: published guidance for alchemical edges
#: is small perturbations, which usually means far more than half the molecule is common. Set here so
#: that a pair failing it is unambiguously not a congeneric pair rather than merely a stretch.
MIN_CORE_FRACTION = 0.5

#: Seconds to spend looking for a maximum common substructure on one pair. Measured: about 34 ms per
#: pair on this panel, so the timeout is for pathological pairs rather than for the normal case.
MCS_TIMEOUT = 2


@dataclass(frozen=True, slots=True)
class Edge:
    """One candidate alchemical transformation, with the mapping it would run through."""

    left: str
    right: str
    #: Heavy atoms in the maximum common substructure.
    core_atoms: int
    left_heavy: int
    right_heavy: int
    core_smarts: str = ""

    @property
    def core_fraction(self) -> float:
        """Share of the larger molecule that is common. The number that decides the edge."""

        larger = max(self.left_heavy, self.right_heavy)
        return 0.0 if not larger else self.core_atoms / larger

    @property
    def perturbed_atoms(self) -> int:
        """Heavy atoms that must be created or destroyed. What the calculation actually costs."""

        return (self.left_heavy - self.core_atoms) + (self.right_heavy - self.core_atoms)

    @property
    def usable(self) -> bool:
        return self.core_fraction >= MIN_CORE_FRACTION

    def as_dict(self) -> dict[str, object]:
        return {
            "left": self.left,
            "right": self.right,
            "core_atoms": self.core_atoms,
            "core_fraction": round(self.core_fraction, 4),
            "perturbed_atoms": self.perturbed_atoms,
            "usable": self.usable,
            "core_smarts": self.core_smarts,
        }


def mapping_quality(
    molecules: Mapping[str, str],
    *,
    timeout: int = MCS_TIMEOUT,
) -> list[Edge]:
    """Every pair's maximum common substructure, which is the observable behind the mapping fault.

    ``F_FEP_MAPPING_DEGENERATE`` has been in the taxonomy since the fault layer was written and
    nothing computed it. This is the computation: a maximum common substructure with ring matching
    required, so a core that cuts a ring in half does not count as common -- an alchemical edge
    through a broken ring is a transformation nobody intended.
    """

    from rdkit import Chem, RDLogger, rdBase
    from rdkit.Chem import rdFMCS

    RDLogger.DisableLog("rdApp.*")
    parsed: dict[str, object] = {}
    for name, smiles in molecules.items():
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is not None:
            parsed[name] = molecule

    names = list(parsed)
    edges: list[Edge] = []
    with rdBase.BlockLogs():
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                result = rdFMCS.FindMCS(
                    [parsed[left], parsed[right]],
                    timeout=timeout,
                    atomCompare=rdFMCS.AtomCompare.CompareElements,
                    bondCompare=rdFMCS.BondCompare.CompareOrder,
                    ringMatchesRingOnly=True,
                    completeRingsOnly=True,
                )
                edges.append(
                    Edge(
                        left=left,
                        right=right,
                        core_atoms=int(result.numAtoms),
                        left_heavy=int(parsed[left].GetNumHeavyAtoms()),  # type: ignore[attr-defined]
                        right_heavy=int(parsed[right].GetNumHeavyAtoms()),  # type: ignore[attr-defined]
                        core_smarts=str(result.smartsString or ""),
                    )
                )
    return edges


@dataclass(frozen=True, slots=True)
class Network:
    """A designed edge network, its cost in edges, and what it cannot reach."""

    edges: tuple[Edge, ...]
    #: Molecules with no usable edge to anything. Each needs an absolute method or its own reference,
    #: and saying so is the point: connecting one through a degenerate mapping produces a number.
    unreachable: tuple[str, ...]
    #: Connected components of the usable graph, largest first. A component with no reference in it
    #: has no anchor, so its internal differences are known and its absolute values are not.
    components: tuple[tuple[str, ...], ...]
    #: Components holding no reference compound with a measured affinity.
    unanchored: tuple[tuple[str, ...], ...] = ()
    #: Independent cycles the network closes: E - V + 1 per component. Each is a free error estimate.
    cycles: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    @property
    def reachable(self) -> int:
        return sum(len(component) for component in self.components)

    def as_dict(self) -> dict[str, object]:
        return {
            "edges": [edge.as_dict() for edge in self.edges],
            "edge_count": self.edge_count,
            "independent_cycles": self.cycles,
            "molecules_reachable": self.reachable,
            "unreachable": list(self.unreachable),
            "components": [list(component) for component in self.components],
            "unanchored_components": [list(component) for component in self.unanchored],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        lines = [
            f"{self.edge_count} edge(s) over {self.reachable} molecule(s) in "
            f"{len(self.components)} component(s); {self.cycles} independent cycle(s) for "
            f"hysteresis, {len(self.unreachable)} molecule(s) unreachable"
        ]
        for component in self.components:
            anchor = "" if component not in self.unanchored else "  [no reference -- differences only]"
            lines.append(f"  component of {len(component)}: {', '.join(component[:6])}{anchor}")
        if self.unreachable:
            lines.append("")
            lines.append(
                f"  no usable edge: {', '.join(self.unreachable[:10])}"
                + (f" and {len(self.unreachable) - 10} more" if len(self.unreachable) > 10 else "")
            )
        for note in self.notes:
            lines.append("")
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def design(
    molecules: Mapping[str, str],
    *,
    references: Sequence[str] = (),
    min_core_fraction: float = MIN_CORE_FRACTION,
    cycle_edges: int = 0,
    edges: Sequence[Edge] | None = None,
) -> Network:
    """Design a network over the molecules that can actually be related to each other.

    Args:
        references: Molecules with a measured affinity. A component containing none of them yields
            differences and no absolute values, which is a usable result for ranking within the
            component and not for comparing it with another.
        cycle_edges: How many edges beyond a spanning forest to add, spent on the best-mapped
            unused pairs. Each one closes an independent cycle and buys a hysteresis check -- the
            only error estimate in this pipeline that is a measurement rather than an assumption.
    """

    found = list(edges) if edges is not None else mapping_quality(molecules)
    usable = sorted(
        (edge for edge in found if edge.core_fraction >= min_core_fraction),
        key=lambda edge: (-edge.core_fraction, edge.perturbed_atoms),
    )

    # A spanning forest over the usable graph: take edges best-mapped first, keeping one that joins
    # two components. Best-mapped first rather than fewest-perturbed-atoms first because a large
    # common core is what makes the calculation relative at all, and cost is secondary to validity.
    parent: dict[str, str] = {name: name for name in molecules}

    def root(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    chosen: list[Edge] = []
    spare: list[Edge] = []
    for edge in usable:
        left, right = root(edge.left), root(edge.right)
        if left == right:
            spare.append(edge)
            continue
        parent[left] = right
        chosen.append(edge)

    added = spare[: max(0, cycle_edges)]
    chosen.extend(added)

    groups: dict[str, list[str]] = {}
    for name in molecules:
        groups.setdefault(root(name), []).append(name)
    components = tuple(
        tuple(sorted(members)) for members in sorted(groups.values(), key=len, reverse=True) if len(members) > 1
    )
    unreachable = tuple(sorted(name for members in groups.values() if len(members) == 1 for name in members))

    reference_set = set(references)
    unanchored = tuple(
        component for component in components if not reference_set.intersection(component)
    )
    cycles = len(chosen) - sum(len(component) for component in components) + len(components)

    notes: list[str] = []
    total_pairs = len(found)
    if total_pairs:
        rejected = total_pairs - len(usable)
        notes.append(
            f"{rejected} of {total_pairs} candidate pairs map through a common core below "
            f"{min_core_fraction:.0%} of the larger molecule and are not edges. A relative "
            "calculation across such a pair destroys and rebuilds most of the molecule; PRISM's "
            "mapper is Cartesian distance with a 0.6 nm cutoff and no quality gate, so it will "
            "construct one without complaint."
        )
    if unreachable:
        notes.append(
            f"{len(unreachable)} molecule(s) have no usable edge to anything. Relative FEP cannot "
            "rank them at all: they need an absolute method, or a reference compound from their own "
            "chemotype. This is the expected outcome for survivors of a funnel that selected for "
            "diversity, and connecting them through a degenerate mapping would produce numbers "
            "rather than an error."
        )
    if unanchored:
        notes.append(
            f"{len(unanchored)} component(s) contain no reference compound, so their internal "
            "differences are computable and their absolute affinities are not. Ranking across two "
            "unanchored components is not possible from this network."
        )
    if cycles == 0 and chosen:
        notes.append(
            "This network is a spanning forest, so it closes no cycles and provides no internal "
            "error estimate. Every edge beyond a forest buys one hysteresis check -- the sum of "
            "differences around a cycle must be zero, and its deviation is a measurement of this "
            "system's own error rather than an assumed bar. Pass cycle_edges to buy some."
        )
    elif cycles:
        notes.append(
            f"{cycles} independent cycle(s) for {len(added)} extra edge(s): that many hysteresis "
            "checks, each a direct measurement of the calculation's error on this system."
        )
    return Network(
        edges=tuple(chosen),
        unreachable=unreachable,
        components=components,
        unanchored=unanchored,
        cycles=cycles,
        notes=tuple(notes),
    )


def observations(network: Network, molecules: Sequence[str]) -> tuple:
    """Turn a designed network into fault observations for the molecules it cannot reach.

    ``F_FEP_MAPPING_DEGENERATE`` was unevaluable everywhere until now, because a mapping is a
    property of an edge and the preflight layer sees one record at a time. A designed network is
    where it becomes evaluable.
    """

    from etalon.faults.attribution import Observation

    unreachable = set(network.unreachable)
    return tuple(
        Observation(
            "F_FEP_MAPPING_DEGENERATE",
            fired=name in unreachable,
            detail=(
                "no candidate pair maps through a common core large enough for a relative "
                "calculation, so this molecule is not in the network"
                if name in unreachable
                else "present in a component with at least one usable edge"
            ),
        )
        for name in molecules
    )


__all__ = [
    "MCS_TIMEOUT",
    "MIN_CORE_FRACTION",
    "Edge",
    "Network",
    "design",
    "mapping_quality",
    "observations",
]
