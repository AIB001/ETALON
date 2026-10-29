"""Turn a measured machine and a user request into execution lanes.

``environment.detect`` already measures NVIDIA hardware properly -- per-device
index, name, memory, driver and compute capability from ``nvidia-smi``, plus
``torch+cu*`` versus ``torch+cpu`` read out of distribution metadata.  What it
deliberately will not do is *import* a framework to find out whether that
hardware is actually reachable, because importing torch costs seconds and
allocates memory, and doing it as a side effect of "what is on this machine"
would be unacceptable in a CLI.

So the two halves live here instead.  :func:`resource_request` validates requested devices and
an independent CPU worker budget.  :func:`probe_framework_gpu`
answers "can this framework actually see a device", in a short-lived subprocess
so that a broken CUDA install cannot poison the process that asked.

The distinction that runs through the whole module is between a request that
may be degraded and one that may not.  ``--device auto`` on a machine whose
torch is a ``+cpu`` build should fall back to CPU and *say so loudly*; the thing
worth preventing is not the fallback but the silent eight-hour run that follows
it.  ``--device cuda`` on the same machine is a statement of intent and must
fail closed instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from molcascade.environment.models import AcceleratorVendor, GpuDevice, HostEnvironment
from molcascade.errors import ConfigError
from molcascade.parallel.models import StageResources, cpu_lane_count

#: How long a framework probe may take before it is treated as unusable.
#: First-touch CUDA initialisation on a cold driver is genuinely slow, so this
#: is generous; a probe that exceeds it has something wrong with it either way.
PROBE_TIMEOUT_SECONDS = 90.0

_PROBE_SOURCE = """
import json, sys
name = sys.argv[1]
try:
    if name == "torch":
        import torch
        payload = {
            "available": bool(torch.cuda.is_available()),
            "count": int(torch.cuda.device_count()),
            "version": getattr(torch.version, "cuda", None),
        }
    elif name == "onnxruntime":
        import onnxruntime
        providers = list(onnxruntime.get_available_providers())
        payload = {
            "available": "CUDAExecutionProvider" in providers,
            "count": 1 if "CUDAExecutionProvider" in providers else 0,
            "version": onnxruntime.__version__,
        }
    else:
        payload = {"error": "unknown framework"}
except BaseException as error:
    payload = {"error": f"{type(error).__name__}: {error}"[:400]}
