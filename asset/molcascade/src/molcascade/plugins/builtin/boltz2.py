"""Affinity from a model that re-folds the complex and never looks at our pose.

Everything else downstream of docking in this project re-reads the pose an
engine produced: strain asks what the conformer costs, the normalized scores
re-express the number, the specificity panel re-docks it elsewhere.  Boltz-2
does none of that.  It is handed a sequence and a SMILES and it folds the
complex itself, so its opinion is about the molecule and the target rather than
about the geometry Uni-Dock stored.  That is why it consumes only
``parent/v1``: there is no evidence contract for it to bind to, and binding one
would imply a relationship that does not exist.

Which means nothing in the compiler forces this tier to be last.  It reads
parents, so it would lower perfectly well as tier one -- and that is exactly the
reason ``max_molecules`` is a field with a small default rather than an optional
guard.  On one 4090 a complex takes tens of seconds, so a thousand molecules is
most of a day and a hundred thousand is a fortnight.  The cost is the only
argument for the position, and a cost argument that lives in a comment is one
the run does not enforce; this one refuses the input instead.

It cannot share this interpreter.  ``boltz`` 2.2.1 accepts the Python that
MolCascade runs on, so the conflict is not the interpreter -- it is Torch.
Resolving boltz into this environment re-pins the Torch that ADMET-AI predicts
with in-process and that KarmaDock's own environment was built against, which
would silently re-score every tier above this one so that the last tier could
run.  So Boltz-2 lives behind its own ``boltz`` executable in its own
environment, and this adapter is the process boundary.

Four decisions here are worth stating rather than leaving to be discovered.

*The target is a sequence, and it is given as one.*  This plugin deliberately
does not declare a ``receptor_path`` field, which is what would make the
cascade inject the campaign's receptor PDB into it (see
``cascade/lower.py``'s target marker).  A PDB is not a sequence: reading one
off ATOM records closes every unresolved gap without saying so, and a co-folded
prediction against a protein whose missing loops were quietly deleted is wrong
in a way no error message would ever mention.  So the sequence arrives as a
FASTA the operator points at, and its bytes are hashed into ``model_id``.

*The MSA is local, or the run stops.*  Boltz-2's accuracy depends on the
alignment, and there are exactly three honest ways to supply one: a file you
produced, an explicit acknowledgement that you are running without one, or
Boltz's own request to a public server.  The third publishes your target
sequence to a third party, so it is available and never a default.  The first
is the default and a missing file is an error before the first molecule is
written, not a silent fall back to the second.

*A molecule Boltz-2 was not asked about gets no row.*  ``prediction/v1`` makes
``prediction_mean`` non-nullable, by design -- the contract has no vocabulary
for "unknown", so there is nowhere to record a failure without inventing a
number.  A ligand above the atom limit, or one RDKit cannot parse, is therefore
counted in the stage's metadata and left out of the evidence, which a
fail-closed numeric gate turns into a rejection.  The alternative -- a
sentinel affinity -- would rank rather than reject.

*The affinity is a log10 IC50 in micromolar, and lower is stronger.*  It is
also not calibrated: Boltz-2's own paper presents it for ranking.  Both facts
are in the endpoint names and in ``calibration_id``, which stays null because
no calibration was applied.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Literal

import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import iter_contract_batches, require_single_input
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, PREDICTION_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin._machine_paths import (
    MachinePath,
    absolute_path,
    conda_environment,
    engine_path,
    fill_machine_paths,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_PREDICTION_PATH = Path("datasets/predictions/part-00000.parquet")

_IMPLEMENTATION_VERSION = 1

#: The affinity head's two outputs, named so that the units and the direction
#: travel with the number.  ``prediction/v1`` has no direction column, so a
#: reader who sees only the endpoint id still has to be able to tell which way
#: is better -- and "affinity" alone does not say.
_AFFINITY_ENDPOINT = "boltz2_affinity_log10_ic50_um"
_BINDER_ENDPOINT = "boltz2_binder_probability"

#: A single-chain target sequence, not a proteome.  Boltz-2's own limits are far
#: below this; the cap is here so that pointing the field at the wrong file
#: fails while reading it rather than after an hour of folding.
_MAX_FASTA_BYTES = 4 * 1024 * 1024

#: An a3m for one chain.  Deep alignments are large, and this is generous
#: rather than tight: colabfold_search routinely writes tens of megabytes.
_MAX_MSA_BYTES = 512 * 1024 * 1024

#: How much of a failed run's console output travels into the error context.
_MAX_LOG_TAIL = 4000

#: Read in blocks so that hashing a large alignment does not hold two copies.
_READ_CHUNK = 4 * 1024 * 1024

_AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWYXBZJUO")
#: Every nucleotide letter is also an amino-acid letter, so the alphabet alone
#: cannot tell a gene from a protein.  Composition can: a sequence of any real
#: length drawn only from these is nucleic acid, because a protein made
#: exclusively of Ala/Cys/Gly/Thr/Sec/Asn for twenty residues does not occur.
#: Without this check a DNA FASTA folds into a meaningless chain and returns a
#: number, which is the one outcome worse than an error.
_NUCLEOTIDES = set("ACGTUN")
_NUCLEOTIDE_MIN_LENGTH = 20

_UNPINNED_WARNING = "BOLTZ2_BACKEND_VERSION_UNPINNED"


class Boltz2Config(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    #: Which engine's environment overrides apply, giving the paths below
    #: ``MOLCASCADE_BOLTZ2_EXECUTABLE`` and ``MOLCASCADE_BOLTZ2_CACHE_DIR``.
    #: See :mod:`molcascade.plugins.builtin._machine_paths`.
    engine_id: ClassVar[str] = "boltz2"

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """An executable in an environment of its own, and a weight cache.

        The cache is the interesting one.  Boltz downloads its weights on first
        use, into ``BOLTZ_CACHE`` or ``~/.boltz``, and a download that happens
        inside a stage is a network call nobody asked for at the worst moment --
        after the run has started, on whichever worker got there first.  So the
        directory is a machine path like any other and is checked for contents
        before the first molecule is written; the remedy is the warmup command.
        """

        cache = os.environ.get("BOLTZ_CACHE", "").strip()
        cache_root = Path(cache).expanduser() if cache else Path.home() / ".boltz"
        return (
            MachinePath(
                field="executable",
                label="the boltz CLI inside Boltz-2's own environment",
                candidates=conda_environment("boltz2", "bin/boltz"),
                remedy="bash envs/bootstrap.sh boltz2",
            ),
            MachinePath(
                field="cache_dir",
                label="the directory Boltz downloads its weights and CCD into",
                kind="directory",
                candidates=(cache_root,),
                contents=("boltz2_conf.ckpt",),
                remedy="bash envs/bootstrap.sh boltz2",
                note=(
                    "That step runs one throwaway prediction so the ~3 GB of weights "
                    "and the CCD dictionary are already on disk when a screen starts. "
                    "Set BOLTZ_CACHE to move them off the home filesystem."
                ),
            ),
        )

    #: Absolute path to ``boltz`` inside the environment created for it. Not a
    #: bare name: that environment is deliberately not on this process's PATH.
    executable: str = Field(default="", max_length=4096)

    #: Where Boltz keeps its weights and the CCD.  Passed as ``--cache``, and
    #: required to be non-empty before the run starts.
    cache_dir: str = Field(default="", max_length=4096)

    #: Absolute path to a FASTA holding the target's single-chain sequence. Read
    #: for its residues and hashed into ``model_id``; see the module docstring
    #: for why this is not derived from the receptor PDB.  Required, and with no
    #: default: unlike the two machine paths above, nothing can discover this
    #: one, and a blank that survived validation would fail later as an
    #: unreadable file named "" rather than as a missing target.
    target_fasta_path: str = Field(max_length=4096)

    #: Where the alignment comes from.
    #:
    #: ``local_a3m`` reads ``msa_a3m_path`` -- the default, and the only mode
    #: that neither degrades the prediction nor sends anything anywhere.
    #: ``empty`` runs single-sequence, which Boltz's own documentation
    #: discourages; it exists so that an operator can say so explicitly instead
    #: of discovering it from a quiet fallback.  ``colabfold_server`` passes
    #: ``--use_msa_server``, which uploads the target sequence to
    #: https://api.colabfold.com -- a third party -- and so is never implied.
    msa_mode: Literal["local_a3m", "empty", "colabfold_server"] = "local_a3m"

    #: Absolute path to the target's a3m alignment.  Required when ``msa_mode``
    #: is ``local_a3m``; its bytes are hashed into ``model_id``, because two
    #: alignments of the same sequence are two different predictions.
    msa_a3m_path: str = Field(default="", max_length=4096)

    #: The hard stop on how many molecules this tier will accept.  Tens of
    #: seconds each on a current card: see the module docstring on why this is
    #: not optional.
    max_molecules: int = Field(default=200, ge=1, le=100_000)

    #: Wall-clock budget per molecule, multiplied by the number of molecules in
    #: the batch.  A deadlock guard around one long-running child, not a
    #: per-complex limit Boltz itself would honour.
    timeout_per_molecule_seconds: float = Field(default=600.0, gt=0.0, le=86_400.0)

    #: Boltz-2's affinity module is trained and evaluated on small molecules;
    #: its own documentation gives 128 atoms as the ceiling and discourages
    #: anything above about 56.  Counted with hydrogens, which is the
    #: conservative reading, and enforced before a YAML is written so that an
    #: oversized ligand costs nothing rather than costing a fold.
    max_ligand_atoms: int = Field(default=128, ge=1, le=1024)

    #: ``--diffusion_samples_affinity``.  The affinity head is sampled, so this
    #: is the sample count that produced the number and it belongs in the
    #: method identity.
    diffusion_samples_affinity: int = Field(default=5, ge=1, le=100)

    #: ``--affinity_mw_correction``.  Off, matching the CLI's own default.  It
    #: removes the molecular-weight trend from the predicted affinity, which is
    #: the same idea as the size-normalized docking metrics two tiers up -- and
    #: like those, it changes what the number means, so it is recorded in
    #: ``model_id`` rather than applied quietly.
    affinity_mw_correction: bool = False

    #: ``--seed``.  Diffusion sampling is seeded, so the same seed is the same
    #: measurement and a different seed is a different one.
    seed: int = Field(default=42, ge=0, le=2_147_483_647)

    #: The Boltz release, as the operator knows it to be.  Optional and hashed
    #: into ``model_id`` when given; when it is not, the stage reports
    #: ``BOLTZ2_BACKEND_VERSION_UNPINNED`` so the gap is visible rather than
    #: implied.  ``boltz --version`` is not consulted: a version read at run
    #: time describes the machine, not the prediction that was archived.
    backend_version: str | None = Field(default=None, min_length=1, max_length=64)

    #: Where the YAML inputs and the prediction outputs are written.  Boltz
    #: writes structures beside every affinity JSON, so this wants space.
    scratch_dir: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def _fill_machine_paths(cls, data: Any) -> Any:
        return fill_machine_paths(data, engine_id=cls.engine_id, paths=cls.installed_paths())

    @field_validator("executable")
    @classmethod
    def _executable_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="executable", engine_id=cls.engine_id)

    @field_validator("cache_dir")
    @classmethod
    def _cache_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="cache_dir", engine_id=cls.engine_id)

    @field_validator("target_fasta_path")
    @classmethod
    def _target_is_given(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "'target_fasta_path' is required: this stage folds a named receptor, "
                "and the sequence is the one input nothing on the machine can supply "
                "for it"
            )
        return absolute_path(value, field="target_fasta_path")

    @field_validator("msa_a3m_path")
    @classmethod
    def _alignment_matches_the_mode(cls, value: str, info: Any) -> str:
        """Required unless the mode says otherwise, and refused when it does.

        Checked here rather than in a model validator so the objection names the
        field that caused it.  ``msa_mode`` is declared above, so it is already
        in ``info.data`` -- unless it was itself rejected, in which case there is
        nothing to compare against and the mode's own error is the one to show.
        """

        mode = info.data.get("msa_mode")
        if mode is None:
            return value
        if mode == "local_a3m" and not value:
            raise ValueError(
                "'msa_a3m_path' is required unless 'msa_mode' says otherwise: set it "
                "to the a3m for your target, or choose msa_mode='empty' to predict "
                "from the single sequence, or msa_mode='colabfold_server' to have "
                "Boltz upload the sequence to api.colabfold.com and build one there"
            )
        if mode != "local_a3m" and value:
            raise ValueError(
                f"'msa_a3m_path' is set but msa_mode is {mode!r}, so the file would "
                "not be read; remove one of the two"
            )
        return "" if not value else absolute_path(value, field="msa_a3m_path")

    @field_validator("scratch_dir")
    @classmethod
    def _scratch_is_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="scratch_dir")


def _validated_config(request: StageRequest) -> Boltz2Config:
    try:
        return Boltz2Config.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid Boltz-2 configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            hint=(
                "'executable' is the absolute path to the boltz CLI inside the "
                "environment you created for it, 'target_fasta_path' is your target's "
                "sequence, and 'msa_a3m_path' is its alignment. Run "
                "'bash envs/bootstrap.sh boltz2' to create the environment and "
                "pre-download the weights."
            ),
            context={"error_count": error.error_count()},
        ) from error


def _prepare_outputs(context: StageContext) -> tuple[Path, Path]:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    relatives = (_PARENT_PATH, _PREDICTION_PATH)
    destinations = tuple(context.staging_root / relative for relative in relatives)
    for relative, destination in zip(relatives, destinations, strict=True):
        if destination.exists() or destination.is_symlink():
            raise PluginError(
                f"Boltz-2 output already exists: {relative.as_posix()}",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": relative.as_posix()},
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
    return destinations[0], destinations[1]


def _resolved_executable(configured: str) -> Path:
    """Check the interpreter-side facts about ``boltz`` before using it.

    ``shutil.which`` is deliberately not consulted: the environment this
    adapter calls into must not be on this process's PATH, because keeping its
    Torch away from this one's is the whole reason it exists elsewhere.
    """

    path = Path(configured)
    try:
        info = path.stat()
    except OSError as error:
        raise PluginError(
            "the boltz CLI was not found at the configured path",
            code="BOLTZ2_EXECUTABLE_MISSING",
            hint=(
                "Create the environment once, outside MolCascade: "
                "'bash envs/bootstrap.sh boltz2', or by hand with "
                "'conda create -n boltz2 \"python>=3.10,<3.13\"' followed by "
                "'conda run -n boltz2 python -m pip install boltz'. Then point this "
                "stage at the absolute path 'conda run -n boltz2 which boltz' prints. "
                "MolCascade never installs anything itself."
            ),
            context={"executable": configured, "error_type": type(error).__name__},
        ) from error
    if not stat.S_ISREG(info.st_mode):
        raise PluginError(
            "the configured boltz path is not a regular file",
            code="BOLTZ2_EXECUTABLE_MISSING",
            context={"executable": configured},
        )
    if not os.access(path, os.X_OK):
        raise PluginError(
            "the configured boltz path is not executable by this user",
            code="BOLTZ2_EXECUTABLE_MISSING",
            context={"executable": configured},
        )
    return path


def _resolved_cache(configured: str) -> Path:
    """Refuse an empty weight cache here rather than downloading from a stage.

    Boltz fetches about three gigabytes on first use.  Inside a screen that is
    a network call in the middle of a run, on a machine that may not have one,
    at a point where the failure looks like a prediction failure.  The warmup
    is a separate, explicit act.
    """

    path = Path(configured)
    if not path.is_dir():
        raise PluginError(
            "the Boltz weight cache directory does not exist",
            code="BOLTZ2_WEIGHTS_MISSING",
            hint=(
                "Run the warmup once: 'bash envs/bootstrap.sh boltz2' predicts one "
                "throwaway complex so the weights and the CCD are on disk before a "
                "screen starts. Set BOLTZ_CACHE to choose where they live."
            ),
            context={"cache_dir": configured},
        )
    try:
        empty = not any(path.iterdir())
    except OSError as error:
        raise PluginError(
            "the Boltz weight cache directory could not be read",
            code="BOLTZ2_WEIGHTS_MISSING",
            context={"cache_dir": configured, "error_type": type(error).__name__},
        ) from error
    if empty:
        raise PluginError(
            "the Boltz weight cache directory is empty",
            code="BOLTZ2_WEIGHTS_MISSING",
            hint=(
                "Boltz would download about 3 GB on first use. Do that once, "
                "deliberately: 'bash envs/bootstrap.sh boltz2'."
            ),
            context={"cache_dir": configured},
        )
    return path


def _file_digest(configured: str, *, field: str, code: str, hint: str, limit: int) -> str:
    """Hash one operator-supplied input file, without interpreting it.

    Both files this is used for -- the FASTA and the a3m -- decide what the
    prediction means, so their bytes belong in the method identity.  Neither
    one's schema belongs to this project.
    """

    path = Path(configured)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError(
            f"the file named by '{field}' could not be opened",
            code=code,
            hint=hint,
            context={field: configured, "error_type": type(error).__name__},
        ) from error
    digest = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PluginError(
                f"the path named by '{field}' is not a regular file",
                code=code,
                context={field: configured},
            )
        if info.st_size > limit:
            raise PluginError(
                f"the file named by '{field}' is larger than this stage will read",
                code=code,
                context={field: configured, "size_bytes": info.st_size, "limit_bytes": limit},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            while True:
                block = stream.read(_READ_CHUNK)
                if not block:
                    break
                digest.update(block)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _target_sequence(configured: str) -> str:
    """Read exactly one chain out of the target FASTA.

    One chain, because the affinity head takes one binder against one receptor
    and a multi-chain FASTA would leave this adapter guessing which chain the
    pocket is in.  Guessing is the failure mode this whole field exists to
    avoid, so two records is an error with both names in it.
    """

    path = Path(configured)
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        raise PluginError(
            "the target FASTA could not be read as text",
            code="BOLTZ2_TARGET_SEQUENCE_UNREADABLE",
            context={"target_fasta_path": configured, "error_type": type(error).__name__},
        ) from error

    headers: list[str] = []
    chunks: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith((">", ";")):
            headers.append(stripped[1:81])
            chunks.append([])
            continue
        if not chunks:
            # A bare sequence with no header at all: accepted, because a file
            # holding one sequence and nothing else is unambiguous.
            headers.append("")
            chunks.append([])
        chunks[-1].append(stripped)

    if not chunks:
        raise PluginError(
            "the target FASTA holds no sequence",
            code="BOLTZ2_TARGET_SEQUENCE_UNREADABLE",
            context={"target_fasta_path": configured},
        )
    if len(chunks) > 1:
        records: list[JsonValue] = list(headers[:8])
        raise PluginError(
            f"the target FASTA holds {len(chunks)} sequences, and this stage folds one",
            code="BOLTZ2_TARGET_SEQUENCE_AMBIGUOUS",
            hint=(
                "Boltz-2's affinity head scores one ligand against one receptor chain. "
                "Split the file and point this stage at the chain your pocket is in."
            ),
            context={"target_fasta_path": configured, "records": records},
        )

    sequence = "".join(chunks[0]).replace(" ", "").upper()
    if not sequence:
        raise PluginError(
            "the target FASTA record has a header and no residues",
            code="BOLTZ2_TARGET_SEQUENCE_UNREADABLE",
            context={"target_fasta_path": configured},
        )
    unknown = sorted(set(sequence) - _AMINO_ACIDS)
    if unknown:
        unexpected: list[JsonValue] = list(unknown[:8])
        raise PluginError(
            "the target FASTA contains characters that are not amino acids",
            code="BOLTZ2_TARGET_SEQUENCE_UNREADABLE",
            hint=(
                "This field wants a protein sequence. An alignment or a PDB saved "
                "with the wrong extension both land here."
            ),
            context={"target_fasta_path": configured, "unexpected": unexpected},
        )
    if len(sequence) >= _NUCLEOTIDE_MIN_LENGTH and not set(sequence) - _NUCLEOTIDES:
        raise PluginError(
            "the target FASTA looks like a nucleotide sequence, not a protein",
            code="BOLTZ2_TARGET_SEQUENCE_UNREADABLE",
            hint=(
                "Every DNA letter is also an amino-acid letter, so this is judged by "
                "composition rather than by alphabet: nothing in the file is invalid, "
                "it just reads as a gene. Point this at the translated sequence. If "
                "the target really is a protein of only these residues, this stage "
                "cannot tell the difference and cannot fold it."
            ),
            context={"target_fasta_path": configured, "length": len(sequence)},
        )
    return sequence


def _isolated_environment(device: str | None, cache: Path) -> dict[str, str]:
    """This process's environment minus the parts that would leak into theirs.

    ``PYTHONPATH`` and ``PYTHONHOME`` are how one environment's site-packages
    ends up in front of another's, and keeping Boltz's Torch away from this
    one's is the entire reason it runs out of process.

    ``CUDA_VISIBLE_DEVICES`` is set rather than passed as a flag: ``--devices``
    is a count, not an ordinal, so the only way to say *which* card is to leave
    one visible.

    The OpenMP pin is not a performance setting; it is what keeps Boltz's
    preprocessing pool from deadlocking.  See the comment on it below.
    """

    environment = dict(os.environ)
    for leaked in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(leaked, None)
    environment["BOLTZ_CACHE"] = str(cache)
    # Boltz preprocesses through a fork-based ``multiprocessing.Pool`` *after*
    # its parent has imported Torch, built a CUDA context and run
    # OpenMP-parallel code.  ``fork()`` copies only the calling thread, so each
    # child inherits a libgomp that still believes it owns a team of workers
    # which no longer exist -- and this interpreter maps two libgomps, its own
    # plus the copy vendored into a manylinux wheel.  A child that then enters a
    # parallel region spins in libgomp's busy-wait barrier for ever: pure user
    # time, one thread, flat RSS, no syscalls.  Nothing recovers from it,
    # because ``pool.imap`` yields in order -- the parent blocks on the first
    # stuck index, the manifest that gates inference is never written, and the
    # GPU is never asked for a single batch.  Seen here on 4 of 32 workers,
    # 113 minutes into a stage that had already finished 865 of 876 molecules.
    #
    # A one-thread team has no barrier to spin on, which closes the hazard for
    # the preprocessing pool and the dataloader workers alike.  Nothing is
    # given up: both are already parallel per molecule, so nested OpenMP was
    # only ever contention.
    environment["OMP_NUM_THREADS"] = "1"
    environment["MKL_NUM_THREADS"] = "1"
    if device and device.startswith("cuda:"):
        environment["CUDA_VISIBLE_DEVICES"] = device.split(":", 1)[1]
    return environment


MAX_PREPROCESSING_THREADS = 8
"""How many children Boltz's preprocessing pool may fork, at most.

