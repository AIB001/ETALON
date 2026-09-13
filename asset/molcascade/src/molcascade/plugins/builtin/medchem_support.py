"""Shared runtime plumbing for the ``medchem`` backed adapters.

``medchem`` answers two different criteria in MolCascade -- structural alerts
and physicochemical rule sets -- and both need the same three things: a
deferred import that explains itself when the package is absent, a way to read
a result column without trusting its shape, and a digest that ties a run to the
rule data rather than to a version string.  They live here so the two adapters
cannot drift apart on any of them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from molcascade.errors import PluginError

MAX_DATA_FILE_BYTES = 64 * 1024 * 1024


def import_medchem() -> Any:
    """Load ``medchem`` on demand, or explain precisely what is missing.

    The import is deferred because ``medchem`` pulls in datamol, pandas and
    loguru.  A run that never selects a medchem backend should not pay for any
    of them, and a machine without them should still be able to list plugins,
    validate configurations, and execute every other stage.
    """

    try:
        import medchem
    except ImportError as error:
        raise PluginError(
            "the medchem backend is not installed in this environment",
            code="MEDCHEM_BACKEND_UNAVAILABLE",
            hint=(
                "Install it yourself -- MolCascade never installs or downloads "
                "anything: pip install medchem (or conda install -c conda-forge medchem)."
            ),
        ) from error
    return medchem


def medchem_version(module: Any) -> str:
    return str(getattr(module, "__version__", "unknown"))


def result_column(frame: Any, name: str, expected_rows: int, family: str) -> list[Any]:
    """Read one column, refusing a table that does not match the input.

    A silently shortened or reordered result would attach one molecule's
    verdict to another, which is the one failure mode a screening audit trail
    cannot detect after the fact.
    """

    if name not in frame:
        raise PluginError(
            "the installed medchem returned an unexpected result shape",
            code="MEDCHEM_RESULT_SHAPE_UNEXPECTED",
            hint=f"Column '{name}' is missing from the {family} result table.",
            context={"family": family, "column": name},
        )
    values = list(frame[name])
    if len(values) != expected_rows:
        raise PluginError(
            "medchem returned a different number of rows than it was given",
            code="MEDCHEM_RESULT_COUNT_MISMATCH",
            context={"family": family, "expected": expected_rows, "received": len(values)},
        )
    return values


def hash_data_file(path: Path, *, code: str) -> str:
    """Bind a run to the rule data that produced it, not just to a version.

    Two installs at the same version can carry different rule tables once a
    user points at their own; a version string alone would make those runs look
    identical in the provenance record.
    """

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DATA_FILE_BYTES:
                    raise PluginError(
                        "medchem rule table is larger than this adapter will read",
                        code=code,
                        context={"path": str(path), "limit_bytes": MAX_DATA_FILE_BYTES},
                    )
                digest.update(chunk)
    except OSError as error:
        raise PluginError(
            "medchem rule table cannot be read",
            code=code,
            context={"path": str(path)},
        ) from error
    return digest.hexdigest()


def local_data_path(filename: str) -> Path:
    """Locate a data file that ships inside the installed ``medchem``."""

    from medchem.utils.loader import get_data_path

    return Path(str(get_data_path(filename=filename)))


def as_bool(value: Any) -> bool:
    """Coerce a numpy or pandas boolean without importing either."""

    return bool(value)


__all__ = [
    "MAX_DATA_FILE_BYTES",
    "as_bool",
    "hash_data_file",
    "import_medchem",
    "local_data_path",
    "medchem_version",
    "result_column",
]
