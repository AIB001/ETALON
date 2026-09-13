"""Conservative, explainable RDKit hard-chemistry gate evaluation."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from rdkit import Chem, rdBase
from rdkit.Chem import FilterCatalog

from molcascade.chemistry.policies import HardGatePolicy, HardSmartsRule
from molcascade.config.canonical import canonical_sha256


@dataclass(frozen=True, slots=True)
class GateFinding:
    """One aggregated hard rejection or warning finding."""

    reason_code: str
    rule_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    """Complete hard-gate outcome for one parent."""

    passed: bool
    rejects: tuple[GateFinding, ...] = ()
    warnings: tuple[GateFinding, ...] = ()


def hard_gate_policy_id(policy: HardGatePolicy) -> str:
    """Return the content identity of every hard-gate policy choice."""

    digest = canonical_sha256(policy.model_dump(mode="json"))
    return f"hard-gate-policy:sha256:{digest}"


@lru_cache(maxsize=1)
def _pains_catalog() -> FilterCatalog.FilterCatalog:
    """Build the immutable RDKit PAINS catalog only once per worker process."""

    parameters = FilterCatalog.FilterCatalogParams()
    parameters.AddCatalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.PAINS)
    return FilterCatalog.FilterCatalog(parameters)


class HardGateEvaluator:
    """Compiled evaluator reusable for every parent in a stage invocation."""

    def __init__(self, policy: HardGatePolicy | None = None) -> None:
        self.policy = policy or HardGatePolicy()
        self._allowed = frozenset(self.policy.allowed_atomic_numbers)
        compiled: list[tuple[HardSmartsRule, Chem.Mol]] = []
        for rule in self.policy.hard_smarts:
            query = Chem.MolFromSmarts(rule.smarts)
            if query is None:  # Policy validation should already prevent this.
                raise ValueError(f"could not compile hard SMARTS rule {rule.id!r}")
            compiled.append((rule, query))
        self._compiled_rules = tuple(compiled)
        self._pains_catalog = _pains_catalog() if self.policy.flag_pains else None

    def evaluate(self, parent_smiles: str) -> GateEvaluation:
        """Evaluate one canonical parent without applying any soft property cut."""

        with rdBase.BlockLogs():
            try:
                molecule = Chem.MolFromSmiles(parent_smiles, sanitize=False)
            except Exception as error:
                return GateEvaluation(
                    passed=False,
                    rejects=(
                        GateFinding(
                            "PARSE_ERROR",
                            None,
                            f"parent SMILES parser failed: {error}",
                        ),
                    ),
                )
        if molecule is None:
            return GateEvaluation(
                passed=False,
                rejects=(
                    GateFinding("PARSE_ERROR", None, "parent SMILES parser returned no molecule"),
                ),
            )
        with rdBase.BlockLogs():
            try:
                Chem.SanitizeMol(molecule)
            except Exception as error:
                return GateEvaluation(
                    passed=False,
                    rejects=(
                        GateFinding(
                            "VALENCE_ERROR",
                            None,
                            f"parent sanitization failed: {error}",
                        ),
                    ),
                )
        if molecule.GetNumAtoms() == 0:
            return GateEvaluation(
                passed=False,
                rejects=(
                    GateFinding("EMPTY_OR_FRAGMENT_ONLY", None, "parent contains no atoms"),
                ),
            )
        query_atoms = [atom.GetIdx() for atom in molecule.GetAtoms() if atom.HasQuery()]
        query_bonds = [bond.GetIdx() for bond in molecule.GetBonds() if bond.HasQuery()]
        if query_atoms or query_bonds:
            return GateEvaluation(
                passed=False,
                rejects=(
                    GateFinding(
                        "QUERY_FEATURE_NOT_ALLOWED",
                        None,
                        "concrete parent contains query features at "
                        f"atoms={query_atoms}, bonds={query_bonds}",
                    ),
                ),
            )
        substance_group_count = len(Chem.GetMolSubstanceGroups(molecule))
        has_link_nodes = molecule.HasProp("_molLinkNodes")
        if substance_group_count or has_link_nodes:
            return GateEvaluation(
                passed=False,
                rejects=(
                    GateFinding(
                        "UNSUPPORTED_STRUCTURAL_METADATA",
                        None,
                        "small-molecule gate does not accept link nodes or "
                        "substance groups "
                        f"(link_nodes={has_link_nodes}, "
                        f"substance_groups={substance_group_count})",
                    ),
                ),
            )
        if len(Chem.GetMolFrags(molecule)) != 1:
            return GateEvaluation(
                passed=False,
                rejects=(
                    GateFinding(
                        "MULTICOMPONENT_PARENT",
                        None,
                        "hard-gate input must contain exactly one registered parent fragment",
                    ),
                ),
            )

        rejects: list[GateFinding] = []
        disallowed_atoms = sorted(
            (
                atom.GetIdx(),
                atom.GetAtomicNum(),
                atom.GetSymbol(),
            )
            for atom in molecule.GetAtoms()
            if atom.GetAtomicNum() not in self._allowed
        )
        if disallowed_atoms:
            disallowed_elements = sorted(
                {(number, symbol) for _, number, symbol in disallowed_atoms}
            )
            rejects.append(
                GateFinding(
                    "DISALLOWED_ELEMENT",
                    "elements.allowlist",
                    json.dumps(
                        {
                            "elements": [
                                {"atomic_number": number, "symbol": symbol}
                                for number, symbol in disallowed_elements
                            ],
                            "atom_indices": [index for index, _, _ in disallowed_atoms],
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )

        matched_by_reason: dict[
            str,
            list[tuple[HardSmartsRule, tuple[tuple[int, ...], ...], bool]],
        ] = defaultdict(list)
        for rule, query in self._compiled_rules:
            detected_matches = molecule.GetSubstructMatches(
                query,
                uniquify=True,
                useChirality=rule.use_chirality,
                maxMatches=17,
            )
            if detected_matches:
                matches_truncated = len(detected_matches) > 16
                atom_matches = detected_matches[:16]
                matched_by_reason[rule.reason_code].append(
                    (rule, atom_matches, matches_truncated)
                )
        for reason_code in sorted(matched_by_reason):
            rule_matches = sorted(
                matched_by_reason[reason_code],
                key=lambda item: item[0].id,
            )
            rejects.append(
                GateFinding(
                    reason_code,
                    ",".join(rule.id for rule, _, _ in rule_matches),
                    json.dumps(
                        {
                            "matched_rules": [
                                {
                                    "id": rule.id,
                                    "description": rule.description,
                                    "smarts": rule.smarts,
                                    "atom_matches": [list(match) for match in atom_matches],
                                    "atom_match_limit": 16,
                                    "atom_matches_truncated": matches_truncated,
                                    "use_chirality": rule.use_chirality,
                                }
                                for rule, atom_matches, matches_truncated in rule_matches
                            ]
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )

        warnings: list[GateFinding] = []
        if self._pains_catalog is not None:
            matches = sorted(
                {entry.GetDescription() for entry in self._pains_catalog.GetMatches(molecule)}
            )
            if matches:
                warnings.append(
                    GateFinding(
                        "PAINS_ALERT",
                        "rdkit.filter_catalog.pains",
                        json.dumps(
                            {"catalog": "PAINS", "matches": matches},
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                )
        return GateEvaluation(
            passed=not rejects,
            rejects=tuple(rejects),
            warnings=tuple(warnings),
        )


__all__ = [
    "GateEvaluation",
    "GateFinding",
    "HardGateEvaluator",
    "hard_gate_policy_id",
]
