"""Measure the local machine without importing anything heavy or touching the network.

Three rules govern this module.

*It never raises.*  Detection runs before a screening run and inside the
browser-payload generator.  A machine that answers no questions at all still
produces a valid :class:`HostEnvironment` with ``None`` everywhere and a note
explaining why, because "we could not tell" must not be an outage.

*It never imports an inference framework.*  Asking whether Torch is installed
by importing Torch costs seconds, allocates memory, and on some builds probes
the driver.  Distribution metadata answers the question for free: a wheel named
``torch`` with local version ``+cu128`` is a CUDA build and one with ``+cpu`` is
not, and ``onnxruntime-gpu`` is a different distribution from ``onnxruntime``.

*It reports the ceiling that will actually be enforced.*  On a shared cluster
node -- which is where this software is going -- the host has 512 GiB and the
cgroup gives you 32.  Reading ``/proc/meminfo`` alone is how a process sizes a
batch for memory it is not allowed to have and gets killed by the OOM reaper.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path

from molcascade.environment.models import (
    AcceleratorVendor,
    CpuInfo,
    DiskInfo,
    ExecutionPlan,
    FrameworkInfo,
    GpuDevice,
    HostEnvironment,
    MemoryInfo,
    PlatformInfo,
)

_GIB = 1024 * 1024 * 1024
_SMI_TIMEOUT_SECONDS = 8.0
_MAX_GPUS = 64
_MAX_SMI_OUTPUT_BYTES = 256 * 1024

# Instruction-set extensions that change how fast fingerprinting and numpy
# inference run by a factor worth reporting.  Anything not on this list is
# noise in a screening context and is dropped rather than shown.
_CPU_FEATURES_OF_INTEREST = (
    "avx",
    "avx2",
    "avx512f",
    "avx512bw",
    "avx512vl",
    "avx512vnni",
    "amx_tile",
    "fma",
    "sse4_2",
    "neon",
    "asimd",
    "sve",
)

# Distributions whose presence changes what a cascade can run.  ``accelerated``
# says whether this particular build can reach a GPU, which is not the same
# question as whether a GPU exists.
_FRAMEWORK_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("torch", ("cu", "rocm", "xpu")),
    ("onnxruntime-gpu", ()),
    ("onnxruntime", ()),
    ("tensorflow", ()),
    ("jax", ()),
    ("cupy-cuda12x", ()),
)


def _read_text(path: str | Path, *, limit: int = 1024 * 1024) -> str | None:
    """Read a small pseudo-file, or return ``None`` for any reason at all."""

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except (OSError, ValueError):
        return None


def _first_int(text: str | None) -> int | None:
    if text is None:
        return None
    match = re.search(r"-?\d+", text)
    if match is None:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _detect_platform(notes: list[str]) -> PlatformInfo:
    proc_version = _read_text("/proc/version", limit=4096) or ""
    lowered = proc_version.lower()
    wsl = "microsoft" in lowered or "wsl" in lowered
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container:
        cgroup = _read_text("/proc/1/cgroup", limit=64 * 1024) or ""
        in_container = any(
            marker in cgroup for marker in ("docker", "kubepods", "containerd", "lxc")
        )
    if wsl:
        notes.append(
            "Running under WSL. GPU access requires a Windows driver with WSL CUDA support; "
            "a native Linux host is the supported deployment target."
        )
    return PlatformInfo(
        system=platform.system() or "unknown",
        release=platform.release() or None,
        machine=platform.machine() or None,
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        in_container=in_container,
        wsl=wsl,
    )


def _cgroup_cpu_quota() -> float | None:
    """Return the cgroup CPU allowance in cores, for v2 then v1."""

    v2 = _read_text("/sys/fs/cgroup/cpu.max", limit=256)
    if v2:
        parts = v2.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
            except ValueError:
                return None
            if period > 0 and quota > 0:
                return round(quota / period, 3)
        return None
    quota = _first_int(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", limit=256))
    period = _first_int(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us", limit=256))
    if quota and quota > 0 and period and period > 0:
        return round(quota / period, 3)
    return None


def _cgroup_memory_limit() -> int | None:
    """Return the enforced memory ceiling in bytes, for v2 then v1.

    Both cgroup versions express "no limit" as a number rather than an absence:
    v2 writes the literal ``max`` and v1 writes a value near 2**63.  Treating
    either as a real ceiling would report a nonsense limit, so both are dropped.
    """

    def sane(value: int | None) -> int | None:
        # 2**62 bytes is four exbibytes.  Anything at or above it is the
        # "unlimited" sentinel rather than a ceiling any machine enforces.
        if value is None or value <= 0 or value >= 2**62:
            return None
        return value

    v2 = _read_text("/sys/fs/cgroup/memory.max", limit=256)
    if v2 is not None:
        stripped = v2.strip()
        if stripped == "max":
            return None
        return sane(_first_int(stripped))
    return sane(_first_int(_read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes", limit=256)))


def _physical_cores_from_proc() -> int | None:
    """Count distinct physical cores, which is not the same as thread count."""

    text = _read_text("/proc/cpuinfo", limit=4 * 1024 * 1024)
    if not text:
        return None
    seen: set[tuple[str, str]] = set()
    package = core = None
    for line in text.splitlines():
        if ":" not in line:
            if package is not None and core is not None:
                seen.add((package, core))
            package = core = None
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "physical id":
            package = value
        elif key == "core id":
            core = value
    if package is not None and core is not None:
        seen.add((package, core))
    return len(seen) or None


def _cpu_model_and_features() -> tuple[str | None, tuple[str, ...]]:
    text = _read_text("/proc/cpuinfo", limit=4 * 1024 * 1024)
    model: str | None = None
    features: tuple[str, ...] = ()
    if text:
        for line in text.splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if model is None and key in ("model name", "Model", "cpu model", "Processor"):
                model = value[:256] or None
            elif not features and key in ("flags", "Features"):
                present = set(value.split())
                features = tuple(f for f in _CPU_FEATURES_OF_INTEREST if f in present)
            if model and features:
                break
    if model is None:
        model = (platform.processor() or "").strip()[:256] or None
    return model, features


def _detect_cpu() -> CpuInfo:
    logical = os.cpu_count()
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = logical or 1
    quota = _cgroup_cpu_quota()
    usable = affinity or logical or 1
    if quota is not None and quota >= 1:
        usable = min(usable, int(quota))
    model, features = _cpu_model_and_features()
    return CpuInfo(
        logical_cores=logical,
        physical_cores=_physical_cores_from_proc(),
        usable_cores=max(1, usable),
        model_name=model,
        architecture=platform.machine() or None,
        features=features,
        cgroup_quota_cores=quota,
    )


def _meminfo_bytes() -> dict[str, int]:
    text = _read_text("/proc/meminfo", limit=256 * 1024)
    if not text:
        return {}
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        amount = _first_int(rest)
        if amount is not None:
            # /proc/meminfo is in kibibytes for every field we read.
            values[key.strip()] = amount * 1024
    return values


def _windows_memory() -> tuple[int | None, int | None]:
    """Read total and available memory on Windows through the Win32 API."""

    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except (ImportError, AttributeError, OSError, ValueError):
        return None, None


def _detect_memory() -> MemoryInfo:
    info = _meminfo_bytes()
    total = info.get("MemTotal")
    available = info.get("MemAvailable") or info.get("MemFree")
    swap = info.get("SwapTotal")
    if total is None and sys.platform == "win32":
        total, available = _windows_memory()
    if total is None:
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (AttributeError, ValueError, OSError):
            total = None
    return MemoryInfo(
        total_bytes=total,
        available_bytes=available,
        swap_total_bytes=swap,
        cgroup_limit_bytes=_cgroup_memory_limit(),
    )


def _detect_disks(paths: Iterable[tuple[str, Path]]) -> tuple[DiskInfo, ...]:
    records: list[DiskInfo] = []
    for label, path in paths:
        probe = path
        # A workspace that does not exist yet still has a filesystem; walk up to
        # the nearest existing ancestor rather than reporting nothing.
        for _ in range(64):
            if probe.exists():
                break
            parent = probe.parent
            if parent == probe:
                break
            probe = parent
        total = free = None
        try:
            usage = shutil.disk_usage(probe)
            total, free = usage.total, usage.free
        except (OSError, ValueError):
            pass
        records.append(
            DiskInfo(label=label, path=str(path), total_bytes=total, free_bytes=free)
        )
    return tuple(records)


def _run_query(command: list[str]) -> str | None:
    """Run a fixed-argv hardware query with a timeout and no shell."""

    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_SMI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    payload = completed.stdout[:_MAX_SMI_OUTPUT_BYTES]
    return payload.decode("utf-8", errors="replace")


_NVIDIA_FIELDS_FULL = "index,name,memory.total,memory.free,driver_version,compute_cap"
_NVIDIA_FIELDS_BASIC = "index,name,memory.total,memory.free,driver_version"


def _parse_nvidia_csv(text: str, *, has_compute_cap: bool) -> list[GpuDevice]:
    devices: list[GpuDevice] = []
    for line in text.splitlines():
        row = [cell.strip() for cell in line.split(",")]
        expected = 6 if has_compute_cap else 5
        if len(row) < expected or not row[0]:
            continue
        index = _first_int(row[0])
        if index is None or not (0 <= index < _MAX_GPUS):
            continue
        name = row[1][:256] or "NVIDIA GPU"
        # `--format=...,nounits` drops the unit suffix but keeps "[N/A]" for
        # fields the driver declines to report, which is not an integer.
        total = _first_int(row[2]) if "N/A" not in row[2] else None
        free = _first_int(row[3]) if "N/A" not in row[3] else None
        driver = row[4][:64] or None
        capability = row[5][:32] if has_compute_cap and "N/A" not in row[5] else None
        devices.append(
            GpuDevice(
                index=index,
                vendor=AcceleratorVendor.NVIDIA,
                name=name,
                memory_total_mib=total if total is not None and total >= 0 else None,
                memory_free_mib=free if free is not None and free >= 0 else None,
                compute_capability=capability or None,
                driver_version=driver,
            )
        )
    return devices[:_MAX_GPUS]


def _detect_nvidia(notes: list[str]) -> list[GpuDevice]:
    if shutil.which("nvidia-smi") is None:
        return []
    text = _run_query(
        ["nvidia-smi", f"--query-gpu={_NVIDIA_FIELDS_FULL}", "--format=csv,noheader,nounits"]
    )
    if text is not None:
        devices = _parse_nvidia_csv(text, has_compute_cap=True)
        if devices:
            return devices
    # compute_cap is a comparatively recent query field; older drivers reject
    # the whole query rather than blanking that one column.
    text = _run_query(
        ["nvidia-smi", f"--query-gpu={_NVIDIA_FIELDS_BASIC}", "--format=csv,noheader,nounits"]
    )
    if text is not None:
        devices = _parse_nvidia_csv(text, has_compute_cap=False)
        if devices:
            return devices
    notes.append("nvidia-smi is installed but returned no usable device rows.")
    return []


def _detect_amd(notes: list[str]) -> list[GpuDevice]:
    if shutil.which("rocm-smi") is None:
        return []
    text = _run_query(["rocm-smi", "--showproductname", "--csv"])
    devices: list[GpuDevice] = []
    if text:
        for line in text.splitlines():
            row = [cell.strip() for cell in line.split(",")]
            if len(row) < 2 or not row[0].lower().startswith("card"):
                continue
            index = _first_int(row[0])
            if index is None or not (0 <= index < _MAX_GPUS):
                continue
            devices.append(
                GpuDevice(
                    index=index,
                    vendor=AcceleratorVendor.AMD,
                    name=(row[1][:256] or "AMD GPU"),
                )
            )
    if not devices:
        notes.append(
            "rocm-smi is installed but its device table could not be parsed; "
            "ROCm memory reporting is not used for planning."
        )
    return devices[:_MAX_GPUS]


def _detect_apple() -> list[GpuDevice]:
    if platform.system() != "Darwin" or platform.machine() not in ("arm64", "aarch64"):
        return []
    return [
        GpuDevice(
            index=0,
            vendor=AcceleratorVendor.APPLE,
            name=f"Apple silicon GPU ({platform.machine()})",
        )
    ]


def _detect_gpus(notes: list[str]) -> tuple[AcceleratorVendor, tuple[GpuDevice, ...]]:
    devices = _detect_nvidia(notes)
    if devices:
        return AcceleratorVendor.NVIDIA, tuple(devices)
    devices = _detect_amd(notes)
    if devices:
        return AcceleratorVendor.AMD, tuple(devices)
    devices = _detect_apple()
    if devices:
        return AcceleratorVendor.APPLE, tuple(devices)
    return AcceleratorVendor.NONE, ()


def _detect_frameworks() -> tuple[FrameworkInfo, ...]:
    found: list[FrameworkInfo] = []
    for distribution, accelerated_markers in _FRAMEWORK_PROBES:
        try:
            version = metadata.version(distribution)
        except (metadata.PackageNotFoundError, ValueError, OSError):
            continue
        build = version.partition("+")[2] or None
        accelerated = distribution.endswith("-gpu") or "cuda" in distribution
        if build and accelerated_markers:
            accelerated = any(build.startswith(marker) for marker in accelerated_markers)
        found.append(
            FrameworkInfo(
                distribution=distribution,
                version=version[:64],
                build=build[:64] if build else None,
                accelerated=accelerated,
            )
        )
    return tuple(found)


def _plan(cpu: CpuInfo, memory: MemoryInfo, gpus: tuple[GpuDevice, ...]) -> ExecutionPlan:
    cores = cpu.usable_cores or 1
    # One core is left for the parent process, the writer, and the operating
    # system; saturating every core makes a long run feel like a hung machine.
    workers = max(1, min(32, cores - 1 if cores > 2 else cores))
    total = memory.effective_total_bytes
    if total is None:
        batch = 32_768
        memory_reason = "memory size unknown, so a conservative batch is used"
    elif total < 8 * _GIB:
        batch = 8_192
        memory_reason = f"{round(total / _GIB, 1)} GiB usable memory"
    elif total < 16 * _GIB:
        batch = 16_384
        memory_reason = f"{round(total / _GIB, 1)} GiB usable memory"
    elif total < 64 * _GIB:
        batch = 65_536
        memory_reason = f"{round(total / _GIB, 1)} GiB usable memory"
    else:
        batch = 131_072
        memory_reason = f"{round(total / _GIB, 1)} GiB usable memory"
    if gpus:
        device = "cuda:0" if gpus[0].vendor is AcceleratorVendor.NVIDIA else "gpu:0"
        if gpus[0].vendor is AcceleratorVendor.APPLE:
            device = "mps"
        # Only NVIDIA cards become addressable lanes here, because a lane is
        # pinned with CUDA_VISIBLE_DEVICES and nothing else honours it.  A
        # machine with one Apple or AMD accelerator still reports one lane; it
        # simply cannot report four of them.
        cuda = tuple(f"cuda:{gpu.index}" for gpu in gpus if gpu.vendor is AcceleratorVendor.NVIDIA)
        devices = cuda or (device,)
        rationale = (
            f"{len(gpus)} accelerator(s) detected ({gpus[0].name}); "
            f"{len(devices)} execution lane(s) ({', '.join(devices)}); "
            f"{workers} CPU worker(s) from {cores} usable core(s); {memory_reason}"
        )
    else:
        device = "cpu"
        devices = ()
        rationale = (
            f"no accelerator detected; {workers} CPU worker(s) from {cores} usable "
            f"core(s); {memory_reason}"
        )
    return ExecutionPlan(
        device=device,
        devices=devices,
        gpu_count=len(gpus),
        recommended_workers=workers,
        recommended_batch_size=batch,
        gpu_backends_runnable=bool(gpus),
        rationale=rationale,
    )


def detect_environment(
    *,
    workspace: Path | None = None,
    include_gpus: bool = True,
) -> HostEnvironment:
    """Measure this machine.  Never raises, never installs, never downloads.

    ``include_gpus=False`` skips the vendor tools, which is worth roughly a
    hundred milliseconds and is the right choice when the answer is only being
    used to fill in a form field.
    """

    notes: list[str] = []
    platform_info = _detect_platform(notes)
    cpu = _detect_cpu()
    memory = _detect_memory()
    if include_gpus:
        vendor, gpus = _detect_gpus(notes)
    else:
        vendor, gpus = AcceleratorVendor.NONE, ()
        notes.append("GPU detection was skipped for this measurement.")
    disk_targets: list[tuple[str, Path]] = []
    if workspace is not None:
        disk_targets.append(("workspace", workspace))
    disk_targets.append(("cwd", Path.cwd()))
    disk_targets.append(("temp", Path(os.environ.get("TMPDIR") or "/tmp")))
    seen_paths: set[str] = set()
    unique_targets = []
    for label, path in disk_targets:
        try:
            resolved = str(path.resolve())
        except (OSError, ValueError):
            resolved = str(path)
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        unique_targets.append((label, path))
    if not gpus and include_gpus:
        notes.append(
            "No accelerator was found. Every GPU-only backend will refuse to run here "
            "rather than silently falling back to a slower or different model."
        )
    return HostEnvironment(
        platform=platform_info,
        cpu=cpu,
        memory=memory,
        disks=_detect_disks(unique_targets),
        accelerator=vendor,
        gpus=gpus,
        frameworks=_detect_frameworks(),
        plan=_plan(cpu, memory, gpus),
        notes=tuple(notes),
    )


__all__ = ["detect_environment"]