sys.stdout.write(json.dumps(payload))
"""

_probe_cache: dict[tuple[str, str], FrameworkProbe] = {}


@dataclass(frozen=True, slots=True)
class FrameworkProbe:
    """What one framework says about the accelerators it can reach."""

    module: str
    available: bool
    device_count: int = 0
    detail: str | None = None

    @property
    def reason(self) -> str:
        if self.available:
            return f"{self.module} reports {self.device_count} usable CUDA device(s)"
        if self.detail:
            return f"{self.module} cannot use CUDA: {self.detail}"
        return f"{self.module} reports no usable CUDA device"


def probe_framework_gpu(module: str, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> FrameworkProbe:
    """Ask a framework, out of process, whether it can actually see a GPU.

    Out of process for two reasons.  A CUDA initialisation that segfaults --
    driver/runtime mismatch is the usual cause -- would otherwise take the
    screening run down with it, and importing torch into the parent would cost
    seconds and hundreds of megabytes in every process that merely wanted to
    plan.  The answer is cached per ``CUDA_VISIBLE_DEVICES`` value, because that
    is the only input to it that this process can change.
    """

    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    cached = _probe_cache.get((module, visible))
    if cached is not None:
        return cached

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE_SOURCE, module],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        payload = json.loads(completed.stdout or "{}")
    except subprocess.TimeoutExpired:
        probe = FrameworkProbe(module, False, detail=f"probe timed out after {timeout:g}s")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        probe = FrameworkProbe(module, False, detail=f"probe failed: {type(error).__name__}")
    else:
        if "error" in payload:
            probe = FrameworkProbe(module, False, detail=str(payload["error"])[:400])
        else:
            probe = FrameworkProbe(
                module,
                bool(payload.get("available")),
                int(payload.get("count") or 0),
                detail=(f"CUDA {payload['version']}" if payload.get("version") else None),
            )
    _probe_cache[(module, visible)] = probe
    return probe


def reset_probe_cache() -> None:
    """Forget cached framework answers.  Exists for tests, not for callers."""

    _probe_cache.clear()


def _nvidia_devices(environment: HostEnvironment) -> tuple[GpuDevice, ...]:
    return tuple(gpu for gpu in environment.gpus if gpu.vendor is AcceleratorVendor.NVIDIA)


def _parse_request(request: str) -> tuple[str, tuple[int, ...]]:
    """Split ``"cuda:0,2"`` into a kind and the physical indices it names."""

    value = (request or "auto").strip().lower()
    if value in {"auto", "cpu"}:
        return value, ()
    if value == "cuda":
        return "cuda", ()
    if not value.startswith("cuda:"):
        raise ConfigError(
            f"unrecognised device request {request!r}",
            code="DEVICE_REQUEST_INVALID",
            hint="Use 'auto', 'cpu', 'cuda', or 'cuda:0,2' naming physical device indices.",
            context={"device": request},
        )
    indices: list[int] = []
    for part in value.removeprefix("cuda:").split(","):
        part = part.strip()
        if not part.isdigit():
            raise ConfigError(
                f"device request {request!r} names {part!r}, which is not a device index",
                code="DEVICE_REQUEST_INVALID",
                hint="Indices are the physical numbers 'nvidia-smi' reports, e.g. 'cuda:0,2'.",
                context={"device": request},
            )
        index = int(part)
        if index not in indices:
            indices.append(index)
    if not indices:
        raise ConfigError(
            f"device request {request!r} names no devices",
            code="DEVICE_REQUEST_INVALID",
            context={"device": request},
        )
    return "cuda", tuple(indices)


def resource_request(
    environment: HostEnvironment,
    *,
    device: str = "auto",
    workers: int | None = None,
) -> StageResources:
    """Validate requested devices and an independent CPU concurrency budget.

    This does not allocate the whole pipeline. CPU adapters use ``on_cpu()``;
    GPU adapters consume the selected devices up to the requested concurrency.
    """
    kind, indices = _parse_request(device)
    cards = _nvidia_devices(environment)
    notes: list[str] = []
    if kind == "cpu":
        devices = ("cpu",)
    elif kind == "cuda":
        available = {gpu.index for gpu in cards}
        chosen = indices or tuple(sorted(available))
        if not chosen or any(index not in available for index in chosen):
            raise ConfigError(
                f"requested CUDA devices are unavailable: {device}",
                code="DEVICE_UNAVAILABLE",
                context={"device": device, "detected": sorted(available)},
            )
        devices = tuple(f"cuda:{index}" for index in chosen)
        notes.append(f"pinned to {len(devices)} explicitly requested NVIDIA device(s)")
    elif cards:
        devices = tuple(f"cuda:{gpu.index}" for gpu in sorted(cards, key=lambda gpu: gpu.index))
    else:
        devices = ("cpu",)
    budget = workers if workers is not None else cpu_lane_count(environment.cpu.usable_cores)
    if devices == ("cpu",):
        devices = ("cpu",) * budget
    return StageResources(
        workers=budget, devices=devices, device_request=device,
        batch_size=environment.plan.recommended_batch_size,
        notes=tuple(notes),
    )


def resolve_framework_lanes(
    resources: StageResources,
    *,
    module: str,
    stage_id: str = "",
) -> StageResources:
    """Confirm a framework can reach the planned cards, or degrade honestly.

    Called by an adapter that is about to import a Python framework, because
    only the adapter knows which one.  A CLI docking engine on the same machine
    is unaffected by torch being a ``+cpu`` build, so this cannot be a run-wide
    decision made in the runner.

    ``device_request == "auto"`` degrades to CPU lanes and records why.  Any
    explicit CUDA request fails closed: the user said which hardware to use,
    and quietly not using it is the failure this whole module exists to
    prevent.
    """

    if not resources.uses_gpu:
        return resources

    probe = probe_framework_gpu(module)
    if probe.available:
        return resources.replace(notes=(*resources.notes, probe.reason))

    if resources.device_request.strip().lower() not in {"auto", ""}:
        where = f" for stage {stage_id!r}" if stage_id else ""
        raise ConfigError(
            f"--device {resources.device_request} was requested{where}, but {probe.reason}",
            code="DEVICE_FRAMEWORK_UNAVAILABLE",
            hint=(
                f"Install a CUDA build of {module}, or use --device auto to let this stage "
                f"fall back to CPU."
            ),
            context={
                "device": resources.device_request,
                "module": module,
                "stage_id": stage_id,
            },
        )

    lanes = ("cpu",) * resources.workers
    return resources.on_cpu().replace(
        notes=(
            *resources.notes,
            f"{len(resources.devices)} GPU lane(s) went unused: {probe.reason}",
            f"degraded to {len(lanes)} CPU lane(s); pass --device cuda to make this an error",
        ),
    )


@contextmanager
def pinned_device(device: str) -> Iterator[None]:
    """Make one CUDA device the only one this process can see.

    ``CUDA_VISIBLE_DEVICES`` rather than ``torch.cuda.set_device`` because it is
    the only mechanism that works for every backend at once.  A pinned child
    sees exactly one card, numbered ``0``, and caps its memory to that card --
    and, critically, it works for libraries that expose no device argument at
    all.  ``admet_ai.ADMETModel`` takes none, and would otherwise be unreachable
    on any card but the first.

    Restores the previous value on the way out so an in-process single-lane run
    does not leak its pinning into the rest of the program.
    """

    if not device.startswith("cuda:"):
        yield
        return
    key = "CUDA_VISIBLE_DEVICES"
    previous = os.environ.get(key)
    os.environ[key] = device.removeprefix("cuda:")
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def describe_lanes(devices: Sequence[str]) -> str:
    """A one-line rendering for the CLI and for audit details."""

    if not devices:
        return "none"
    gpus = [device for device in devices if device.startswith("cuda:")]
    if not gpus:
        return f"{len(devices)} CPU worker(s)"
    return f"{len(gpus)} GPU lane(s): {', '.join(gpus)}"


__all__ = [
    "PROBE_TIMEOUT_SECONDS",
    "FrameworkProbe",
    "describe_lanes",
    "pinned_device",
    "resource_request",
    "probe_framework_gpu",
    "reset_probe_cache",
    "resolve_framework_lanes",
]
