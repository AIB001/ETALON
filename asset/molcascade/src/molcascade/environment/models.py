"""Serializable description of the machine a screening run will execute on.

MolCascade is written to be developed on a laptop and deployed on a very
different machine.  A cascade that is authored where there is no GPU may be
executed where there are eight of them, so the software cannot bake either
assumption into the configuration file.  What it can do is *measure* the host
it is presently running on and say so out loud.

Every field is optional-shaped rather than exception-shaped.  A missing value
means "this machine did not tell us", which is a legitimate answer, and never
an error: environment detection must never be the reason a screening run fails
to start.  The one thing detection does refuse to do is guess.  ``None`` is
reported when a number could not be read, not a plausible default.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ENVIRONMENT_SCHEMA_VERSION = 1

_MIB = 1024 * 1024
_GIB = 1024 * 1024 * 1024


class _EnvModel(BaseModel):
    """Frozen, strict, JSON-only base shared by every environment record."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class AcceleratorVendor(StrEnum):
    """Which accelerator family a device belongs to.

    ``NONE`` is a real answer and the common one during development; it is not
    a failure state and nothing downstream should treat it as one.
    """

    NVIDIA = "nvidia"
    AMD = "amd"
    APPLE = "apple"
    NONE = "none"


class GpuDevice(_EnvModel):
    """One accelerator visible to this process.

    Deliberately excludes the device UUID and serial number.  Those identify a
    specific piece of hardware, and this record is written into run provenance
    and into the browser payload, both of which users share.
    """

    index: int = Field(ge=0, le=4095)
    vendor: AcceleratorVendor
    name: str = Field(min_length=1, max_length=256)
    memory_total_mib: int | None = Field(default=None, ge=0, le=2**31 - 1)
    memory_free_mib: int | None = Field(default=None, ge=0, le=2**31 - 1)
    compute_capability: str | None = Field(default=None, max_length=32)
    driver_version: str | None = Field(default=None, max_length=64)

    @property
    def memory_total_gib(self) -> float | None:
        if self.memory_total_mib is None:
            return None
        return round(self.memory_total_mib / 1024, 2)


class CpuInfo(_EnvModel):
    """Processor capacity, as seen from inside whatever container we are in.

    ``usable_cores`` is the number that matters for choosing a worker count:
    it respects CPU affinity and cgroup quota, either of which can be far
    below the physical core count on a shared cluster node.
    """

    logical_cores: int | None = Field(default=None, ge=1, le=8192)
    physical_cores: int | None = Field(default=None, ge=1, le=8192)
    usable_cores: int | None = Field(default=None, ge=1, le=8192)
    model_name: str | None = Field(default=None, max_length=256)
    architecture: str | None = Field(default=None, max_length=64)
    features: tuple[str, ...] = ()
    cgroup_quota_cores: float | None = Field(default=None, ge=0.0, le=8192.0)


class MemoryInfo(_EnvModel):
    """Host memory, and the cgroup ceiling if one is imposed.

    ``effective_total_bytes`` is the smaller of the two.  Reading only
    ``total_bytes`` inside a memory-limited container is how a process ends up
    sizing a batch for 512 GiB and being killed at 16 GiB.
    """

    total_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)
    available_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)
    swap_total_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)
    cgroup_limit_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)

    @property
    def effective_total_bytes(self) -> int | None:
        candidates = [value for value in (self.total_bytes, self.cgroup_limit_bytes) if value]
        return min(candidates) if candidates else None

    @property
    def total_gib(self) -> float | None:
        total = self.effective_total_bytes
        return None if total is None else round(total / _GIB, 2)

    @property
    def available_gib(self) -> float | None:
        if self.available_bytes is None:
            return None
        return round(self.available_bytes / _GIB, 2)


class DiskInfo(_EnvModel):
    """Free space at one path that MolCascade writes to."""

    label: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=4096)
    total_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)
    free_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)

    @property
    def free_gib(self) -> float | None:
        if self.free_bytes is None:
            return None
        return round(self.free_bytes / _GIB, 2)


class FrameworkInfo(_EnvModel):
    """An installed inference framework, identified without importing it.

    Importing ``torch`` costs seconds and allocates memory, and doing so as a
    side effect of asking "what is on this machine" would be rude in a CLI and
    unacceptable while generating a browser payload.  Distribution metadata
    answers both questions we actually care about -- is it installed, and is it
    a CUDA build -- because that is encoded in the version string and in the
    distribution name.
    """

    distribution: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, max_length=64)
    build: str | None = Field(default=None, max_length=64)
    accelerated: bool = False