Boltz defaults ``--preprocessing-threads`` to ``multiprocessing.cpu_count()``,
which on a 32-core host forks 32 children out of a CUDA-initialised parent --
see ``_isolated_environment`` for what that has cost.  Preprocessing runs at
roughly 0.4 s/molecule, so eight threads clear a 900-molecule batch in under a
minute: the cap buys safety for nothing.  ``--num_workers`` stays at Boltz's
own default of 2, which is small already and no longer dangerous once OpenMP is
pinned to one thread.
"""


def _preprocessing_threads() -> int:
    """The cap, or the core count when the machine has fewer cores than that.

    Boltz clamps this to the number of inputs itself, so the only job here is to
    stop the default from scaling with the host.
    """

    return min(MAX_PREPROCESSING_THREADS, os.cpu_count() or 1)


def _ligand_atoms(smiles: str) -> int | None:
    """Count the atoms Boltz would be asked to place, hydrogens included.

    ``None`` when RDKit cannot parse the SMILES: the caller records that
    separately from "too large", because they are different problems with the
    same consequence.
    """

    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return None
    return int(Chem.AddHs(molecule).GetNumAtoms())


def _read_affinity(path: Path) -> dict[str, float]:
    """Pull the affinity head's numbers out of one prediction JSON.

    Boltz writes the ensemble members alongside the aggregate as
    ``affinity_pred_value1`` and ``affinity_pred_value2``.  They are not
    samples of one distribution, so they do not become a standard deviation;
    they become the interval, which is exactly what the range of an ensemble
    is.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PluginError(
            "a Boltz-2 affinity result could not be parsed",
            code="BOLTZ2_OUTPUT_UNREADABLE",
            context={"path": path.name, "error_type": type(error).__name__},
        ) from error
    if not isinstance(payload, dict):
        raise PluginError(
            "a Boltz-2 affinity result is not a JSON object",
            code="BOLTZ2_OUTPUT_UNREADABLE",
            context={"path": path.name},
        )

    numbers: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        number = float(value)
        if not math.isfinite(number):
            continue
        numbers[str(key)] = number
    for required in ("affinity_pred_value", "affinity_probability_binary"):
        if required not in numbers:
            keys: list[JsonValue] = list(sorted(numbers)[:12])
            raise PluginError(
                f"a Boltz-2 affinity result has no finite '{required}'",
                code="BOLTZ2_OUTPUT_UNREADABLE",
                hint=(
                    "The affinity head only runs when the input YAML asks for it. If "
                    "this file came from a hand-written input, check that its "
                    "'properties' block names the ligand chain as the binder."
                ),
                context={"path": path.name, "keys": keys},
            )
    return numbers


