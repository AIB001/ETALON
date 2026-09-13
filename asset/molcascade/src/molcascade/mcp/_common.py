"""Shared machinery for the MolCascade MCP submodules.

Four concerns live here, and each exists because of a property of the MCP stdio
transport or of this project rather than because of convenience.

**stdout belongs to JSON-RPC.**  Under stdio transport every byte on stdout is
protocol.  MolCascade's runner and reporters write progress to stdout through
the same ``print`` the CLI uses, so any library call made inside a tool must
have stdout pointed elsewhere first.  :class:`StdoutToStderr` does that, and
every tool in this package wraps its work in it.

**One error shape, and it is the product's own.**  :class:`MolCascadeError`
already carries ``code``, ``hint``, ``retryable`` and a ``context`` mapping, and
``molcascade --json`` already serialises exactly that.  The tools here reuse
:meth:`MolCascadeError.as_dict` rather than inventing a second vocabulary, so an
agent reading a tool result and an operator reading a terminal are looking at
the same fields.  ``retryable`` matters most: it is the field that distinguishes
"call this again" from "fix the input", and an agent that cannot tell those
apart will either give up on a transient failure or hammer a permanent one.

**Paths are absolute or refused.**  A stdio server inherits the working
directory of whatever launched it, which is not a property an agent can see or
reason about.  A relative path would therefore mean different files depending on
how the client was started.  :func:`absolute_path` refuses one instead, with the
path in the error so the caller can fix it without guessing.

**The workspace is resolved once.**  Every run-reading tool needs a
:class:`LocalRunner` over the same workspace, and constructing one twice for the
same call would read the artifact store twice.  :func:`open_runner` builds it
with the builtin plugin registry, which is what a reader needs: resolving an
artifact requires the descriptor that produced it.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("molcascade-mcp")


class StdoutToStderr:
    """Point ``sys.stdout`` at ``sys.stderr`` for the duration of a block.

    Not optional.  ``LocalRunner`` renders a live funnel, ``trace_run`` reports
    per-stage progress and the cascade lowering prints advisories, all through
    ``print``.  One of those lines on stdout ends the session with a JSON parse
    error on the client side and no indication of which tool did it.
    """

    def __enter__(self) -> StdoutToStderr:
        self._original = sys.stdout
        sys.stdout = sys.stderr
        return self

    def __exit__(self, *args: object) -> None:
        sys.stdout = self._original


def dumps(payload: Any) -> str:
    """Serialise a tool result.

    ``default=str`` is deliberate: several of the objects returned here carry
    ``datetime`` and ``Path`` values, and a tool that raises on an unexpected
    type would fail *after* doing the expensive part of the work.  Stringifying
    is lossy for a consumer that wanted a real timestamp and honest about it;
    failing would be neither.
    """

    return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)


def ok(**fields: Any) -> str:
    return dumps({"ok": True, **fields})


def tool(function: Callable[..., Any]) -> Callable[..., str]:
    """Wrap a tool body so every failure returns the project's error shape.

    A bare exception escaping a tool reaches the agent as a transport-level
    error with a Python traceback and no machine-readable code, which is the
    worst of both: unreadable by a person and unclassifiable by a model.  This
    converts a :class:`MolCascadeError` into its own ``as_dict`` payload, and
    anything else into the same envelope with the exception type as the code so
    that an unexpected failure is still structured.
    """

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        from molcascade.errors import MolCascadeError

        try:
            with StdoutToStderr():
                return function(*args, **kwargs)
        except MolCascadeError as error:
            logger.error("%s failed: [%s] %s", function.__name__, error.code, error)
            return dumps({"ok": False, "error": error.as_dict()})
        except Exception as error:
            logger.exception("%s raised", function.__name__)
            return dumps(
                {
                    "ok": False,
                    "error": {
                        "category": "UNEXPECTED",
                        "code": type(error).__name__,
                        "message": str(error),
                        "hint": (
                            "This is not a MolCascade error code, so it is either a bug "
                            "in the adapter or a failure in a third-party library. The "
                            "server log on stderr carries the traceback."
                        ),
                        "retryable": False,
                        "context": {"tool": function.__name__},
                    },
                }
            )

    return wrapper


def absolute_path(value: str, *, field: str, must_exist: bool = True) -> Path:
    """Resolve one caller-supplied path, refusing a relative one.

    ``must_exist`` is false for outputs, where the point is that the file is not
    there yet; the parent directory is still required, because creating a tree
    the caller did not ask for is how a typo becomes a directory.
    """

    from molcascade.errors import MolCascadeError

    if not value:
        raise MolCascadeError(
            f"{field} is required",
            code="MCP_PATH_REQUIRED",
            context={"field": field},
        )
    path = Path(value)
    if not path.is_absolute():
        raise MolCascadeError(
            f"{field} must be an absolute path",
            code="MCP_PATH_NOT_ABSOLUTE",
            hint=(
                "A stdio server inherits its working directory from the client, so a "
                "relative path names a different file depending on how the client was "
                "started. Pass the full path."
            ),
            context={"field": field, "path": value},
        )
    if must_exist and not path.exists():
        raise MolCascadeError(
            f"{field} does not exist",
            code="MCP_PATH_NOT_FOUND",
            context={"field": field, "path": str(path)},
        )
    if not must_exist and not path.parent.is_dir():
        raise MolCascadeError(
            f"the parent directory of {field} does not exist",
            code="MCP_PARENT_NOT_FOUND",
            hint=(
                "Create the directory first; this adapter will not create a tree "
                "for an output path."
            ),
            context={"field": field, "path": str(path), "parent": str(path.parent)},
        )
    return path


def open_runner(workspace: str) -> Any:
    """A :class:`LocalRunner` over one workspace, with the builtin registry.

    The registry is not decoration.  Reading a finished run means resolving
    artifacts, and resolving an artifact means knowing the descriptor of the
    plugin that produced it; a runner without a registry can load run state but
    not the data the state points at.
    """

    from molcascade.plugins import create_builtin_registry
    from molcascade.runtime import LocalRunner

    root = absolute_path(workspace, field="workspace")
    return LocalRunner(root, plugins=create_builtin_registry())


def require_mcp() -> Any:
    """Import the MCP SDK's server class, across both of its generations.

    The SDK is an optional extra.  MolCascade's own dependency set is
    deliberately narrow and a screening run needs none of it, so a user who
    never drives the package from an agent should not be made to install a
    server framework.

    Two generations are supported because two are in use.  SDK 2.x renamed
    ``FastMCP`` to ``MCPServer`` at a new import path; 1.x has only the old one.
    The decorator API this package relies on -- ``tool``, ``resource``,
    ``prompt`` and ``run(transport=...)`` -- is the same under both, so one
    import shim is the whole difference.  Supporting 1.x is not politeness: the
    environment that already hosts a sibling MCP server is the obvious place to
    register this one, and pinning it to a newer SDK than that environment has
    would make the two mutually exclusive for no reason.
    """

    try:  # SDK 2.x
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ModuleNotFoundError:
        pass
    try:  # SDK 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except ModuleNotFoundError as error:  # pragma: no cover - import-time guard
        raise SystemExit(
            "The MCP SDK is not installed in this interpreter.\n"
            "  pip install 'molcascade[mcp]'   (or: pip install 'mcp[cli]')\n"
            "MolCascade itself does not need it; only this server does."
        ) from error


__all__ = [
    "StdoutToStderr",
    "absolute_path",
    "dumps",
    "logger",
    "ok",
    "open_runner",
    "require_mcp",
    "tool",
]