class PlatformInfo(_EnvModel):
    """Operating system and interpreter."""

    system: str = Field(min_length=1, max_length=64)
    release: str | None = Field(default=None, max_length=128)
    machine: str | None = Field(default=None, max_length=64)
    python_version: str = Field(min_length=1, max_length=64)
    python_implementation: str = Field(min_length=1, max_length=64)
    in_container: bool = False
    wsl: bool = False


class ExecutionPlan(_EnvModel):
    """What this machine should actually be asked to do.

    A recommendation, not a mandate.  It exists so that a cascade authored on a
    laptop does not have to carry hard-coded worker counts and batch sizes that
    were right for that laptop and wrong everywhere else.
    """

    device: str = Field(min_length=1, max_length=32)
    devices: tuple[str, ...] = ()
    """Every accelerator this machine can run a lane on, not just the first.

    ``device`` names one device because a single string is what a summary line
    and a form field can hold.  A four-card node, though, is four lanes, and a
    plan that only ever says ``cuda:0`` is how three quarters of a machine goes
    quietly unused.
    """

    gpu_count: int = Field(ge=0, le=4095)
    recommended_workers: int = Field(ge=1, le=1024)
    recommended_batch_size: int = Field(ge=1, le=1_000_000)
    gpu_backends_runnable: bool = False
    rationale: str = Field(min_length=1, max_length=1024)


class HostEnvironment(_EnvModel):
    """The complete measurement, ready for JSON, provenance, or the browser."""

    schema_version: Literal[1] = ENVIRONMENT_SCHEMA_VERSION
    platform: PlatformInfo
    cpu: CpuInfo
    memory: MemoryInfo
    disks: tuple[DiskInfo, ...] = ()
    accelerator: AcceleratorVendor = AcceleratorVendor.NONE
    gpus: tuple[GpuDevice, ...] = ()
    frameworks: tuple[FrameworkInfo, ...] = ()
    plan: ExecutionPlan
    notes: tuple[str, ...] = ()

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpus)

    @property
    def total_gpu_memory_gib(self) -> float | None:
        values = [gpu.memory_total_mib for gpu in self.gpus if gpu.memory_total_mib is not None]
        return round(sum(values) / 1024, 2) if values else None

    def provenance(self) -> dict[str, Any]:
        """The subset of this measurement worth writing into a run's audit trail.

        A run already measures this machine -- ``_stage_resources`` calls
        :func:`~molcascade.environment.detect_environment`, which forks
        ``nvidia-smi`` -- and then keeps only the lane count and the batch size.
        Everything else was discarded, so a finished run recorded which molecules
        it kept and nothing at all about the machine that decided that.  A score
        is a function of the toolkit that computed it, so provenance without an
        environment is provenance with the interesting part missing.

        Deliberately a summary rather than the whole model.  The audit log is
        append-only and read by people, and the fields left out -- per-disk
        capacity, free GPU memory, the execution plan's own rationale -- describe
        a moment rather than a run.  What is kept is what someone reproducing the
        run would have to match: the interpreter, the toolkit versions, the
        accelerator, and how much of the machine was usable.

        The GPU entries carry no UUID or serial: :class:`GpuDevice` excludes them
        precisely because this record gets shared, and that reasoning does not
        stop being true here.
        """

        return {
            "schema_version": self.schema_version,
            "platform": {
                "system": self.platform.system,
                "release": self.platform.release,
                "machine": self.platform.machine,
                "python_version": self.platform.python_version,
                "python_implementation": self.platform.python_implementation,
                "in_container": self.platform.in_container,
                "wsl": self.platform.wsl,
            },
            "cpu": {
                "model_name": self.cpu.model_name,
                "logical_cores": self.cpu.logical_cores,
                "usable_cores": self.cpu.usable_cores,
            },
            "memory_total_gib": self.memory.total_gib,
            "accelerator": self.accelerator.value,
            "gpus": [
                {
                    "index": gpu.index,
                    "name": gpu.name,
                    "memory_total_mib": gpu.memory_total_mib,
                    "compute_capability": gpu.compute_capability,
                }
                for gpu in self.gpus
            ],
            "frameworks": [
                {
                    "distribution": framework.distribution,
                    "version": framework.version,
                    "build": framework.build,
                    "accelerated": framework.accelerated,
                }
                for framework in self.frameworks
            ],
        }


__all__ = [
    "ENVIRONMENT_SCHEMA_VERSION",
    "AcceleratorVendor",
    "CpuInfo",
    "DiskInfo",
    "ExecutionPlan",
    "FrameworkInfo",
    "GpuDevice",
    "HostEnvironment",
    "MemoryInfo",
    "PlatformInfo",
]
