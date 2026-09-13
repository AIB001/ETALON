"""Where a tool is installed is a fact about a machine, not about a campaign.

Four adapters call out to software that lives in an environment of its own --
the three docking engines and AiZynthFinder -- and every one of them needs an
absolute path to say where.  Those paths are the same on every run on a given
host and different on every host, which is exactly the wrong shape for a
``cascade.json``: the file is the screening *policy*, meant to be reviewed,
committed and handed to someone else, and a policy carrying ``/home/you/...``
is one the recipient has to edit before it will start.

So a cascade may leave them out, and three things answer in turn:

1. a value in the config, which always wins;
2. ``MOLCASCADE_<ENGINE>_<FIELD>``, set once per machine;
3. the layout ``envs/bootstrap.sh`` creates, looked for on disk.

None of this is the ``PATH`` probing these adapters refuse to do.  That refusal
is about *guessing*: resolving a bare command name would find whatever happens
to be first on a ``PATH`` the isolated environment is deliberately absent from,
and "something called unidock" is not the same claim as "the Uni-Dock this
project installed".  Every candidate here is a specific absolute path that this
project's own installer writes, each is validated exactly as a typed-in value
would be, and each travels into the stage cache key and the provenance record
the same way -- so a run says which program produced its numbers whether or not
anybody typed the path.

What *is* new is that this module reads the filesystem, which the doc comment
here used to forbid.  The rule it was protecting still holds and is worth
stating precisely: config validation must work on a laptop that is not the
machine the run will happen on.  Discovery cannot break that, because it only
ever *supplies* a value -- a laptop that finds nothing leaves the field blank,
and a blank machine path is valid.  Whether the path is really there is settled
by :func:`molcascade.backends.preflight.preflight_engine_paths`, on the host
that is about to do the work, before the first molecule is read.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "MachinePath",
    "PathKind",
    "absolute_path",
    "backend_root",
    "conda_environment",
    "conda_environment_roots",
    "discover_machine_path",
    "engine_path",
    "environment_key",
    "fill_machine_paths",
    "path_from_environment",
]

#: What the path is expected to be when the preflight looks at it.  Split from
#: plain ``file`` because "present but not executable" is a different mistake
#: with a different fix -- a downloaded binary nobody chmod'ed -- and saying
#: "not found" about it sends the operator looking for the wrong thing.
PathKind = Literal["executable", "file", "directory"]


def absolute_path(value: str, *, field: str) -> str:
    """Reject anything that is not an absolute path to a specific file.

    A bare name would be resolved against this process's ``PATH`` or working
    directory, and both are the wrong answer: an isolated engine lives in an
    environment that deliberately is not on this ``PATH``, and a run's working
    directory is not a stable part of its identity.
    """

    candidate = Path(value.strip()).expanduser()
    if not value.strip() or not candidate.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return str(candidate)


def environment_key(engine_id: str, field: str) -> str:
    """Name the environment variable that can stand in for one engine path.

    ``MOLCASCADE_KARMADOCK_EXECUTABLE``, and so on, matching the shape of
    ``MOLCASCADE_ASSET_ROOT`` in the asset store.
    """

    return f"MOLCASCADE_{engine_id.upper()}_{field.upper()}"


def path_from_environment(engine_id: str, field: str) -> str | None:
    """Read one engine path override, treating blank as unset."""

    if not engine_id:
        return None
    return os.environ.get(environment_key(engine_id, field), "").strip() or None


def backend_root() -> Path:
    """The directory ``envs/bootstrap.sh`` provisions out-of-band tools into.

    Same variable and same default as the script, so the two cannot drift: a
    release binary that is not a package, and a checkout that is not an
    installation, both land here.
    """

    configured = os.environ.get("MOLCASCADE_BACKEND_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "molcascade-backends"


def conda_environment_roots() -> tuple[Path, ...]:
    """The directories that could hold a sibling environment, best guess first.

    ``MOLCASCADE_CONDA_ENVS`` replaces the list outright, for a host whose
    environments are somewhere this cannot infer -- a ``conda create --prefix``
    tree, a mamba root, or a plain venv whose parent holds nothing.  Otherwise
    conda's own ``CONDA_ENVS_DIRS`` is honoured first, and then the interpreter
    running this is used as the landmark: ``envs/bootstrap.sh`` creates one
    environment per conflicting dependency set under a shared ``envs/``, so this
    process's prefix is either a sibling of the engine's or its parent.  Both
    shapes are offered because a MolCascade installed into the base environment
    sits one level above its own ``envs/`` rather than beside it, and looking at
    a path that does not exist costs nothing.
    """

    override = os.environ.get("MOLCASCADE_CONDA_ENVS", "").strip()
    if override:
        return tuple(
            Path(entry).expanduser() for entry in override.split(os.pathsep) if entry.strip()
        )
    prefix = Path(sys.prefix)
    roots = [
        Path(entry).expanduser()
        for entry in os.environ.get("CONDA_ENVS_DIRS", "").split(os.pathsep)
        if entry.strip()
    ]
    roots += [prefix.parent, prefix / "envs"]
    return tuple(roots)


def conda_environment(name: str, relative: str) -> tuple[Path, ...]:
    """Where a named sibling environment's own file would be, from inside another."""

    return tuple(root / name / relative for root in conda_environment_roots())


