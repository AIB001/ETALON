"""The Lilly Medchem Rules: 275 published queries, scored rather than matched.

Three alert adapters already ship beside this one, and between them they apply
2,458 substructure rules from 23 published collections -- PAINS, BMS, Glaxo,
Dundee, Inpharmatica, MLSMR, SureChEMBL and the rest.  None of them contains a
single Lilly rule, and more importantly none of them can express what this rule
set does.  Every one of those 2,458 is a boolean: a molecule matches or it does
not, and the adapter turns that into IGNORE, WARN or REJECT.

Bruns and Watson's system is graded.  A motif that is undesirable but not fatal
earns *demerits* -- a nitro group 60, a hexyl chain 50, a heptyl chain 100 -- and
demerits from different motifs are summed.  A molecule is rejected once the
total passes the cutoff, so the rejected set includes compounds with no single
disqualifying group and an accumulation of blemishes, which no boolean catalogue
can find.  Separately, a smaller set of queries rejects outright, and those
rules are themselves assigned demerits equal to the cutoff so that the score
stays usable as a number rather than becoming a sentinel.

That graded total is evidence, not a verdict, so it is published as
``derived_metric/v1`` and a threshold gate decides what to do with it -- the same
division this project makes everywhere else.  The outright rejections are a
verdict and are published as ``decision/v1``.  Both come out of one pass, because
the four programs below form one pipeline and running it twice to split the
outputs would double the cost for nothing.

Three things about the implementation are worth stating, because each was found
by measurement rather than by reading.

**The rules and the engine come from different places.**  The conda package
``lilly-medchem-rules`` installs four executables and no rules at all; the 275
queries ship inside ``medchem`` as data files (``reject1``, ``reject2``,
``demerits``, plus the charge-assigner directory).  So this adapter digests the
query files into its own policy identity and locates the executables separately.
A project that swaps in its own query files gets a different ``method_id``, which
is the point: a different rule revision is a different measurement.

**The input must be Kekule.**  LillyMol and RDKit disagree about aromaticity in
complex fused systems, and the upstream documentation says so outright.  Fed
RDKit's aromatic SMILES, ``mc_first_pass`` silently failed to interpret 89 of
2,000 molecules from a generated library -- written to a log, absent from every
output file.  Fed Kekule SMILES, 6 of the same 2,000 failed.  Those six keep a
row with a null value and ``BACKEND_FAILED``, because a molecule the engine
could not read is not a molecule that passed.

**``medchem``'s own wrapper cannot be used for this.**  ``LillyDemeritsFilters``
ends with ``results["mol"] = mols``, which assumes the pipeline returns one row
per input; on the same 2,000 molecules it returns 1,911 and raises a pandas
length error.  The four programs are therefore driven here, with every molecule
carrying a shard-local token so that what the pipeline drops is accounted for
rather than inferred from a row count.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, DERIVED_METRIC_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import (
    PendingOutput,
    StageContext,
    StageRequest,
    StageResponse,
)
from molcascade.plugins.builtin.structural_alerts import CatalogAction
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_METRIC_PATH = Path("datasets/metrics/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")

#: The executables the pipeline needs, in the order it runs them.  ``mc_summarise``
#: also ships in the conda package and is a reporting tool; it is not used here.
_BINARIES = ("mc_first_pass", "tsubstructure", "iwdemerit")

#: Query files, in the order the pipeline consumes them.  Two outright-rejection
#: sets then the demerit set -- 89, 105 and 80 queries in ``medchem`` 2.0.5,
#: which is the 275 of the paper.
_QUERY_FILES = ("reject1", "reject2", "demerits")

#: ``medchem`` packages the rules as data, so this is where they are looked for
#: unless the operator names a directory of their own.
_QUERY_PACKAGE = "medchem.data.queries"
_CHARGE_ASSIGNER_PACKAGE = "medchem.data.charge_assigner"

#: The metric this stage publishes.  ``COUNT`` because a demerit total is a
#: tally of penalty points and not a physical quantity; ``LOWER_BETTER`` because
#: the cutoff is an upper bound.
_METRIC_ID = "lilly_demerit_total"
_METRIC_UNITS = "COUNT"
_METRIC_DIRECTION = "LOWER_BETTER"

#: ``(N matches to 'name')`` is how every stage names what fired.  The demerit
#: stage prefixes the demerit value into the name -- ``'D60 nitro'`` -- so the
#: rule name and its cost arrive in one capture.
_REASON_RE = re.compile(r"\((\d+)\s+matches\s+to\s+'([^']*)'\)")
#: ``<smiles> <token> : D(160) (1 matches to ...)`` on the demerit stage, and
#: ``<smiles> <token>`` with no colon at all when nothing fired.
_TOTAL_RE = re.compile(r"D\((\d+)\)")

#: Stage names used in reason codes, indexed the way the output files are.
_STAGE_NAMES = ("first_pass", "reject1", "reject2", "demerits")


class LillyMedchemConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=4_096, ge=1, le=250_000)
    decision_buffer_size: int = Field(default=50_000, ge=1, le=1_000_000)

    #: Absolute path to the directory holding the four executables.  Named
    #: rather than resolved on PATH for the same reason every out-of-process
    #: engine in this project is named: a run has to be able to say which binary
    #: produced its numbers, and ``shutil.which`` answers that differently
    #: depending on who started the process.  ``None`` falls back to PATH and
    #: records what it found.
    binary_dir: str | None = None

    #: Absolute path to a directory holding ``reject1``, ``reject2`` and
    #: ``demerits``.  ``None`` uses the copies ``medchem`` ships.
    queries_dir: str | None = None

    #: ``regular`` is the published default: 7 to 40 heavy atoms, demerits above
    #: 25, rejection at 100.  ``relaxed`` is the authors' own wider setting, 7 to
    #: 50 with rejection at 160.  ``rejections_only`` applies the two rejection
    #: query sets and no demerits at all.
    mode: str = "regular"

    #: Override the mode's atom-count bounds.  ``mc_first_pass`` rejects below
    #: the lower bound and above the hard bound; ``iwdemerit`` demerits
    #: progressively between the soft and hard bounds.
    lower_atom_count: int | None = Field(default=None, ge=1, le=1_000)
    soft_upper_atom_count: int | None = Field(default=None, ge=1, le=1_000)
    hard_upper_atom_count: int | None = Field(default=None, ge=1, le=1_000)

    #: Demerit total at which a molecule is rejected. ``None`` leaves
    #: ``iwdemerit`` on its own default, which is the paper's 100.
    demerit_cutoff: int | None = Field(default=None, ge=1, le=10_000)

    #: Rule names to drop from the demerit set, matched case-insensitively as
    #: regular expressions. The upstream driver calls this ``-odm``.
    omit_demerits: tuple[str, ...] = ()

    #: What an outright rejection does -- the atom-count bounds and the two
    #: rejection query sets.
    rejection_action: CatalogAction = CatalogAction.REJECT
    #: What a demerit total over the cutoff does.
    demerit_action: CatalogAction = CatalogAction.REJECT
    #: What a molecule LillyMol could not interpret does.  ``WARN`` by default:
    #: the molecule is kept and the failure is recorded, because an engine that
    #: cannot read a structure has said nothing about it.
    unreadable_action: CatalogAction = CatalogAction.WARN

    @field_validator("omit_demerits", mode="before")
    @classmethod
    def _accept_list_or_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value) if isinstance(value, list) else value

    @field_validator("rejection_action", "demerit_action", "unreadable_action", mode="before")
    @classmethod
    def _parse_action(cls, value: Any) -> Any:
        return CatalogAction(value) if isinstance(value, str) else value

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in ("regular", "relaxed", "rejections_only"):
            raise ValueError("mode must be regular, relaxed or rejections_only")
        return value

    @field_validator("binary_dir", "queries_dir")
    @classmethod
    def _absolute(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            raise ValueError("must be an absolute path")
        return value

    @model_validator(mode="after")
    def _bounds_ordered(self) -> LillyMedchemConfig:
        lower, soft, hard = self.bounds()
        if lower >= hard:
            raise ValueError("lower_atom_count must be below hard_upper_atom_count")
        if soft > hard:
            raise ValueError("soft_upper_atom_count must not exceed hard_upper_atom_count")
        return self

    def bounds(self) -> tuple[int, int, int]:
        """``(lower, soft upper, hard upper)`` heavy-atom counts for this mode."""

        defaults = (7, 26, 50) if self.mode == "relaxed" else (7, 25, 40)
        lower = self.lower_atom_count or defaults[0]
        hard = self.hard_upper_atom_count or defaults[2]
        if self.mode == "rejections_only":
            # The upstream driver collapses the soft bound onto the hard one so
            # that no atom-count demerits accrue at all.
            return (lower, hard - 1, hard)
        soft = self.soft_upper_atom_count or defaults[1]
        return (lower, soft, hard)

    def cutoff(self) -> int | None:
        if self.demerit_cutoff is not None:
            return self.demerit_cutoff
        return 160 if self.mode == "relaxed" else None

    def actions(self) -> dict[str, CatalogAction]:
        return {
            "rejection": self.rejection_action,
            "demerit": self.demerit_action,
            "unreadable": self.unreadable_action,
        }


def _validated_config(request: StageRequest) -> LillyMedchemConfig:
    try:
        return LillyMedchemConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid Lilly Medchem Rules configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _resolve_binaries(config: LillyMedchemConfig) -> dict[str, str]:
    """Absolute paths to the three executables, or an explanation."""

    resolved: dict[str, str] = {}
    for name in _BINARIES:
        if config.binary_dir is not None:
            candidate = Path(config.binary_dir) / name
            found = str(candidate) if candidate.is_file() else None
        else:
            found = shutil.which(name)
        if found is None:
            raise PluginError(
                f"the Lilly Medchem Rules executable {name} is not available",
                code="LILLY_BACKEND_UNAVAILABLE",
                hint=(
                    "Install the engine with 'conda install -c conda-forge "
                    "lilly-medchem-rules', then either put its bin directory on "
                    "PATH or set 'binary_dir' to it. The rules themselves come "
                    "from medchem and are a separate install."
                ),
                context={"binary": name, "binary_dir": config.binary_dir},
            )
        resolved[name] = found
    return resolved


def _resolve_queries(config: LillyMedchemConfig) -> tuple[dict[str, Path], Path]:
    """The three query files and the charge-assigner directory."""

    if config.queries_dir is not None:
        root = Path(config.queries_dir)
        paths = {name: root / name for name in _QUERY_FILES}
        charge_assigner = root / "charge_assigner"
    else:
        try:
            paths = {
                name: Path(str(resource_files(_QUERY_PACKAGE).joinpath(name)))
                for name in _QUERY_FILES
            }
            charge_assigner = Path(
                str(resource_files(_CHARGE_ASSIGNER_PACKAGE).joinpath("queries"))
            )
        except (ModuleNotFoundError, FileNotFoundError) as error:
            raise PluginError(
                "the Lilly Medchem Rules query files are not installed",
                code="LILLY_QUERIES_UNAVAILABLE",
                hint=(
                    "The 275 queries ship as data inside medchem; install it with "
                    "'pip install medchem', or set 'queries_dir' to a directory "
                    "holding reject1, reject2 and demerits."
                ),
            ) from error
    for name, path in paths.items():
        if not path.is_file():
            raise PluginError(
                f"the Lilly Medchem Rules query file {name} is missing",
                code="LILLY_QUERIES_UNAVAILABLE",
                context={"path": str(path)},
            )
    return paths, charge_assigner


def _queries_digest(paths: dict[str, Path], charge_assigner: Path) -> str:
    """One digest over the rule set, so a changed revision is a changed method.

    The charge-assigner directory is a tree of small query files that affect
    which demerits fire, so its listing is folded in by name and digest rather
    than ignored.
    """

    parts: dict[str, str] = {}
    for name in _QUERY_FILES:
        parts[name] = hashlib.sha256(paths[name].read_bytes()).hexdigest()
    if charge_assigner.is_dir():
        for child in sorted(charge_assigner.rglob("*")):
            if child.is_file():
                key = f"charge_assigner/{child.relative_to(charge_assigner).as_posix()}"
                parts[key] = hashlib.sha256(child.read_bytes()).hexdigest()
    return canonical_sha256({"queries": parts})


def _method_id(config: LillyMedchemConfig, queries_digest: str) -> str:
    lower, soft, hard = config.bounds()
    return "lilly-medchem:sha256:" + canonical_sha256(
        {
            "queries_sha256": queries_digest,
            "mode": config.mode,
            "bounds": [lower, soft, hard],
            "demerit_cutoff": config.cutoff(),
            "omit_demerits": sorted(config.omit_demerits),
            "semantics": "graded-demerit-triage-not-experimental-proof",
        }
    )


def _kekule(smiles: str) -> str | None:
    """Kekule form, which is what LillyMol's parser agrees with RDKit about.

    Returns ``None`` when RDKit itself cannot read the structure, which for a
    registered parent should not happen and is reported rather than skipped.
    """

    from rdkit import Chem, rdBase

    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            return None
        try:
            Chem.Kekulize(molecule, clearAromaticFlags=True)
        except Exception:
            return None
        return Chem.MolToSmiles(molecule, kekuleSmiles=True)


def _run(command: list[str]) -> None:
    """Run one stage, tolerating output that is not valid UTF-8.

    ``mc_first_pass`` writes bytes to stderr that no codec claims, so the stream
    is decoded with replacement rather than read as text.
    """

    completed = subprocess.run(command, capture_output=True)
    if completed.returncode != 0:
        raise PluginError(
            "a Lilly Medchem Rules stage failed",
            code="LILLY_STAGE_FAILED",
            context={
                "program": Path(command[0]).name,
                "returncode": completed.returncode,
                "stderr_tail": completed.stderr.decode("utf-8", "replace")[-2_000:],
            },
        )


def _parse_rejects(path: Path) -> dict[str, list[tuple[int, str]]]:
    """``token`` to the ``(count, rule name)`` pairs that rejected it."""

    found: dict[str, list[tuple[int, str]]] = {}
    if not path.is_file():
        return found
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 2:
                continue
            token = fields[1]
            reasons = [
                (int(count), name) for count, name in _REASON_RE.findall(line)
            ]
            found[token] = reasons
    return found


def _parse_demerits(path: Path) -> dict[str, tuple[int, list[tuple[int, str]]]]:
    """``token`` to ``(demerit total, reasons)`` for one demerit-stage file."""

    found: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    if not path.is_file():
        return found
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            head, _, tail = line.partition(" : ")
            fields = head.split()
            if len(fields) < 2:
                continue
            token = fields[1]
            match = _TOTAL_RE.search(tail)
            total = int(match.group(1)) if match else 0
            reasons = [(int(count), name) for count, name in _REASON_RE.findall(tail)]
            found[token] = (total, reasons)
    return found


def _reason_text(reasons: list[tuple[int, str]]) -> str:
    return ", ".join(
        name if count == 1 else f"{name} x{count}" for count, name in reasons
    )


def _reason_code(stage: str, reasons: list[tuple[int, str]]) -> str:
    """Stable code naming the pipeline stage and the rules that fired.

    The rule names are hashed rather than spelled out: the demerit set alone can
    fire in combination, and a code built by concatenation has no bound.
    """

    if not reasons:
        return f"LILLY_{stage.upper()}"
    digest = canonical_sha256({"rules": sorted(name for _, name in reasons)})[:12]
    return f"LILLY_{stage.upper()}_{digest}"


def _detail(stage: str, reasons: list[tuple[int, str]], total: int | None) -> str:
    payload: dict[str, Any] = {
        "stage": stage,
        "rules": [{"name": name, "matches": count} for count, name in reasons],
    }
    if total is not None:
        payload["demerit_total"] = total
    payload["message"] = (
        f"Lilly Medchem Rules {stage}: {_reason_text(reasons) or 'no named rule'}"
    )
    return json.dumps(payload, sort_keys=True)


def _pipeline(
    *,
    binaries: dict[str, str],
    queries: dict[str, Path],
    charge_assigner: Path,
    config: LillyMedchemConfig,
    tokens: dict[str, str],
    workspace: Path,
) -> tuple[
    dict[str, tuple[str, list[tuple[int, str]]]],
    dict[str, tuple[int, list[tuple[int, str]]]],
    set[str],
]:
    """Run all four stages over one batch.

    Returns the rejections keyed by token, the demerit readings keyed by token,
    and the tokens that reached the end without a verdict -- which is how a
    molecule the engine could not interpret becomes visible.
    """

    lower, soft, hard = config.bounds()
    path = lambda name: str(workspace / name)  # noqa: E731 - local shorthand
    source = workspace / "input.smi"
    source.write_text(
        "".join(f"{smiles}\t{token}\n" for token, smiles in tokens.items()),
        encoding="utf-8",
    )

    demerit_queries = queries["demerits"]
    if config.omit_demerits:
        patterns = [re.compile(entry, re.IGNORECASE) for entry in config.omit_demerits]
        kept = [
            line
            for line in demerit_queries.read_text(encoding="utf-8").splitlines()
            if not any(pattern.search(line) for pattern in patterns)
        ]
        demerit_queries = workspace / "demerits.filtered"
        demerit_queries.write_text("\n".join(kept) + "\n", encoding="utf-8")

    _run(
        [
            binaries["mc_first_pass"],
            "-I", "0", "-A", "I", "-A", "ipp",
            "-c", str(lower), "-C", str(hard),
            "-E", "autocreate", "-o", "smi",
            "-V", "-g", "all", "-g", "ltltr", "-i", "ICTE",
            "-L", path("bad0"), "-K", "TP1",
            "-a", "-u", "-S", path("first_pass.smi"),
            str(source),
        ]
    )
    _run(
        [
            binaries["tsubstructure"],
            "-E", "autocreate", "-b", "-u", "-i", "smi", "-o", "smi", "-A", "D",
            "-m", path("bad1"), "-m", "QDT",
            "-n", path("reject1.smi"),
            "-q", "F:" + str(queries["reject1"]),
            path("first_pass.smi"),
        ]
    )
    _run(
        [
            binaries["tsubstructure"],
            "-A", "D", "-E", "autocreate", "-b", "-u", "-i", "smi", "-o", "smi",
            "-m", path("bad2"), "-m", "QDT",
            "-n", path("reject2.smi"),
            "-q", "F:" + str(queries["reject2"]),
            path("reject1.smi"),
        ]
    )

    rejections: dict[str, tuple[str, list[tuple[int, str]]]] = {}
    for index, name in enumerate(("bad0", "bad1", "bad2")):
        for token, reasons in _parse_rejects(workspace / f"{name}.smi").items():
            rejections[token] = (_STAGE_NAMES[index], reasons)

    readings: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    if config.mode == "rejections_only":
        survivors = _parse_demerits(workspace / "reject2.smi")
        readings = {token: (0, []) for token in survivors}
    else:
        command = [
            binaries["iwdemerit"],
            "-u", "-k", "-x", "-t",
            "-E", "autocreate", "-A", "D", "-i", "smi", "-o", "smi",
            "-q", "F:" + str(demerit_queries),
            "-R", path("bad3"),
            "-G", path("passed.smi"),
            "-c", f"smax={soft}", "-c", f"hmax={hard}",
        ]
        if charge_assigner.exists():
            command[6:6] = ["-N", "F:" + str(charge_assigner)]
        cutoff = config.cutoff()
        if cutoff is not None:
            command[6:6] = ["-f", str(cutoff)]
        command.append(path("reject2.smi"))
        _run(command)
        for token, reading in _parse_demerits(workspace / "bad3.smi").items():
            rejections[token] = (_STAGE_NAMES[3], reading[1])
            readings[token] = reading
        readings.update(_parse_demerits(workspace / "passed.smi"))

    accounted = set(rejections) | set(readings)
    return rejections, readings, set(tokens) - accounted


_RUNTIME_KEY = "molcascade.runtime"


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Screen one contiguous range of parents through the four programs."""

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = LillyMedchemConfig.model_validate(settings)
    method_id = str(runtime["method_id"])
    binaries = {str(k): str(v) for k, v in dict(runtime["binaries"]).items()}
    queries = {str(k): Path(str(v)) for k, v in dict(runtime["queries"]).items()}
    charge_assigner = Path(str(runtime["charge_assigner"]))
    actions = config.actions()

    counters = {
        "input_count": 0,
        "retained_count": 0,
        "reject_count": 0,
        "decision_count": 0,
        "reject_decision_count": 0,
        "warning_decision_count": 0,
        "metric_count": 0,
        "unreadable_count": 0,
        "demerited_count": 0,
    }
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["metrics"], DERIVED_METRIC_V1.schema, compression="zstd"
        ) as metric_writer,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decision_writer,
    ):
        decision_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []

        def flush() -> None:
            if decision_rows:
                decision_writer.write_table(
                    pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
                )
                decision_rows.clear()
            if metric_rows:
                metric_writer.write_table(
                    pa.Table.from_pylist(metric_rows, schema=DERIVED_METRIC_V1.schema)
                )
                metric_rows.clear()

        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            rows = batch.to_pylist()
            counters["input_count"] += len(rows)
            tokens: dict[str, str] = {}
            by_token: dict[str, dict[str, Any]] = {}
            unreadable_by_rdkit: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                kekule = _kekule(str(row.get("parent_smiles")))
                if kekule is None:
                    unreadable_by_rdkit.append(row)
                    continue
                token = f"L{index}"
                tokens[token] = kekule
                by_token[token] = row

            rejections: dict[str, tuple[str, list[tuple[int, str]]]] = {}
            readings: dict[str, tuple[int, list[tuple[int, str]]]] = {}
            unaccounted: set[str] = set()
            if tokens:
                with tempfile.TemporaryDirectory(prefix="lilly-") as workspace:
                    rejections, readings, unaccounted = _pipeline(
                        binaries=binaries,
                        queries=queries,
                        charge_assigner=charge_assigner,
                        config=config,
                        tokens=tokens,
                        workspace=Path(workspace),
                    )

            retained_rows: list[dict[str, Any]] = []

            def record(
                row: dict[str, Any],
                *,
                value: float | None,
                status: str,
                status_detail: str | None,
                outcome: str,
                reason_code: str,
                detail: str,
                source: dict[str, Any] | None = None,
            ) -> None:
                parent_id = row.get("parent_id")
                metric_rows.append(
                    {
                        "parent_id": parent_id,
                        "metric_id": _METRIC_ID,
                        "method_id": method_id,
                        "value": value,
                        "units": _METRIC_UNITS,
                        "direction": _METRIC_DIRECTION,
                        "status": status,
                        "status_detail": status_detail,
                        "source_json": None if source is None else json.dumps(
                            source, sort_keys=True
                        ),
                    }
                )
                counters["metric_count"] += 1
                decision_rows.append(
                    {
                        "entity_id": parent_id,
                        "entity_kind": "PARENT",
                        "stage_id": task.stage_id,
                        "outcome": outcome,
                        "reason_code": reason_code,
                        "rule_id": method_id,
                        "detail": detail,
                    }
                )
                counters["decision_count"] += 1
                if outcome == "REJECT":
                    counters["reject_decision_count"] += 1
                elif outcome == "WARN":
                    counters["warning_decision_count"] += 1

            for row in unreadable_by_rdkit:
                action = actions["unreadable"]
                record(
                    row,
                    value=None,
                    status="BACKEND_FAILED",
                    status_detail="RDKit could not read the registered SMILES",
                    outcome="PASS" if action is CatalogAction.IGNORE else action.value.upper(),
                    reason_code="LILLY_UNREADABLE_RDKIT",
                    detail=_detail("input", [], None),
                )
                counters["unreadable_count"] += 1
                if action is CatalogAction.REJECT:
                    counters["reject_count"] += 1
                else:
                    counters["retained_count"] += 1
                    retained_rows.append(row)

            for token in sorted(unaccounted):
                row = by_token[token]
                action = actions["unreadable"]
                record(
                    row,
                    value=None,
                    status="BACKEND_FAILED",
                    status_detail="LillyMol could not interpret the structure",
                    outcome="PASS" if action is CatalogAction.IGNORE else action.value.upper(),
                    reason_code="LILLY_UNREADABLE_ENGINE",
                    detail=_detail("first_pass", [], None),
                )
                counters["unreadable_count"] += 1
                if action is CatalogAction.REJECT:
                    counters["reject_count"] += 1
                else:
                    counters["retained_count"] += 1
                    retained_rows.append(row)

            for token, (stage, reasons) in sorted(rejections.items()):
                row = by_token[token]
                reading = readings.get(token)
                total = reading[0] if reading is not None else None
                is_demerit = stage == _STAGE_NAMES[3]
                action = actions["demerit" if is_demerit else "rejection"]
                record(
                    row,
                    value=None if total is None else float(total),
                    status="OK" if total is not None else "NOT_APPLICABLE",
                    status_detail=(
                        None
                        if total is not None
                        else f"rejected at the {stage} stage, before any demerit was scored"
                    ),
                    outcome="PASS" if action is CatalogAction.IGNORE else action.value.upper(),
                    reason_code=_reason_code(stage, reasons),
                    detail=_detail(stage, reasons, total),
                    source={"stage": stage, "rules": [name for _, name in reasons]},
                )
                if action is CatalogAction.REJECT:
                    counters["reject_count"] += 1
                else:
                    counters["retained_count"] += 1
                    retained_rows.append(row)

            for token, (total, reasons) in sorted(readings.items()):
                if token in rejections:
                    continue
                row = by_token[token]
                if total:
                    counters["demerited_count"] += 1
                record(
                    row,
                    value=float(total),
                    status="OK",
                    status_detail=None,
                    outcome="PASS",
                    reason_code=(
                        "LILLY_CLEAR" if not reasons else _reason_code("demerited", reasons)
                    ),
                    detail=_detail("demerits", reasons, total),
                    source={"stage": "demerits", "rules": [name for _, name in reasons]},
                )
                counters["retained_count"] += 1
                retained_rows.append(row)

            if retained_rows:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained_rows, schema=PARENT_V1.schema)
                )
            if len(decision_rows) >= config.decision_buffer_size:
                flush()
        flush()
    return ShardOutcome(
        rows_in=counters["input_count"],
        rows_out={
            "primary": counters["retained_count"],
            "metrics": counters["metric_count"],
            "decisions": counters["decision_count"],
        },
        metadata=dict(counters),
    )


