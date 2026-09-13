"""Read what a plugin's configuration model actually declares.

Plugin configuration models forbid unknown fields, so a helpfully-supplied
``schema_version`` is a hard validation error for any plugin that does not
version its configuration.  Defaults, lowering, and the builder all need the
same answer to the same question — *does this backend have somewhere to put
this setting?* — so they ask it here rather than each keeping a private list of
which plugins are versioned.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from molcascade.errors import PluginError
from molcascade.plugins.registry import BUILTIN_PLUGIN_REGISTRY, PluginRegistry

SCHEMA_VERSION_FIELD = "schema_version"


def active_registry(registry: PluginRegistry | None) -> PluginRegistry:
    """Return the caller's registry, or the shared built-in one.

    The built-in registry is a module singleton, so falling back to it costs
    nothing and keeps callers from rebuilding a registry per criterion.
    """

    return BUILTIN_PLUGIN_REGISTRY if registry is None else registry


def config_fields(backend: str, *, registry: PluginRegistry | None = None) -> frozenset[str]:
    """Names the backend's configuration model accepts, empty if unknown."""

    try:
        entry = active_registry(registry).entry(backend)
    except PluginError:
        return frozenset()
    model = getattr(entry.plugin, "config_model", None)
    return frozenset(getattr(model, "model_fields", None) or ())


def declares(backend: str, field: str, *, registry: PluginRegistry | None = None) -> bool:
    """Whether the backend's configuration model has ``field``."""

    return field in config_fields(backend, registry=registry)


def with_schema_version(
    backend: str,
    settings: Mapping[str, Any],
    *,
    registry: PluginRegistry | None = None,
) -> dict[str, Any]:
    """Copy ``settings``, pinning the config schema version where one exists.

    Versioned plugin configurations are pinned so a future schema bump is a
    loud mismatch rather than a silent reinterpretation of an old file.
    Unversioned ones are left alone, because writing the field would be
    rejected outright.
    """

    result = dict(settings)
    if SCHEMA_VERSION_FIELD in result:
        return result
    if declares(backend, SCHEMA_VERSION_FIELD, registry=registry):
        result[SCHEMA_VERSION_FIELD] = 1
    return result


__all__ = [
    "SCHEMA_VERSION_FIELD",
    "active_registry",
    "config_fields",
    "declares",
    "with_schema_version",
]