@dataclass(frozen=True, slots=True)
class MachinePath:
    """One installation path an engine needs, and every way a host can answer it.

    Declared on the engine's own config model, next to the field it fills, so
    that the environment variable, the places worth looking, what has to be
    inside once found, and the command that provisions it are one statement
    rather than four that can disagree.
    """

    #: The config field this fills.
    field: str
    #: What the operator should see it called: "the Uni-Dock binary", not
    #: "executable".  A field name is a fact about a Pydantic model.
    label: str
    kind: PathKind = "executable"
    #: Absolute paths to look at, in order, when neither the config nor the
    #: environment named one.  Empty means this project's installer does not
    #: place it, so there is nothing to find and nothing to guess.
    candidates: tuple[Path, ...] = ()
    #: Relative paths that must exist inside a discovered directory for it to be
    #: the thing this needs.  A KarmaDock checkout without its committed
    #: checkpoint is a directory, not a KarmaDock.
    contents: tuple[str, ...] = ()
    #: The exact command that would provision it.  This is what the preflight
    #: prints, so it has to be runnable as written.
    remedy: str = ""
    #: Extra sentence for the preflight message when the remedy needs context.
    note: str = ""

    def variable(self, engine_id: str) -> str:
        return environment_key(engine_id, self.field)


def discover_machine_path(spec: MachinePath) -> str | None:
    """Return the first candidate that is really there, or ``None``.

    Existence is the whole test.  Deciding a candidate is *the right one* is the
    preflight's job on the run host, where a wrong answer can be reported
    instead of silently accepted -- and where ``contents`` is checked, which is
    what separates a KarmaDock checkout from an empty directory of that name.
    """

    for candidate in spec.candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            # An unreadable parent is not an answer; try the next shape.
            continue
    return None


def fill_machine_paths(
    data: Any,
    *,
    engine_id: str,
    paths: Sequence[MachinePath],
) -> Any:
    """Supply the machine-local engine paths a cascade did not carry.

    Requiring them in every ``cascade.json`` makes the config non-portable and
    forces the operator to re-type the same two paths into every config the
    builder generates -- and the builder, running on a laptop, cannot type them
    either.  Answering here fixes it once for every cascade run on the machine.

    A value already present in the config always wins, so a cascade that names
    its own paths is unaffected.  See the module docstring for why reading the
    filesystem is safe in a validator and why this is not ``PATH`` probing.
    """

    if not isinstance(data, Mapping):
        return data
    filled: dict[str, Any] | None = None
    for spec in paths:
        current = data.get(spec.field)
        if not _unanswered(current):
            continue
        replacement = path_from_environment(engine_id, spec.field)
        if replacement is None:
            replacement = discover_machine_path(spec)
        if replacement is None:
            continue
        if filled is None:
            filled = dict(data)
        filled[spec.field] = replacement
    return data if filled is None else filled


def _unanswered(current: Any) -> bool:
    """Is this config value still an open question a host can answer?

    A blank string is the ordinary case: the builder ran on a laptop and left
    the box empty.  A field that may be given more than once -- one model
    directory per ensemble member -- is blank when the list is empty, and an
    empty list has to count, because a builder that always writes the key would
    otherwise suppress discovery for ever on every machine.  Anything of the
    wrong type is left exactly as it is, so that Pydantic reports the type error
    against the value the operator actually wrote rather than against a
    discovered path that replaced it.
    """

    if current is None:
        return True
    if isinstance(current, str):
        return not current.strip()
    if isinstance(current, Sequence):
        return not any(str(entry).strip() for entry in current)
    return False


def engine_path(value: str, *, field: str, engine_id: str) -> str:
    """Validate an installation path, and accept "not answered yet" as an answer.

    Blank passes.  It has to: a cascade is authored in a browser on one machine
    and run on another, and the machine that draws it often has no engine
    installed at all -- so rejecting a blank here made the builder's own output
    fail its own validator, which is the one thing a builder exists to prevent.
    Nothing is weakened by allowing it, because a blank path cannot reach an
    engine: :func:`preflight_engine_paths` refuses the run before the first
    molecule is read, naming this field, its variable, where it looked and the
    command that installs it.

    ``engine_id`` is still taken, and still shapes the message for a value that
    is present but relative -- the mistake of typing a command name where an
    absolute path belongs.
    """

    if not value.strip():
        return ""
    try:
        return absolute_path(value, field=field)
    except ValueError:
        variable = environment_key(engine_id, field) if engine_id else ""
        remedy = f"{field} must be an absolute path, not a name to resolve on PATH"
        if variable:
            remedy += f" -- or leave it blank and export {variable} once for this machine"
        raise ValueError(remedy) from None
