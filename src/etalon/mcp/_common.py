"""Shared machinery for ETALON's MCP tools, and the one rule that shapes all of them.

The conventions are MolCascade's, deliberately: an agent reading a result from either server should
be looking at the same fields. ``stdout`` belongs to JSON-RPC under stdio transport, so every tool
redirects it; paths are absolute or refused, because a stdio server inherits a working directory the
caller cannot see; and errors carry a ``retryable`` flag, which is the field that separates "call this
again" from "fix the input".

What is specific to ETALON is the cost classification, and it exists because the caller is a model.

A model driving a campaign cannot be asked to confirm 750,000 times, so the governance cannot be
per-action confirmation the way PRISM's small-scale workflow is. It is per-*kind*. Every tool here
declares what it spends:

``FREE``   reads files and arithmetic. Costs nothing, changes nothing, and refuses things. Call these
          as often as useful -- they are the whole point of the server.
``CHEAP``  minutes of CPU, or a GPU for seconds. Safe to call on a loop.
``SPENDS`` GPU-hours to GPU-days. Every one of these requires a plan to have been made and returns
          its own cost before it starts.
``NEVER``  cannot be done by a model at all. ``recommend_waiver`` is the only one, and it records a
          recommendation rather than granting anything, because a waiver's entire value is that a
          person accepted a consequence and can be asked about it later (ADR 0003).

The classification is in the tool's own description, so a model reading the tool list sees it before
choosing, rather than discovering it from an error.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import threading
from collections.abc import Callable
from contextlib import contextmanager
from enum import StrEnum
from functools import partial, wraps
from pathlib import Path
from typing import Any

from etalon.active.store import StateError

logger = logging.getLogger("etalon.mcp")


class _WindowsPipeReader(io.RawIOBase):
    """Read only bytes already available on a borrowed Windows pipe handle.

    In the tested Windows/Conda environment, a pending synchronous stdin read can
    stall the first NumPy DLL import, even on another thread. Waiting with
    PeekNamedPipe avoids that blocking read without eagerly loading scientific
    dependencies. The MCP SDK still owns decoding, messages and worker lifetime.
    There must be exactly one reader of the handle, as with ordinary MCP stdio.
    """

    def __init__(self, fd: int, handle: int, kernel: Any, ctypes: Any) -> None:
        super().__init__()
        self._fd, self._handle = fd, handle
        self._kernel, self._ctypes = kernel, ctypes
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def readable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._fd

    def readinto(self, target: Any) -> int:
        if self.closed:
            raise ValueError("read of closed Windows pipe wrapper")
        if not len(target):
            return 0
        # The SDK reads in an AnyIO worker. Honor cancellation of that reader,
        # so transport shutdown need not wait for a client to close its pipe.
        # Direct ordinary-thread use has no AnyIO cancellation scope.
        try:
            from anyio.from_thread import check_cancelled
            check_cancelled()
        except (ImportError, RuntimeError):
            check_cancelled = None
        ctypes = self._ctypes
        available = ctypes.c_ulong()
        while not self._stop.is_set():
            if check_cancelled is not None:
                check_cancelled()
            if not self._kernel.PeekNamedPipe(
                self._handle, None, 0, None, ctypes.byref(available), None
            ):
                error = ctypes.get_last_error()
                if error in {109, 232, 233}:  # Broken, closing or disconnected pipe.
                    return 0
                raise ctypes.WinError(error)
            if available.value:
                size = min(len(target), available.value)
                buffer = ctypes.create_string_buffer(size)
                received = ctypes.c_ulong()
                success = self._kernel.ReadFile(
                    self._handle, buffer, size, ctypes.byref(received), None
                )
                if not success:
                    error = ctypes.get_last_error()
                    if error in {109, 232, 233}:
                        return 0
                    if error != 234:  # A message pipe may have more data after this chunk.
                        raise ctypes.WinError(error)
                target[: received.value] = buffer.raw[: received.value]
                return received.value
            self._stop.wait(0.01)
        return 0


def _windows_pipe_input(stream: Any) -> Any | None:
    if sys.platform != "win32":
        return None
    import ctypes
    import msvcrt

    try:
        fd = stream.fileno()
        handle = msvcrt.get_osfhandle(fd)
    except (AttributeError, OSError, ValueError):
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileType.argtypes = (ctypes.c_void_p,)
    kernel.GetFileType.restype = ctypes.c_ulong
    if kernel.GetFileType(handle) != 3:  # FILE_TYPE_PIPE; consoles and files stay unchanged.
        return None
    kernel.PeekNamedPipe.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
    )
    kernel.PeekNamedPipe.restype = ctypes.c_int
    kernel.ReadFile.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
    )
    kernel.ReadFile.restype = ctypes.c_int
    # Closing this RawIOBase never closes the borrowed OS handle or original stdin.
    return io.TextIOWrapper(io.BufferedReader(_WindowsPipeReader(fd, handle, kernel, ctypes)),
                            encoding="utf-8", errors="replace")


@contextmanager
def stdio_input():
    """Keep lazy native imports safe on Windows pipes; leave other inputs intact."""
    original = sys.stdin
    replacement = _windows_pipe_input(original)
    if replacement is None:
        yield
        return
    try:
        sys.stdin = replacement
        yield
    finally:
        sys.stdin = original
        # Signal before BufferedReader.close() acquires its lock: a reader may
        # still be waiting for input during an exception or cancellation.
        if not replacement.closed:
            replacement.buffer.raw.stop()
        replacement.close()


class Cost(StrEnum):
    FREE = "free"
    CHEAP = "cheap"
    SPENDS = "spends"
    NEVER = "never-by-a-model"


class StdoutToStderr:
    """Point stdout at stderr for the duration of a call.

    Under stdio transport every byte on stdout is protocol. ETALON's own code is quiet, but it calls
    into MolCascade and PRISM, both of which print progress, and one stray line corrupts the session.

    Nesting and concurrency are handled by a depth count rather than by per-instance save/restore,
    and the difference is not academic. ``sys.stdout`` is process-global, so two overlapping calls
    each saving what they found produced this: the first saves the real stdout and installs stderr,
    the second saves *stderr*, the first restores the real stdout, and the second then restores
    stderr -- leaving ``sys.stdout`` permanently pointing at ``sys.stderr``. Under stdio transport
    that is a session that has stopped speaking protocol, with no error anywhere. Only the outermost
    context saves and restores now, and the saved handle lives on the class beside the count that
    decides who is outermost.
    """

    _lock = threading.Lock()
    _depth = 0
    _saved: Any = None

    def __enter__(self) -> StdoutToStderr:
        with StdoutToStderr._lock:
            if StdoutToStderr._depth == 0:
                StdoutToStderr._saved = sys.stdout
                sys.stdout = sys.stderr
            StdoutToStderr._depth += 1
        return self

    def __exit__(self, *_: object) -> None:
        with StdoutToStderr._lock:
            StdoutToStderr._depth -= 1
            if StdoutToStderr._depth == 0:
                sys.stdout = StdoutToStderr._saved
                StdoutToStderr._saved = None


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def ok(**payload: Any) -> str:
    return dumps({"ok": True, **payload})


def fail(
    code: str,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
    **context: Any,
) -> str:
    """An error in the shape MolCascade's tools use.

    ``hint`` is never omitted in practice: a model that cannot tell what to do next will either give
    up on something fixable or retry something permanent, and both look like the tool failing.
    """

    return dumps(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "hint": hint,
                "retryable": retryable,
                "context": context,
            },
        }
    )


def absolute_path(value: str, *, label: str) -> Path:
    """Resolve a path or refuse it, with the path in the error.

    A relative path means different files depending on how the client was launched, which is not a
    property the caller can see.
    """

    path = Path(value)
    if not path.is_absolute():
        raise ValueError(
            f"{label} must be an absolute path; got {value!r}. A stdio server inherits the working "
            "directory of whatever launched it, so a relative path names different files for "
            "different callers."
        )
    return path


#: Exception types worth calling again. Everything else that reaches the boundary unrecognised is
#: reported as permanent, and the asymmetry is the argument: an ``AttributeError`` marked retryable
#: tells a model to repeat a deterministic defect forever, while a genuinely transient failure marked
#: permanent costs one call and a sentence to the operator. ``OSError`` covers the connection,
#: timeout and device errors that are worth a second attempt; a ``TypeError`` from ETALON's own code
#: is not one of them.
_TRANSIENT: tuple[type[BaseException], ...] = (OSError,)


def _refusal(error: BaseException) -> str | None:
    """The hint for a deliberate refusal, or ``None`` for a genuine surprise.

    Two of this project's governance refusals reached a model wearing the wrong clothes, in
    opposite directions, and both were a consequence of classifying by Python's exception
    hierarchy rather than by what the exception means.

    ``NotAuthorized`` subclasses ``PermissionError``, which subclasses ``OSError``, which is
    :data:`_TRANSIENT`. So the one refusal that exists to stop a million-molecule mistake -- no
    gate authorization, an expired or unverifiable signature, or a token minted for a different
    funnel -- was reported as *"it looks transient, a filesystem or device error, calling again is
    reasonable once."* None of its three causes is fixed by calling again.

    ``SweepError`` is a ``RuntimeError`` and therefore not transient, so every deliberate sweep
    refusal -- a prefix that disagrees with the pool, a batch id that already names a run, a batch
    that is not claimable, an outcome already recorded, a non-terminal run -- was reported as
    *"this is a defect in ETALON ... Report it with this message rather than retrying."* The
    remedy was in the message the whole time, and the hint told the reader to disregard it.

    The imports are deferred rather than module-scope so this module stays importable without the
    authority and campaign layers, and so the cost is paid on the error path only.
    """

    from etalon.authority.grant import NotAuthorized
    from etalon.campaign.sweep import SweepError

    if isinstance(error, NotAuthorized):
        return (
            "This is a refusal, not a transient failure: a gate authorization is missing, expired "
            "or minted for a different funnel revision, and calling again will refuse again. "
            "Screen a known-active panel through this cascade, call etalon_calibrate_gates to see "
            "what the gates do to it, then etalon_authorize_gates. A token also does not survive "
            "an MCP server restart unless ETALON_AUTHORITY_KEY is set."
        )
    if isinstance(error, SweepError):
        return (
            "This is a refusal the sweep made deliberately, not a defect: read the message, which "
            "names what to do. Calling again unchanged will refuse again."
        )
    return None


def tool(cost: Cost) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Wrap a tool: redirect stdout, turn exceptions into the error shape, record the cost.

    The cost is recorded on the function as ``etalon_cost``. It is **not** prepended to the
    description by anything: this docstring used to say the server did that, and the server does
    not. Every FREE/CHEAP/SPENDS/NEVER prefix a model reads is a hand-typed literal at the start of
    the tool's own docstring, and the only reader of ``etalon_cost`` is :func:`etalon.mcp.server.costs`.
    The agreement between the two is therefore a convention one typo away from being wrong, which
    is why ``tests/test_mcp.py`` now asserts it for every registered tool rather than trusting it.
    """

    def decorate(function: Callable[..., str]) -> Callable[..., str]:
        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                with StdoutToStderr():
                    return function(*args, **kwargs)
            except StateError as error:
                logger.warning("%s refused: %s", function.__name__, error)
                return fail(
                    "StateError", str(error),
                    hint="Refresh status and the current plan or observation. Reconcile unfinished work before submitting a new execution.",
                    retryable=False, tool=function.__name__,
                )
            except (ValueError, KeyError) as error:
                logger.warning("%s refused: %s", function.__name__, error)
                return fail(
                    type(error).__name__,
                    str(error),
                    hint="Fix the input and call again; this is not a transient failure.",
                    retryable=False,
                    tool=function.__name__,
                )
            except FileNotFoundError as error:
                return fail(
                    "FileNotFound",
                    str(error),
                    hint="Check the path. Paths must be absolute.",
                    retryable=False,
                    tool=function.__name__,
                )
            except FileExistsError as error:
                return fail(
                    "AlreadyExists", str(error),
                    hint="Inspect the existing run or artifact. Use a new identifier only for a deliberate new run.",
                    retryable=False, tool=function.__name__,
                )
            except Exception as error:  # noqa: BLE001 -- the boundary must not leak a traceback
                refusal = _refusal(error)
                if refusal is not None:
                    # A refusal is not a crash and must not be logged as one: ``logger.exception``
                    # files a traceback, which is what an operator greps for when something broke.
                    logger.warning("%s refused: %s", function.__name__, error)
                    return fail(
                        type(error).__name__, str(error),
                        hint=refusal, retryable=False, tool=function.__name__,
                    )
                logger.exception("%s raised", function.__name__)
                transient = isinstance(error, _TRANSIENT)
                return fail(
                    type(error).__name__,
                    str(error),
                    hint=(
                        "Unexpected, and it looks transient -- a filesystem or device error. Calling "
                        "again is reasonable once. If the call involved a vendored asset, check "
                        "etalon_infrastructure first: a shadowed import is the usual cause."
                        if transient
                        else "Unexpected, and calling again will produce it again -- this is a "
                        "defect in ETALON or in an input it did not validate. Report it with this "
                        "message rather than retrying. If the call involved a vendored asset, check "
                        "etalon_infrastructure first: a shadowed import is the usual cause."
                    ),
                    retryable=transient,
                    tool=function.__name__,
                )

        wrapper.etalon_cost = cost  # type: ignore[attr-defined]
        return wrapper

    return decorate


