"""Bind a molecule library file to the correct ingest plugin and settings.

The browser builder produces a reusable screening policy; the library is
normally supplied on the command line.  This module is the single place that
decides *which reader* a given path needs, so the CLI, the lowering step, and
the tests cannot disagree about it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molcascade.cascade.models import LibraryConfig, LibraryFormat
from molcascade.errors import ConfigError

DELIMITED_SOURCE = "source.delimited_smiles@0.1.0"
XLSX_SOURCE = "source.xlsx@0.1.0"
SDF_SOURCE = "source.sdf@0.1.0"
PARQUET_SOURCE = "source.raw_molecule_parquet@0.1.0"
MOL2_DIRECTORY_SOURCE = "source.mol2_directory@0.1.0"

_FORMAT_PLUGINS: dict[LibraryFormat, str] = {
    LibraryFormat.DELIMITED: DELIMITED_SOURCE,
    LibraryFormat.XLSX: XLSX_SOURCE,
    LibraryFormat.SDF: SDF_SOURCE,
    LibraryFormat.PARQUET: PARQUET_SOURCE,
    LibraryFormat.MOL2_DIRECTORY: MOL2_DIRECTORY_SOURCE,
}

PLUGIN_FORMATS: dict[str, LibraryFormat] = {
    plugin: library_format for library_format, plugin in _FORMAT_PLUGINS.items()
}

_SUFFIX_FORMATS: dict[str, LibraryFormat] = {
    ".csv": LibraryFormat.DELIMITED,
    ".tsv": LibraryFormat.DELIMITED,
    ".tab": LibraryFormat.DELIMITED,
    ".txt": LibraryFormat.DELIMITED,
    ".smi": LibraryFormat.DELIMITED,
    ".smiles": LibraryFormat.DELIMITED,
    ".xlsx": LibraryFormat.XLSX,
    ".xlsm": LibraryFormat.XLSX,
    ".sdf": LibraryFormat.SDF,
    ".sd": LibraryFormat.SDF,
    ".mol": LibraryFormat.SDF,
    ".parquet": LibraryFormat.PARQUET,
    ".pq": LibraryFormat.PARQUET,
}

# A cascade authored in the browser normally carries no library path: the file
# to screen is chosen on the command line.  Validating such a cascade still has
# to name *something* the reader would accept, so these stand-ins are used and
# reported as placeholders rather than silently presented as a real input.
_FORMAT_PLACEHOLDERS: dict[LibraryFormat, str] = {
    LibraryFormat.AUTO: "molecules.csv",
    LibraryFormat.DELIMITED: "molecules.csv",
    LibraryFormat.XLSX: "molecules.xlsx",
    LibraryFormat.SDF: "molecules.sdf",
    LibraryFormat.PARQUET: "molecules.parquet",
    LibraryFormat.MOL2_DIRECTORY: "molecules",
}

_TAB_SUFFIXES = frozenset({".tsv", ".tab"})
_WHITESPACE_SUFFIXES = frozenset({".smi", ".smiles"})

# Only these readers accept a column/row layout description.  Sending
# ``smiles_column`` to the SDF or Parquet reader would be silently meaningless,
# so the mapping below is explicit rather than a blanket ``update``.
_COLUMN_AWARE = frozenset({DELIMITED_SOURCE, XLSX_SOURCE})


@dataclass(frozen=True, slots=True)
class ResolvedLibrary:
    """The exact reader and settings a cascade run will use."""

    path: str
    format: LibraryFormat
    plugin: str
    settings: dict[str, Any]
    is_placeholder: bool = False

    @property
    def is_directory_source(self) -> bool:
        return self.plugin == MOL2_DIRECTORY_SOURCE


def detect_format(path: str | Path) -> LibraryFormat:
    """Infer a library format from a path without reading the file.

    Directories are treated as MOL2 collections because that is the only
    directory-shaped source MolCascade ships.  A file is classified by suffix;
    an unknown suffix is an explicit error rather than a guess, because reading
    an SDF as CSV would produce plausible-looking garbage.
    """

    candidate = Path(path)
    if candidate.is_dir():
        return LibraryFormat.MOL2_DIRECTORY
    suffix = candidate.suffix.casefold()
    if suffix in {".gz", ".bz2", ".xz", ".zst"}:
        raise ConfigError(
            f"compressed library {candidate.name!r} must be decompressed before screening",
            code="LIBRARY_COMPRESSED",
            context={"path": str(candidate)},
        )
    library_format = _SUFFIX_FORMATS.get(suffix)
    if library_format is None:
        supported = ", ".join(sorted(_SUFFIX_FORMATS))
        raise ConfigError(
            f"cannot infer a reader for {candidate.name!r}; supported suffixes are {supported}",
            code="LIBRARY_FORMAT_UNKNOWN",
            context={"path": str(candidate), "suffix": suffix},
        )
    return library_format


def _default_delimiter(path: str) -> str:
    suffix = Path(path).suffix.casefold()
    if suffix in _TAB_SUFFIXES:
        return "\t"
    if suffix in _WHITESPACE_SUFFIXES:
        return " "
    return ","


def reader_settings(library: LibraryConfig, plugin: str) -> dict[str, Any]:
    """The layout options in ``library`` that ``plugin`` understands.

    ``LibraryConfig`` describes a file in the user's vocabulary -- "the id
    column" -- and each reader names the same thing differently: the delimited
    and XLSX sources call it ``candidate_id_column``, the SDF source calls it
    ``candidate_id_property`` because an SD record has no columns at all.  This
    is the one place that translation happens, so the command line and a stored
    configuration cannot end up disagreeing about what ``--id-column`` means.

    Options the selected reader has no field for are dropped rather than
    rejected: ``--sheet`` is meaningless for a CSV, and refusing the whole run
    over an inapplicable flag would be worse than ignoring it.
    """

    settings: dict[str, Any] = {}
    if plugin in _COLUMN_AWARE:
        if library.smiles_column is not None:
            settings["smiles_column"] = library.smiles_column
        if library.id_column is not None:
            settings["candidate_id_column"] = library.id_column
        if library.has_header is not None:
            settings["has_header"] = library.has_header
        if library.skip_rows is not None:
            settings["skip_rows"] = library.skip_rows
    if plugin == XLSX_SOURCE and library.sheet_name is not None:
        settings["sheet_name"] = library.sheet_name
    elif plugin == SDF_SOURCE and library.id_column is not None:
        settings["candidate_id_property"] = library.id_column
    return settings


def resolve_library(
    library: LibraryConfig,
    *,
    override_path: str | Path | None = None,
    override_format: LibraryFormat | None = None,
    base_settings: Mapping[str, Any] | None = None,
    base_plugin: str | None = None,
    allow_placeholder: bool = False,
) -> ResolvedLibrary:
    """Choose the ingest plugin and merge the user's reader options.

    ``base_settings`` are the settings already stored on the cascade's ingest
    step; library options are layered on top so that an explicitly configured
    value is never silently discarded.  They are kept only when ``base_plugin``
    confirms they were written for the reader now being selected -- a delimited
    reader's ``delimiter`` is not a field an SDF reader will accept, and
    carrying it across would fail validation instead of switching format.
    """

    path_value = str(override_path) if override_path is not None else library.path
    placeholder = False
    if not path_value:
        if not allow_placeholder:
            raise ConfigError(
                "no molecule library was provided; set library.path or pass --library",
                code="LIBRARY_MISSING",
                context={},
            )
        selected = override_format or library.format
        path_value = _FORMAT_PLACEHOLDERS[selected]
        placeholder = True
    if any(character in path_value for character in "\r\n\x00"):
        raise ConfigError(
            "library path must not contain control characters",
            code="LIBRARY_PATH_INVALID",
            context={},
        )

    if override_format is not None and override_format is not LibraryFormat.AUTO:
        library_format = override_format
    elif library.format is LibraryFormat.AUTO or override_path is not None:
        # An explicit --library points at a different file, so re-infer instead
        # of reusing a format that described the previous one.
        library_format = detect_format(path_value)
    else:
        library_format = library.format

    plugin = _FORMAT_PLUGINS[library_format]
    reusable = base_settings if base_plugin is None or base_plugin == plugin else None
    settings: dict[str, Any] = dict(reusable or {})
    settings["path"] = path_value
    settings["schema_version"] = 1

    if plugin == PARQUET_SOURCE:
        settings.pop("max_record_bytes", None)
        settings.pop("source_uri", None)
        settings.pop("generator_id", None)
        settings.pop("batch_id", None)
    if plugin == DELIMITED_SOURCE:
        settings["delimiter"] = library.delimiter or _default_delimiter(path_value)
    settings.update(reader_settings(library, plugin))
    settings["batch_size"] = library.batch_size

    return ResolvedLibrary(
        path=path_value,
        format=library_format,
        plugin=plugin,
        settings=settings,
        is_placeholder=placeholder,
    )


__all__ = [
    "DELIMITED_SOURCE",
    "MOL2_DIRECTORY_SOURCE",
    "PARQUET_SOURCE",
    "PLUGIN_FORMATS",
    "SDF_SOURCE",
    "XLSX_SOURCE",
    "ResolvedLibrary",
    "detect_format",
    "reader_settings",
    "resolve_library",
]
