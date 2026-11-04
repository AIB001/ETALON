"""Compose MolCascade components without inheriting its example screening hierarchy.

Only I/O and registration are supplied by default. The scientific components, their order,
parallel joins, gates and evidence bindings belong to the caller and are validated by MolCascade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from etalon.boundary.infra import load
from etalon.campaign.configuration import merge_changes


def component(identifier: str, backend: str, *, settings: Mapping[str, Any] | None = None,
              gate: Mapping[str, Any] | None = None,
              evidence_from: Mapping[str, str] | None = None) -> dict[str, Any]:
    """One exact, versioned producer. No threshold gate is silently added."""
    load("molcascade")
    from molcascade.cascade.introspect import with_schema_version
    from molcascade.cascade.models import CriterionConfig
    from molcascade.plugins import create_builtin_registry

    registry = create_builtin_registry()
    raw = {"id": identifier, "backend": backend,
           "settings": with_schema_version(backend, dict(settings or {}), registry=registry),
           "evidence_from": dict(evidence_from or {}), "gate": dict(gate) if gate else None}
    return CriterionConfig.model_validate(raw).model_dump(mode="json")


#: Backend prefixes that satisfy a named indispensable step. A prefix rather than an exact
#: ref on purpose: which docking engine runs is a scientific choice the host keeps, while
#: *that something docks* can be the campaign's declared invariant. Versions are therefore
#: not pinned here either -- an engine upgrade is not an omitted step.
CAPABILITY_BACKENDS: dict[str, tuple[str, ...]] = {
    "docking": ("docking.unidock", "docking.gnina", "docking.karmadock"),
    "redock": ("docking.redock_consistency",),
    "affinity": ("prediction.boltz2",),
    "conformers": ("docking.rdkit_conformers", "docking.registered_conformers"),
    "handoff": ("handoff.md_system_input_conformer",),
}


def provided_capabilities(tiers: Sequence[Mapping[str, Any]]) -> set[str]:
    """Which named steps this composition actually contains, by backend prefix."""
    backends: list[str] = []
    for tier in tiers:
        for criterion in tier.get("criteria", []) or []:
            backend = criterion.get("backend")
            if isinstance(backend, str):
                backends.append(backend)
        for step in tier.get("steps", []) or []:
            backend = step.get("backend")
            if isinstance(backend, str):
                backends.append(backend)
    return {name for name, prefixes in CAPABILITY_BACKENDS.items()
            if any(backend.startswith(prefix) for prefix in prefixes for backend in backends)}


def check_required(tiers: Sequence[Mapping[str, Any]], required: Sequence[str]) -> None:
    """Refuse a composition that drops a step the campaign declared indispensable.

    Free composition is the point of this module, so nothing is required by default: a
    one-component cascade stays legal. What this adds is a way for a *campaign* to name the
    steps its conclusions depend on, so that a later revision -- including one an automated
    improvement loop proposes -- cannot quietly remove one and still compile. An omitted
    check is not a cheaper funnel; it is a different experiment.

    The invariant is about presence, not adequacy: it cannot say whether the docking box was
    right or the redock threshold sensible. Those remain the gate calibration's question.
    """
    unknown = sorted(set(required) - set(CAPABILITY_BACKENDS))
    if unknown:
        raise ValueError(
            f"unknown required capability {unknown}; declared names are "
            f"{sorted(CAPABILITY_BACKENDS)}. A typo must not read as a satisfied requirement."
        )
    missing = sorted(set(required) - provided_capabilities(tiers))
    if missing:
        raise ValueError(
            f"this composition provides no {missing} step, which this campaign declared "
            "indispensable. Add a backend for it, or change the campaign's declaration "
            "deliberately -- a revision that drops a required step is a different experiment, "
            f"not an improvement. Backends that satisfy each name: "
            f"{ {name: CAPABILITY_BACKENDS[name] for name in missing} }"
        )


def compose(name: str, tiers: Sequence[Mapping[str, Any]], *,
            target: Mapping[str, Any] | None = None,
            standardize_settings: Mapping[str, Any] | None = None,
            finalize: Mapping[str, Any] | None = None,
            require: Sequence[str] = ()) -> dict[str, Any]:
    """Build only the requested tiers; a one-component cascade is a first-class design.

    Tier mode may be serial/all/any/at_least. The underlying compiler checks input contracts,
    ordering, ambiguous producers and prerequisites; missing dependencies are errors, not an
    invitation to restore the default cascade. Registration settings are explicit in the result.

    ``require`` names steps this campaign treats as indispensable -- for an affinity loop,
    normally ``("docking", "redock", "affinity")``. It is empty by default and constrains
    nothing unless a caller declares it; see :func:`check_required`.
    """
    if require:
        check_required(tiers, require)
    load("molcascade")
    from molcascade.cascade.introspect import with_schema_version
    from molcascade.cascade.models import CascadeConfig
    from molcascade.plugins import create_builtin_registry

    registry = create_builtin_registry()
    ingest = "source.delimited_smiles@0.1.0"
    standardize = "chemistry.rdkit_standardize@0.1.0"
    registration = merge_changes({"identity_policy": {"schema_version": 1, "tautomer_policy": "preserve"}},
                                 standardize_settings or {})
    raw = {"schema_version": 2, "kind": "cascade", "name": name,
           "library": {"format": "delimited", "smiles_column": "smiles", "id_column": "id", "delimiter": ","},
           "ingest": {"id": "library", "backend": ingest,
                      "settings": with_schema_version(ingest, {}, registry=registry)},
           "standardize": {"id": "standardize", "backend": standardize,
                           "settings": with_schema_version(standardize, registration, registry=registry)},
           "tiers": [dict(tier) for tier in tiers], "target": dict(target) if target else None,
           "finalize": dict(finalize or {"steps": []})}
    return CascadeConfig.model_validate(raw).model_dump(mode="json")


def catalogue() -> dict[str, Any]:
    """Discover registered criteria/backends, not a mandatory ordering of screening tiers."""
    load("molcascade")
    from molcascade.cascade import catalogue_json
    from molcascade.plugins import create_builtin_registry

    registry = create_builtin_registry()
    result = catalogue_json(available_plugins=[entry.key for entry in registry])
    result["plugins"] = [{"key": entry.key, **entry.descriptor.model_dump(mode="json")}
                         for entry in registry]
    return result