class LillyMedchemPlugin:
    """Apply the Lilly Medchem Rules and publish the demerit total as evidence."""

    descriptor = PluginDescriptor(
        id="chemistry.lilly_medchem",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DERIVED_METRIC_V1.id, DECISION_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "metrics": DERIVED_METRIC_V1.id,
            "decisions": DECISION_V1.id,
        },
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="Lilly Medchem Rules",
        description=(
            "The 275 published Lilly queries, applied as outright rejections plus "
            "a graded demerit total that accumulates across motifs. The total is "
            "published as evidence so a threshold gate decides what it is worth."
        ),
    )
    config_model = LillyMedchemConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        # Resolved in the parent so a missing engine or rule file stops the stage
        # before shards are scheduled rather than in every worker at once.
        binaries = _resolve_binaries(config)
        queries, charge_assigner = _resolve_queries(config)
        queries_digest = _queries_digest(queries, charge_assigner)
        method_id = _method_id(config, queries_digest)
        lower, soft, hard = config.bounds()

        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "metrics": _METRIC_PATH.as_posix(),
                "decisions": _DECISION_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config={
                **config.model_dump(mode="json"),
                _RUNTIME_KEY: {
                    "method_id": method_id,
                    "binaries": binaries,
                    "queries": {name: str(path) for name, path in queries.items()},
                    "charge_assigner": str(charge_assigner),
                },
            },
        )
        if result.rows_in == 0:
            raise PluginError(
                "the Lilly Medchem Rules stage input contains no parents",
                code="LILLY_EMPTY_INPUT",
            )
        counters = {
            name: result.total(name)
            for name in (
                "input_count",
                "retained_count",
                "reject_count",
                "decision_count",
                "reject_decision_count",
                "warning_decision_count",
                "metric_count",
                "unreadable_count",
                "demerited_count",
            )
        }
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": counters["retained_count"]},
                ),
                "metrics": PendingOutput(
                    DERIVED_METRIC_V1.id,
                    result.file_paths["metrics"],
                    {"row_count": counters["metric_count"], "method_id": method_id},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": counters["decision_count"], "policy_id": method_id},
                ),
            },
            metadata={
                **counters,
                "output_count": counters["retained_count"],
                "method_id": method_id,
                "metric_id": _METRIC_ID,
                "mode": config.mode,
                "atom_count_bounds": [lower, soft, hard],
                "demerit_cutoff": config.cutoff(),
                "queries_sha256": queries_digest,
                "binaries": binaries,
                "backend": "lilly-medchem-rules",
                "network_or_download_invoked_by_adapter": False,
                **result.response_metadata(),
            },
        )


__all__ = ["LillyMedchemConfig", "LillyMedchemPlugin"]