def _ensemble_interval(numbers: dict[str, float], prefix: str) -> tuple[float | None, float | None]:
    members = [value for key, value in numbers.items() if re.fullmatch(rf"{prefix}\d+", key)]
    if len(members) < 2:
        return None, None
    return min(members), max(members)


class Boltz2AffinityPlugin:
    """Predict binding affinity by co-folding the complex, out of process."""

    descriptor = PluginDescriptor(
        id="prediction.boltz2",
        version="0.1.0",
        kind=PluginKind.PREDICTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PREDICTION_V1.id),
        output_ports={"primary": PARENT_V1.id, "predictions": PREDICTION_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        # Seeded diffusion sampling in someone else's Torch build: the seed is
        # recorded and honoured, and bit-for-bit equality across cards is not
        # something this adapter can promise on its behalf.
        determinism=Determinism.SEEDED,
        display_name="Boltz-2 co-folded affinity",
        description=(
            "Open-weight co-folding model with an affinity head, executed in a "
            "separate environment through the boltz CLI. Predicts log10 IC50 in "
            "micromolar (lower is stronger) and a binder probability; ranks rather "
            "than calibrates, and needs a target sequence and a local alignment."
        ),
    )
    config_model = Boltz2Config

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)

        # Every check that can be made without reading a molecule is made
        # before one is read, so a missing environment or an unreadable
        # alignment costs nothing rather than costing a pass over the shortlist.
        executable = _resolved_executable(config.executable)
        cache = _resolved_cache(config.cache_dir)
        sequence = _target_sequence(config.target_fasta_path)
        fasta_sha256 = _file_digest(
            config.target_fasta_path,
            field="target_fasta_path",
            code="BOLTZ2_TARGET_SEQUENCE_UNREADABLE",
            hint="This is the FASTA holding your target's single-chain sequence.",
            limit=_MAX_FASTA_BYTES,
        )
        msa_sha256: str | None = None
        if config.msa_mode == "local_a3m":
            msa_sha256 = _file_digest(
                config.msa_a3m_path,
                field="msa_a3m_path",
                code="BOLTZ2_MSA_UNREADABLE",
                hint=(
                    "This is the a3m alignment for your target. Produce one with "
                    "colabfold_search or MMseqs2, or choose msa_mode='empty' to "
                    "predict from the single sequence and accept the loss of accuracy."
                ),
                limit=_MAX_MSA_BYTES,
            )

        parent_destination, prediction_destination = _prepare_outputs(context)
        model_id = "boltz2:sha256:" + canonical_sha256(
            {
                "backend": "boltz",
                "backend_version": config.backend_version,
                "implementation_version": _IMPLEMENTATION_VERSION,
                "method": "Boltz-2 co-folding with the affinity head",
                "doi": "10.1101/2025.06.14.659707",
                "target_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                "target_fasta_sha256": fasta_sha256,
                "msa_mode": config.msa_mode,
                "msa_sha256": msa_sha256,
                "diffusion_samples_affinity": config.diffusion_samples_affinity,
                "affinity_mw_correction": config.affinity_mw_correction,
                "seed": config.seed,
                "max_ligand_atoms": config.max_ligand_atoms,
            }
        )

        try:
            accepted, skipped_large, unparseable = self._write_parents(
                stage_input,
                parent_destination,
                config=config,
            )
            results = self._predict(
                accepted,
                config=config,
                context=context,
                executable=executable,
                cache=cache,
                sequence=sequence,
            )
            written = self._write_predictions(
                prediction_destination,
                accepted=accepted,
                results=results,
                model_id=model_id,
            )
        except BaseException:
            parent_destination.unlink(missing_ok=True)
            prediction_destination.unlink(missing_ok=True)
            raise

        input_count = len(accepted) + len(skipped_large) + len(unparseable)
        endpoints: list[JsonValue] = [_AFFINITY_ENDPOINT, _BINDER_ENDPOINT]
        too_large_examples: list[JsonValue] = list(skipped_large[:8])
        unparseable_examples: list[JsonValue] = list(unparseable[:8])
        warnings: list[JsonValue] = [] if config.backend_version else [_UNPINNED_WARNING]
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    (_PARENT_PATH.as_posix(),),
                    {"row_count": input_count},
                ),
                "predictions": PendingOutput(
                    PREDICTION_V1.id,
                    (_PREDICTION_PATH.as_posix(),),
                    {"row_count": written, "model_id": model_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "model_id": model_id,
                "endpoints": endpoints,
                "affinity_direction": "LOWER_STRONGER",
                "affinity_units": "log10(IC50 / uM)",
                "calibrated": False,
                "predicted_count": len(results),
                "prediction_row_count": written,
                # Counted rather than recorded as rows: prediction/v1 cannot
                # hold "unknown", so these molecules carry no evidence and a
                # fail-closed gate is what turns that into a rejection.
                "skipped_too_large_count": len(skipped_large),
                "skipped_too_large_examples": too_large_examples,
                "skipped_unparseable_count": len(unparseable),
                "skipped_unparseable_examples": unparseable_examples,
                "max_ligand_atoms": config.max_ligand_atoms,
                "msa_mode": config.msa_mode,
                "seed": config.seed,
                "backend": "boltz",
                "backend_version": config.backend_version,
                "warnings": warnings,
            },
        )

    def _write_parents(
        self,
        stage_input: Any,
        destination: Path,
        *,
        config: Boltz2Config,
    ) -> tuple[list[tuple[str, str]], list[str], list[str]]:
        """Pass the parents through and decide which of them can be folded.

        The whole shortlist is held in memory, which is affordable precisely
        because ``max_molecules`` refuses the case where it would not be.
        """

        accepted: list[tuple[str, str]] = []
        too_large: list[str] = []
        unparseable: list[str] = []
        seen = 0
        with pq.ParquetWriter(destination, PARENT_V1.schema, compression="zstd") as writer:
            for batch in iter_contract_batches(
                stage_input,
                PARENT_V1,
                batch_size=config.batch_size,
            ):
                for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
                    seen += 1
                    if seen > config.max_molecules:
                        raise PluginError(
                            "co-folding was given more molecules than this stage allows",
                            code="BOLTZ2_INPUT_TOO_LARGE",
                            hint=(
                                "One complex is tens of seconds on a current card, so "
                                f"{config.max_molecules} molecules is already hours. Put "
                                "this tier last, behind a threshold that has reduced the "
                                "library, or raise 'max_molecules' knowing the run scales "
                                "linearly with it."
                            ),
                            context={
                                "max_molecules": config.max_molecules,
                                "molecules_seen": seen,
                            },
                        )
                    parent_id = str(row.get("parent_id"))
                    smiles = row.get("parent_smiles")
                    atoms = _ligand_atoms(smiles) if isinstance(smiles, str) and smiles else None
                    if atoms is None:
                        unparseable.append(parent_id)
                    elif atoms > config.max_ligand_atoms:
                        too_large.append(parent_id)
                    else:
                        accepted.append((parent_id, str(smiles)))
                writer.write_batch(batch)
        if not seen:
            raise PluginError(
                "co-folding input contains no parents",
                code="BOLTZ2_EMPTY_INPUT",
            )
        if not accepted:
            raise PluginError(
                "no molecule in this population can be folded by the affinity head",
                code="BOLTZ2_NOTHING_TO_PREDICT",
                hint=(
                    "Every molecule was either above 'max_ligand_atoms' or unreadable "
                    "as SMILES. Boltz-2's affinity head is trained on small molecules; "
                    "a population of peptides or macrocycles belongs elsewhere."
                ),
                context={
                    "too_large": len(too_large),
                    "unparseable": len(unparseable),
                    "max_ligand_atoms": config.max_ligand_atoms,
                },
            )
        return accepted, too_large, unparseable

    def _predict(
        self,
        accepted: list[tuple[str, str]],
        *,
        config: Boltz2Config,
        context: StageContext,
        executable: Path,
        cache: Path,
        sequence: str,
    ) -> dict[str, dict[str, float]]:
        """Write one YAML per molecule and run ``boltz predict`` over the directory.

        A directory rather than a call per molecule: that is the CLI's own
        batching mechanism, and it loads the weights once instead of once per
        complex.  The record name is generated here and mapped back to the
        parent, so nothing is joined on position -- which is what a future
        version reordering or deduplicating its output would break silently.
        """

        devices = context.resources.devices or ("cpu",)
        device = devices[0]
        timeout = len(accepted) * config.timeout_per_molecule_seconds
        with tempfile.TemporaryDirectory(
            prefix="molcascade-boltz2-",
            dir=config.scratch_dir,
        ) as scratch_name:
            scratch = Path(scratch_name)
            inputs = scratch / "inputs"
            outputs = scratch / "outputs"
            inputs.mkdir()
            outputs.mkdir()

            records: dict[str, str] = {}
            for index, (parent_id, smiles) in enumerate(accepted):
                record = f"mol-{index:06d}"
                records[record] = parent_id
                (inputs / f"{record}.yaml").write_text(
                    self._input_yaml(sequence, smiles, config=config),
                    encoding="utf-8",
                )

            command = [
                str(executable),
                "predict",
                str(inputs),
                "--out_dir",
                str(outputs),
                "--cache",
                str(cache),
                "--accelerator",
                "gpu" if device.startswith("cuda:") else "cpu",
                "--devices",
                "1",
                "--diffusion_samples_affinity",
                str(config.diffusion_samples_affinity),
                "--seed",
                str(config.seed),
                "--preprocessing-threads",
                str(_preprocessing_threads()),
            ]
            if config.affinity_mw_correction:
                command.append("--affinity_mw_correction")
            if config.msa_mode == "colabfold_server":
                command.append("--use_msa_server")

            try:
                # An argv list, never a shell string: nothing an operator
                # configures is parsed by /bin/sh, and every path in it was
                # validated as absolute before we got here.
                completed = subprocess.run(
                    command,
                    cwd=scratch,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=_isolated_environment(device, cache),
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise PluginError(
                    "boltz did not finish within the configured budget",
                    code="BOLTZ2_TIMEOUT",
                    hint=(
                        "Co-folding is tens of seconds per complex on a current card and "
                        "minutes on a slower one; the first call of a cold run also loads "
                        "several gigabytes of weights. Raise "
                        "'timeout_per_molecule_seconds' or reduce 'max_molecules'."
                    ),
                    context={
                        "timeout_seconds": timeout,
                        "molecules": len(accepted),
                        "device": device,
                    },
                ) from error
            except OSError as error:
                raise PluginError(
                    "boltz could not be started",
                    code="BOLTZ2_RUN_FAILED",
                    context={"executable": str(executable), "error_type": type(error).__name__},
                ) from error

            log = completed.stdout.decode("utf-8", errors="replace")
            if completed.returncode != 0:
                raise PluginError(
                    f"boltz exited with status {completed.returncode}",
                    code="BOLTZ2_RUN_FAILED",
                    hint=(
                        "The message below comes from Boltz, not MolCascade. A cache "
                        "without weights and a GPU without enough memory for the "
                        "requested sample count are the usual causes."
                    ),
                    context={
                        "returncode": completed.returncode,
                        "output_tail": log[-_MAX_LOG_TAIL:],
                    },
                )
            return self._collect(outputs, records=records, log=log)

    def _input_yaml(self, sequence: str, smiles: str, *, config: Boltz2Config) -> str:
        """One complex, in Boltz's own input schema.

        Serialized with a YAML dumper rather than a format string: a SMILES is
        full of characters YAML cares about, and quoting them by hand is the
        kind of thing that works until a molecule with a ``#`` in it arrives.
        """

        protein: dict[str, Any] = {"id": "A", "sequence": sequence}
        if config.msa_mode == "local_a3m":
            protein["msa"] = config.msa_a3m_path
        elif config.msa_mode == "empty":
            # Boltz's own spelling for "predict from the single sequence".
            protein["msa"] = "empty"
        document = {
            "version": 1,
            "sequences": [
                {"protein": protein},
                {"ligand": {"id": "B", "smiles": smiles}},
            ],
            "properties": [{"affinity": {"binder": "B"}}],
        }
        return str(yaml.safe_dump(document, sort_keys=False, default_flow_style=False))

    def _collect(
        self,
        outputs: Path,
        *,
        records: dict[str, str],
        log: str,
    ) -> dict[str, dict[str, float]]:
        """Find every affinity JSON under the output tree and key it by parent.

        Globbed rather than assembled from a documented path: Boltz has moved
        its results between ``boltz_results_<name>/predictions/<record>/`` and
        neighbouring layouts across releases, and a hard-coded path turns a
        successful run into an unreadable-output error.  The record name is
        what identifies the molecule, and it is in the file name.
        """

        found: dict[str, dict[str, float]] = {}
        for path in sorted(outputs.rglob("affinity_*.json")):
            record = path.stem[len("affinity_") :]
            parent_id = records.get(record)
            if parent_id is None:
                continue
            found[parent_id] = _read_affinity(path)
        if not found:
            raise PluginError(
                "boltz produced no affinity results",
                code="BOLTZ2_OUTPUT_MISSING",
                hint=(
                    "The run exited cleanly, so this is a layout or an input problem "
                    "rather than a crash. The console tail below is Boltz's own."
                ),
                context={
                    "expected": len(records),
                    "out_dir": outputs.name,
                    "output_tail": log[-_MAX_LOG_TAIL:],
                },
            )
        return found

    def _write_predictions(
        self,
        destination: Path,
        *,
        accepted: list[tuple[str, str]],
        results: dict[str, dict[str, float]],
        model_id: str,
    ) -> int:
        """Two rows per molecule that was predicted, and none for one that was not.

        A molecule Boltz accepted and then failed on gets no row for the same
        reason an oversized one does not: there is no number to write that is
        not an invention.  The count of missing molecules is the difference
        between the parents and these rows, and the gate above sees it as
        absent evidence.
        """

        parent_ids: list[str] = []
        endpoint_ids: list[str] = []
        model_ids: list[str] = []
        means: list[float] = []
        lowers: list[float | None] = []
        uppers: list[float | None] = []
        for parent_id, _smiles in accepted:
            numbers = results.get(parent_id)
            if numbers is None:
                continue
            for endpoint, key, prefix in (
                (_AFFINITY_ENDPOINT, "affinity_pred_value", "affinity_pred_value"),
                (
                    _BINDER_ENDPOINT,
                    "affinity_probability_binary",
                    "affinity_probability_binary",
                ),
            ):
                lower, upper = _ensemble_interval(numbers, prefix)
                parent_ids.append(parent_id)
                endpoint_ids.append(endpoint)
                model_ids.append(model_id)
                means.append(numbers[key])
                lowers.append(lower)
                uppers.append(upper)

        table = pa.table(
            {
                "parent_id": pa.array(parent_ids, pa.string()),
                "endpoint_id": pa.array(endpoint_ids, pa.string()),
                "model_id": pa.array(model_ids, pa.string()),
                "prediction_mean": pa.array(means, pa.float64()),
                "prediction_std": pa.array([None] * len(means), pa.float64()),
                "interval_lower": pa.array(lowers, pa.float64()),
                "interval_upper": pa.array(uppers, pa.float64()),
                "calibration_id": pa.array([None] * len(means), pa.string()),
            },
            schema=PREDICTION_V1.schema,
        )
        PREDICTION_V1.validate(table)
        with pq.ParquetWriter(destination, PREDICTION_V1.schema, compression="zstd") as writer:
            writer.write_table(table)
        return int(table.num_rows)
