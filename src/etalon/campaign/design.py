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


def compose(name: str, tiers: Sequence[Mapping[str, Any]], *,
            target: Mapping[str, Any] | None = None,
            standardize_settings: Mapping[str, Any] | None = None,
            finalize: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build only the requested tiers; a one-component cascade is a first-class design.

    Tier mode may be serial/all/any/at_least. The underlying compiler checks input contracts,
    ordering, ambiguous producers and prerequisites; missing dependencies are errors, not an
    invitation to restore the default cascade. Registration settings are explicit in the result.
    """
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
