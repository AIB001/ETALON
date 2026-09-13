"""Explicit plugin registration and allow-listed entry-point discovery."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from importlib import metadata as importlib_metadata
from typing import Any, cast

from molcascade.contracts import DEFAULT_CONTRACT_REGISTRY, ContractRegistry
from molcascade.errors import PluginError
from molcascade.plugins.api import StagePlugin
from molcascade.plugins.manifest import PluginDescriptor

ENTRY_POINT_GROUP = "molcascade.plugins"


@dataclass(frozen=True, slots=True)
class RegisteredPlugin:
    """A plugin implementation plus registry-owned provenance and trust state."""

    plugin: StagePlugin
    descriptor: PluginDescriptor
    origin: str
    trusted: bool
    distribution: str | None = None

    @property
    def key(self) -> str:
        return self.descriptor.key


def _normalise_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


class PluginRegistry:
    """Registry for built-ins and explicitly discovered third-party plugins.

    Construction performs no package scan.  Entry-point imports execute Python
    code and therefore require an explicit allowlist.  Even after discovery a
    third-party plugin remains unavailable to :meth:`get` until it is trusted
    explicitly or the caller opts into an untrusted inspection.
    """

    def __init__(
        self,
        builtins: Iterable[StagePlugin] = (),
        *,
        contracts: ContractRegistry = DEFAULT_CONTRACT_REGISTRY,
    ) -> None:
        self._contracts = contracts
        self._entries: dict[tuple[str, str], RegisteredPlugin] = {}
        for plugin in builtins:
            self.register_builtin(plugin)

    def _validate_plugin(self, plugin: object) -> tuple[StagePlugin, PluginDescriptor]:
        if not isinstance(plugin, StagePlugin):
            raise PluginError(
                "plugin does not implement the StagePlugin protocol",
                code="PLUGIN_PROTOCOL_INVALID",
                context={"actual_type": type(plugin).__name__},
            )
        descriptor = plugin.descriptor
        if not isinstance(descriptor, PluginDescriptor):
            raise PluginError(
                "plugin descriptor is not a PluginDescriptor",
                code="PLUGIN_DESCRIPTOR_INVALID",
                context={"actual_type": type(descriptor).__name__},
            )

        contract_ids = (*descriptor.inputs, *descriptor.outputs)
        unknown = sorted(
            {
                contract_id
                for contract_id in contract_ids
                if contract_id not in self._contracts
            }
        )
        if unknown:
            raise PluginError(
                f"plugin {descriptor.key} declares unknown data contracts",
                code="PLUGIN_CONTRACT_UNKNOWN",
                hint="Use an installed, exact contract version; contracts are never inferred.",
                context={"plugin": descriptor.key, "unknown_contracts": unknown},
            )
        return plugin, descriptor

    def register(
        self,
        plugin: StagePlugin,
        *,
        origin: str = "manual",
        trusted: bool = False,
        distribution: str | None = None,
    ) -> RegisteredPlugin:
        """Register a plugin without ever replacing an existing ID/version."""

        checked_plugin, descriptor = self._validate_plugin(plugin)
        key = (descriptor.id, descriptor.version)
        if key in self._entries:
            existing = self._entries[key]
            raise PluginError(
                f"plugin ID and version are already registered: {descriptor.key}",
                code="PLUGIN_DUPLICATE",
                context={
                    "plugin": descriptor.key,
                    "existing_origin": existing.origin,
                    "new_origin": origin,
                },
            )
        entry = RegisteredPlugin(
            plugin=checked_plugin,
            descriptor=descriptor,
            origin=origin,
            trusted=trusted,
            distribution=distribution,
        )
        self._entries[key] = entry
        return entry

    def register_builtin(self, plugin: StagePlugin) -> RegisteredPlugin:
        """Register a statically imported, distribution-owned built-in."""

        return self.register(plugin, origin="builtin", trusted=True)

    @staticmethod
    def _parse_reference(reference: str) -> tuple[str, str | None]:
        if "@" not in reference:
            if not reference:
                raise PluginError(
                    "plugin reference must not be empty",
                    code="PLUGIN_REFERENCE_INVALID",
                )
            return reference, None
        plugin_id, version = reference.rsplit("@", maxsplit=1)
        if not plugin_id or not version:
            raise PluginError(
                f"invalid plugin reference: {reference!r}",
                code="PLUGIN_REFERENCE_INVALID",
                context={"reference": reference},
            )
        return plugin_id, version

    def entry(self, reference: str) -> RegisteredPlugin:
        """Look up registry metadata without making a trust decision."""

        plugin_id, version = self._parse_reference(reference)
        if version is not None:
            try:
                return self._entries[(plugin_id, version)]
            except KeyError as error:
                raise PluginError(
                    f"plugin is not registered: {reference}",
                    code="PLUGIN_NOT_FOUND",
                    context={"plugin": reference},
                ) from error

        matches = [entry for key, entry in self._entries.items() if key[0] == plugin_id]
        if not matches:
            raise PluginError(
                f"plugin is not registered: {plugin_id}",
                code="PLUGIN_NOT_FOUND",
                context={"plugin": plugin_id},
            )
        if len(matches) > 1:
            versions = sorted(entry.descriptor.version for entry in matches)
            raise PluginError(
                f"plugin reference is ambiguous; specify a version: {plugin_id}",
                code="PLUGIN_VERSION_REQUIRED",
                context={"plugin_id": plugin_id, "versions": versions},
            )
        return matches[0]

    def get(self, reference: str, *, allow_untrusted: bool = False) -> StagePlugin:
        """Resolve an implementation, blocking untrusted third-party code by default."""

        entry = self.entry(reference)
        if not entry.trusted and not allow_untrusted:
            raise PluginError(
                f"plugin is registered but not trusted: {entry.key}",
                code="PLUGIN_NOT_TRUSTED",
                hint="Review the distribution and explicitly trust the exact plugin version.",
                context={
                    "plugin": entry.key,
                    "origin": entry.origin,
                    "distribution": entry.distribution,
                },
            )
        return entry.plugin

    resolve = get

    def trust(self, reference: str) -> RegisteredPlugin:
        """Explicitly trust one exact ID/version after external review."""

        entry = self.entry(reference)
        if "@" not in reference:
            # ``entry`` only reaches here for a uniquely registered bare ID,
            # but trust should remain exact and auditable rather than change
            # behaviour if another version is installed later.
            raise PluginError(
                "trust requires an exact plugin ID@version reference",
                code="PLUGIN_TRUST_VERSION_REQUIRED",
                context={"plugin": reference, "resolved": entry.key},
            )
        trusted_entry = replace(entry, trusted=True)
        key = (entry.descriptor.id, entry.descriptor.version)
        self._entries[key] = trusted_entry
        return trusted_entry

    def ensure_compatible(
        self,
        upstream: str | PluginDescriptor,
        downstream: str | PluginDescriptor,
    ) -> tuple[str, ...]:
        """Return shared contracts or raise a structured pipeline-edge error.

        ``inputs`` currently denotes alternative contracts accepted by a stage,
        while ``outputs`` denotes contracts it can emit.  Named multi-port
        requirements can be added in a future plugin API version without
        weakening this exact-version check.
        """

        upstream_descriptor = (
            upstream if isinstance(upstream, PluginDescriptor) else self.entry(upstream).descriptor
        )
        downstream_descriptor = (
            downstream
            if isinstance(downstream, PluginDescriptor)
            else self.entry(downstream).descriptor
        )
        shared = tuple(sorted(set(upstream_descriptor.outputs) & set(downstream_descriptor.inputs)))
        if not shared:
            raise PluginError(
                (
                    f"plugin contracts are incompatible: {upstream_descriptor.key} -> "
                    f"{downstream_descriptor.key}"
                ),
                code="PLUGIN_CONTRACT_INCOMPATIBLE",
                hint="Select plugins sharing an exact versioned data contract.",
                context={
                    "upstream": upstream_descriptor.key,
                    "upstream_outputs": list(upstream_descriptor.outputs),
                    "downstream": downstream_descriptor.key,
                    "downstream_inputs": list(downstream_descriptor.inputs),
                },
            )
        return shared

    def discover_entry_points(
        self,
        *,
        allowlist: Mapping[str, str | None] | Collection[str],
        group: str = ENTRY_POINT_GROUP,
    ) -> tuple[RegisteredPlugin, ...]:
        """Load allow-listed entry points and register them as untrusted.

        A mapping may pin each entry-point name to an expected distribution
        name.  A collection permits any distribution providing the named entry
        point, which is useful for tests and local development but weaker
        against package-name spoofing.  This function never installs packages.
        """

        if isinstance(allowlist, str):
            raise PluginError(
                "plugin allowlist must be a collection or mapping, not a string",
                code="PLUGIN_ALLOWLIST_INVALID",
            )
        if isinstance(allowlist, Mapping):
            allowed = dict(allowlist)
        else:
            allowed = {name: None for name in allowlist}
        if any(not isinstance(name, str) or not name for name in allowed):
            raise PluginError(
                "plugin allowlist names must be non-empty strings",
                code="PLUGIN_ALLOWLIST_INVALID",
            )
        if any(
            distribution is not None
            and (not isinstance(distribution, str) or not distribution)
            for distribution in allowed.values()
        ):
            raise PluginError(
                "allowlisted distribution names must be non-empty strings or null",
                code="PLUGIN_ALLOWLIST_INVALID",
            )

        discovered: list[RegisteredPlugin] = []
        entry_points = importlib_metadata.entry_points()
        selected = (
            entry_points.select(group=group)
            if hasattr(entry_points, "select")
            else [entry_point for entry_point in entry_points if entry_point.group == group]
        )
        for entry_point in sorted(selected, key=lambda item: (item.name, item.value)):
            if entry_point.name not in allowed:
                continue
            distribution = getattr(getattr(entry_point, "dist", None), "name", None)
            expected_distribution = allowed[entry_point.name]
            if (
                expected_distribution is not None
                and (
                    distribution is None
                    or _normalise_distribution_name(distribution)
                    != _normalise_distribution_name(expected_distribution)
                )
            ):
                raise PluginError(
                    f"entry-point distribution does not match allowlist: {entry_point.name}",
                    code="PLUGIN_DISTRIBUTION_MISMATCH",
                    context={
                        "entry_point": entry_point.name,
                        "expected_distribution": expected_distribution,
                        "actual_distribution": distribution,
                    },
                )
            try:
                loaded: Any = entry_point.load()
                if isinstance(loaded, type) or (
                    not isinstance(loaded, StagePlugin) and callable(loaded)
                ):
                    loaded = loaded()
            except Exception as error:
                raise PluginError(
                    f"failed to load plugin entry point: {entry_point.name}",
                    code="PLUGIN_LOAD_FAILED",
                    context={
                        "entry_point": entry_point.name,
                        "distribution": distribution,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                ) from error

            entry = self.register(
                cast(StagePlugin, loaded),
                origin=f"entry_point:{entry_point.name}",
                trusted=False,
                distribution=distribution,
            )
            discovered.append(entry)
        return tuple(discovered)

    def entries(self) -> tuple[RegisteredPlugin, ...]:
        """Return a stable registry snapshot sorted by ID then version."""

        return tuple(self._entries[key] for key in sorted(self._entries))

    def __contains__(self, reference: object) -> bool:
        if not isinstance(reference, str):
            return False
        try:
            self.entry(reference)
        except PluginError:
            return False
        return True

    def __iter__(self) -> Iterator[RegisteredPlugin]:
        return iter(self.entries())

    def __len__(self) -> int:
        return len(self._entries)


# Built-ins are listed statically rather than discovered from the environment.
# This import is deliberately below the registry class definitions so built-in
# implementations can import the public protocol without a registry cycle.
from molcascade.plugins.builtin import BUILTIN_STAGE_PLUGINS  # noqa: E402

BUILTIN_PLUGINS: tuple[StagePlugin, ...] = BUILTIN_STAGE_PLUGINS


def create_builtin_registry() -> PluginRegistry:
    """Create an isolated registry containing only reviewed built-ins."""

    return PluginRegistry(BUILTIN_PLUGINS)


BUILTIN_PLUGIN_REGISTRY = create_builtin_registry()

__all__ = [
    "BUILTIN_PLUGINS",
    "BUILTIN_PLUGIN_REGISTRY",
    "ENTRY_POINT_GROUP",
    "PluginRegistry",
    "RegisteredPlugin",
    "create_builtin_registry",
]
