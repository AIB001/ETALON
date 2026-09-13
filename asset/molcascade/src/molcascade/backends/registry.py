"""Explicit backend registry and non-installing local probes."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from importlib import metadata
from types import MappingProxyType

from molcascade.backends.models import (
    Availability,
    BackendInterface,
    BackendSpec,
    BackendStatus,
    Capability,
    LicenseClass,
    ProbePolicy,
)


def _platform_name() -> str:
    value = platform.system().lower()
    if value == "windows":
        return "windows"
    if value == "darwin":
        return "darwin"
    return "linux"


def _license_block_reason(spec: BackendSpec, policy: ProbePolicy) -> str | None:
    if spec.license_class is LicenseClass.WEAK_COPYLEFT and not policy.allow_weak_copyleft:
        return "deployment policy blocks weak-copyleft backends"
    if spec.license_class is LicenseClass.COPYLEFT and not policy.allow_copyleft:
        return "deployment policy blocks copyleft backends"
    if spec.license_class is LicenseClass.PROPRIETARY and not policy.allow_proprietary:
        return "deployment policy blocks proprietary backends"
    if spec.license_class is LicenseClass.UNKNOWN and not policy.allow_unknown_license:
        return "deployment policy blocks backends with unknown license terms"
    return None


def probe_backend(
    spec: BackendSpec,
    policy: ProbePolicy | None = None,
) -> BackendStatus:
    """Probe only the local machine; never install or download anything."""

    selected_policy = policy or ProbePolicy()
    platform_name = _platform_name()
    evidence: dict[str, str | bool | list[str]] = {
        "platform": platform_name,
        "offline_declared": spec.offline,
        "license_spdx": spec.license_spdx,
    }
    if platform_name not in spec.platforms:
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=f"backend does not declare support for {platform_name}",
            evidence=evidence,
        )
    blocked = _license_block_reason(spec, selected_policy)
    if blocked:
        return BackendStatus(
            spec=spec,
            availability=Availability.BLOCKED,
            reason=blocked,
            evidence=evidence,
        )
    if spec.interface is BackendInterface.NATIVE:
        return BackendStatus(
            spec=spec,
            availability=Availability.AVAILABLE,
            version=platform.python_version(),
            reason="implemented with the Python standard library",
            evidence=evidence,
        )
    if spec.interface is BackendInterface.PYTHON:
        assert spec.module is not None
        try:
            found = importlib.util.find_spec(spec.module) is not None
        except (ImportError, ModuleNotFoundError, AttributeError, ValueError) as error:
            evidence["probe_error"] = f"{type(error).__name__}: {error}"
            found = False
        if not found:
            return BackendStatus(
                spec=spec,
                availability=Availability.UNAVAILABLE,
                reason=f"Python module is not installed: {spec.module}",
                evidence=evidence,
            )
        version: str | None = None
        if spec.distribution:
            try:
                version = metadata.version(spec.distribution)
            except metadata.PackageNotFoundError:
                evidence["version_probe"] = "distribution metadata not found"
        evidence["module"] = spec.module
        return BackendStatus(
            spec=spec,
            availability=(
                Availability.AVAILABLE
                if version or not spec.distribution
                else Availability.DEGRADED
            ),
            version=version,
            reason=(
                "Python module and distribution metadata found"
                if version or not spec.distribution
                else "module found, but its distribution version is unavailable"
            ),
            evidence=evidence,
        )

    executable = shutil.which(spec.command[0])
    if executable is None:
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=f"local command is not installed: {spec.command[0]}",
            evidence=evidence,
        )
    evidence["executable"] = executable
    version: str | None = None
    if selected_policy.run_version_commands:
        try:
            completed = subprocess.run(
                [executable, *spec.command[1:]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=selected_policy.command_timeout_seconds,
                check=False,
            )
            output = completed.stdout.strip().splitlines()
            version = output[0][:256] if output else None
            evidence["exit_code"] = str(completed.returncode)
        except (OSError, subprocess.SubprocessError) as error:
            return BackendStatus(
                spec=spec,
                availability=Availability.DEGRADED,
                reason=f"command exists but version probe failed: {error}",
                evidence=evidence,
            )
    return BackendStatus(
        spec=spec,
        availability=Availability.AVAILABLE,
        version=version,
        reason="local executable found",
        evidence=evidence,
    )


class BackendRegistry:
    def __init__(self, specs: Iterable[BackendSpec] = ()) -> None:
        self._specs: dict[str, BackendSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: BackendSpec) -> None:
        if spec.id in self._specs:
            raise ValueError(f"backend is already registered: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, backend_id: str) -> BackendSpec:
        try:
            return self._specs[backend_id]
        except KeyError as error:
            raise KeyError(f"unknown backend: {backend_id}") from error

    def specs(self, capability: Capability | None = None) -> tuple[BackendSpec, ...]:
        values = self._specs.values()
        if capability is not None:
            values = (spec for spec in values if spec.capability is capability)
        return tuple(
            sorted(values, key=lambda spec: (spec.capability.value, spec.tier.value, spec.id))
        )

    def probe_all(
        self,
        policy: ProbePolicy | None = None,
        capability: Capability | None = None,
    ) -> tuple[BackendStatus, ...]:
        return tuple(probe_backend(spec, policy) for spec in self.specs(capability))

    def snapshot(self) -> MappingProxyType[str, BackendSpec]:
        return MappingProxyType(dict(self._specs))

    def __iter__(self) -> Iterator[BackendSpec]:
        return iter(self.specs())

    def __len__(self) -> int:
        return len(self._specs)


__all__ = ["BackendRegistry", "probe_backend"]