def threaded_tool(cost: Cost) -> Callable[[Callable[..., str]], Callable[..., Any]]:
    """Await bounded blocking work in a thread so MCP inspection/heartbeats remain responsive.

    This is not a detached or restartable worker. The same error and stdout boundary is
    applied inside the thread, and the call still waits for its actual result.
    """
    def decorate(function: Callable[..., str]) -> Callable[..., Any]:
        guarded = tool(cost)(function)

        @wraps(function)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            from anyio import to_thread

            return await to_thread.run_sync(partial(guarded, *args, **kwargs))

        wrapper.etalon_cost = cost  # type: ignore[attr-defined]
        return wrapper

    return decorate


#: Where the SDK's server class has lived, newest first. Both the name and the path moved between
#: generations -- 2.x renamed ``FastMCP`` to ``MCPServer`` and re-exported it from ``mcp.server``
#: itself -- and guessing one combination produces a failure whose message is about an import rather
#: than about a version. Found by inspection of the installed package rather than from a changelog.
_SERVER_LOCATIONS: tuple[tuple[str, str], ...] = (
    ("mcp.server", "MCPServer"),
    ("mcp.server.mcpserver", "MCPServer"),
    ("mcp.server.fastmcp", "FastMCP"),
    ("fastmcp", "FastMCP"),
)


def require_mcp() -> Any:
    """Import the MCP SDK's server class, wherever this installation keeps it.

    The SDK is an optional extra. ETALON's planning tools are all available from the CLI, which needs
    none of it, so somebody who never drives the package from an agent should not have to install a
    server framework.
    """

    import importlib

    tried: list[str] = []
    for module_name, class_name in _SERVER_LOCATIONS:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            tried.append(f"{module_name} (not importable)")
            continue
        found = getattr(module, class_name, None)
        if found is not None:
            return found
        tried.append(f"{module_name}.{class_name} (absent)")
    raise RuntimeError(
        "no MCP server class found. Looked in: "
        + "; ".join(tried)
        + ". Install or upgrade the mcp package. ETALON's CLI (`python -m etalon`) needs none of "
        "this and exposes the same planning tools."
    )


__all__ = [
    "Cost",
    "StdoutToStderr",
    "absolute_path",
    "dumps",
    "fail",
    "ok",
    "require_mcp",
    "tool",
    "threaded_tool",
]
