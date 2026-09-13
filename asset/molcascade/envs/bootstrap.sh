#!/usr/bin/env bash
#
# Provision every environment MolCascade needs on a fresh Linux + NVIDIA machine.
#
#   bash envs/bootstrap.sh              # everything
#   bash envs/bootstrap.sh prism        # just the main environment
#   bash envs/bootstrap.sh gnina karmadock
#   bash envs/bootstrap.sh boltz2      # opt-in: not in the default set
#   bash envs/bootstrap.sh openadmet   # opt-in: not in the default set
#
# Reads nothing from the network except conda-forge, PyPI, download.pytorch.org,
# data.pyg.org and the GNINA release asset -- plus, for the opt-in openadmet
# target, Hugging Face for the model, Zenodo for its featurizer's weights, and
# the four git repositories upstream's own environment file installs from.
# Every step is idempotent: re-running
# skips what is already in place, so an interrupted bootstrap can simply be re-run.
#
# See envs/README.md for what each environment is for and why they are separate.
set -euo pipefail

# --- knobs -------------------------------------------------------------------
# Where the out-of-process backends live. Nothing here is on PATH by design;
# cascades address these by absolute path.
BACKEND_ROOT="${MOLCASCADE_BACKEND_ROOT:-${HOME}/molcascade-backends}"
# AiZynthFinder's ~750 MB of models and stock.
AIZYNTH_DATA="${MOLCASCADE_AIZYNTH_DATA:-${HOME}/aizynth-data}"
# Boltz-2's ~3 GB of weights plus the CCD. Boltz reads this variable itself, so
# it is spelled its way rather than ours.
BOLTZ_CACHE_DIR="${BOLTZ_CACHE:-${HOME}/.boltz}"
# The checkout this script is being run from.
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

GNINA_VERSION="1.3.3"
GNINA_ASSET="gnina.cuda12.8.static"
GNINA_SHA256="3340c1f49cd3c7c84d8699182a1c6af13c7fa2a22448d1204640446106f72172"

# OpenADMET is pinned twice over, because the code and the model move apart:
# a commit for the CLI contract this adapter was written against, and an
# immutable Hugging Face revision plus a digest for the 49 MB checkpoint.
OPENADMET_COMMIT="55dd001549885f8d8095af14733d61f36fc046c0"
OPENADMET_MODEL_REV="5c4fb081dfec62a5108423d0f86ec99023551bd8"
OPENADMET_HERG_SHA256="93f6527a4766d6c5373eaccf229a0aaf24961a51553f7af87825bbdfca146945"
# The featurizer the hERG model is built on. procedure.yaml sets
# from_chemeleon: true, so this file is not optional -- without it the model
# cannot be constructed at all.
CHEMELEON_URL="https://zenodo.org/records/15460715/files/chemeleon_mp.pt"
CHEMELEON_BYTES="34859448"
# Cross-checked, not merely self-computed: Zenodo publishes an md5 per file in
# its record API, and this digest was taken from a download whose md5 matched
# that published value (6a80b54fdb7de37ef0374d302f01e8ce). A sha256 read off
# whatever bytes happened to arrive would pin the corruption instead.
CHEMELEON_SHA256="c376624d3407204e780a0ed13a9ac097cc9bb1c13ef89cdbc633c1715c183651"

# --- plumbing ----------------------------------------------------------------
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\n\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

command -v conda >/dev/null 2>&1 || die "conda not found; install Miniforge or Anaconda first"
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

env_exists() { conda env list | awk '{print $1}' | grep -qx "$1"; }
pybin() { echo "$(conda info --base)/envs/$1/bin/python"; }

# The classic solver has been observed to consume >10 GB of RAM and get OOM-killed
# on these specs. libmamba is not optional here.
#
# --override-channels because otherwise these environments are built from
# whatever the user's ~/.condarc happens to list, which makes the same script
# produce different environments on different machines and, on one of them,
# none at all: a Tsinghua mirror of pkgs/msys2 answers 403 and conda refuses to
# solve at all. Everything asked for below -- python, rdkit, unidock -- is on
# conda-forge, so naming it and only it is both reproducible and sufficient.
CONDA_CREATE=(conda create --solver=libmamba -y --override-channels -c conda-forge)

# One dropped transfer should not cost a whole solve. conda's own
# remote_max_retries does not cover the case that actually happens on a large
# package: the transfer completes short, and "Downloaded bytes did not match
# Content-Length" is raised *after* the download, as a validation failure rather
# than an HTTP error, so nothing inside conda retries it. Observed here on
# libtorch's 817 MB artifact, which arrived 91% complete and took the
# openadmet environment down with it.
#
# Retrying from out here works because the package cache is the unit of
# progress: every artifact that did land stays in the cache, so attempt two
# re-downloads only what is still missing. The short file has to go first --
# conda writes the final name while downloading, so it would revalidate the
# same truncated bytes and fail the same way -- but *only* the short file, which
# is why this is not `conda clean --tarballs`. That would throw away the
# completed downloads as well and turn a resumable retry into a restart.
#
# Truncation is detected structurally rather than by size: a .conda artifact is
# a zip, and a zip whose end-of-central-directory record cannot be read is
# incomplete. That check reads the tail of the file, not the gigabyte in front
# of it. Bounded at three attempts, because a fourth identical failure is a
# broken mirror or a full disk, and both want a human rather than a loop.
drop_truncated_packages() {
  "$(conda info --base)/bin/python" - <<'PARTIALS'
import zipfile
from pathlib import Path

from conda.base.context import context

removed = 0
for directory in context.pkgs_dirs:
    for artifact in Path(directory).glob("*.conda"):
        try:
            intact = zipfile.is_zipfile(artifact)
        except OSError:
            intact = False
        if intact:
            continue
        try:
            artifact.unlink()
        except OSError:
            continue
        removed += 1
        print(f"    discarded truncated {artifact.name}")
print(f"    {removed} truncated artifact(s) removed; completed downloads kept")
PARTIALS
}

conda_create() {
  local attempt
  for attempt in 1 2 3; do
    if "${CONDA_CREATE[@]}" "$@"; then
      return 0
    fi
    [ "${attempt}" -eq 3 ] && die "conda create failed three times: $*"
    note "conda create failed (attempt ${attempt}/3); dropping truncated artifacts and retrying"
    drop_truncated_packages || true
    sleep 5
  done
}

# =============================================================================
# prism -- MolCascade itself, plus every backend that can share its interpreter.
# =============================================================================
setup_prism() {
  if env_exists prism; then
    note "env 'prism' already exists; installing/refreshing packages into it"
  else
    say "creating env 'prism' (Python 3.12)"
    conda_create -n prism python=3.12
  fi
  local py; py="$(pybin prism)"

  say "prism: MolCascade (editable) with all in-process extras"
  # 'all-backends' is FPSim2 + admet-ai + chembl_structure_pipeline + medchem +
  # meeko + mordredcommunity + onnxruntime. 'analytics' adds duckdb, which
  # all-backends deliberately excludes. 'dev' brings pytest/ruff/mypy.
  "${py}" -m pip install -e "${REPO_ROOT}[all-backends,analytics,dev]"

  # meeko 0.7.1 imports gemmi at package-import time but does not declare it.
  # Without this, `import meeko` raises ModuleNotFoundError while
  # `molcascade doctor` still reports meeko as available (it probes with
  # importlib.util.find_spec, which only needs the directory to exist).
  say "prism: meeko's undeclared gemmi dependency"
  "${py}" -m pip install gemmi

  # Open Babel is in no extra on purpose -- it is GPL-2.0-only and the default
  # deployment policy refuses copyleft backends. Two catalogued backends and two
  # tests need it present. Skip this line if your project cannot accept GPL.
  say "prism: Open Babel (GPL-2.0-only; stays licence-blocked until --allow-copyleft)"
  "${py}" -m pip install openbabel-wheel

  note "torch: $("${py}" -c 'import torch;print(torch.__version__, "cuda", torch.cuda.is_available())')"
}

# =============================================================================
# karmadock -- pins rdkit 2022.09.1, so it can never share prism's interpreter.
# =============================================================================
setup_karmadock() {
  local repo="${BACKEND_ROOT}/KarmaDock"

  if [ -d "${repo}/.git" ]; then
    note "KarmaDock checkout already at ${repo}"
  else
    say "cloning KarmaDock into ${repo}"
    mkdir -p "${BACKEND_ROOT}"
    git clone --depth 1 https://github.com/schrojunzhang/KarmaDock.git "${repo}"
  fi
  # The screening weights are committed to the repo (15.6 MB, not LFS), so the
  # Zenodo 7789066 download that KarmaDock's README describes is unnecessary.
  [ -f "${repo}/trained_models/karmadock_screening.pkl" ] \
    || die "KarmaDock weights missing at ${repo}/trained_models/karmadock_screening.pkl"

  if env_exists karmadock; then
    note "env 'karmadock' already exists"
  else
    # conda handles only python + rdkit. Asking it to solve the torch stack too
    # sends libmamba into a multi-GB search; the PyG wheel index below is both
    # faster and exactly version-matched.
    say "creating env 'karmadock' (Python 3.10 + rdkit 2022.09.1)"
    conda_create -n karmadock python=3.10 "rdkit=2022.09.1"
  fi
  local py; py="$(pybin karmadock)"

  # rdkit 2022.09.1's compiled extension is built against the numpy 1.x ABI, so
  # numpy must stay below 2. Pinning it in each pip call stops a transitive
  # dependency from silently upgrading it and breaking `import rdkit`.
  local NP="numpy==1.26.4"

  say "karmadock: torch 2.5.1+cu124"
  "${py}" -m pip install "torch==2.5.1" "${NP}" \
    --index-url https://download.pytorch.org/whl/cu124

  # Prebuilt against this exact torch -- no compilation, no solver.
  # All three are required: torch_cluster by dataset/protein_feature.py and
  # torch_sparse by architecture/GraphTransformer_Block.py. Neither KarmaDock's
  # README nor MolCascade's backend catalogue mentions either one, and both fail
  # only at import time, well after the environment looks finished.
  say "karmadock: torch_scatter / torch_cluster / torch_sparse prebuilt for pt25cu124"
  "${py}" -m pip install "torch_scatter==2.1.2" "torch_cluster==1.6.3" \
    "torch_sparse==0.6.18" "${NP}" \
    -f https://data.pyg.org/whl/torch-2.5.1+cu124.html

  say "karmadock: remaining runtime dependencies"
  # bio-embeddings-esm appears in KarmaDock's own env file but is training-only.
  "${py}" -m pip install "torch_geometric==2.5.3" "${NP}" \
    MDAnalysis prody networkx joblib scipy pandas tqdm rmsd prefetch_generator

  # KarmaDock loads its checkpoint with strict=False, so a state_dict that
  # matches nothing at all still "succeeds" and leaves the network at its random
  # initialisation -- scoring every ligand with an untrained model. Assert that
  # the weights actually land. The checkpoint was saved from a DataParallel
  # wrapper, so its keys carry a "module." prefix and the wrapper is required
  # for them to match; that is what virtual_screening.py:89 does.
  say "karmadock: checking the checkpoint really loads"
  ( cd "${repo}" && "${py}" - <<'SMOKE'
import sys, torch, torch.nn as nn
sys.path.insert(0, ".")
from architecture.KarmaDock_architecture import KarmaDock

model = nn.DataParallel(KarmaDock(), device_ids=[0], output_device=0).to("cuda:0")
state = torch.load("trained_models/karmadock_screening.pkl",
                   map_location="cuda:0", weights_only=True)["model_state_dict"]
key = "module.lig_encoder.node_encoder.weight"
before = model.state_dict()[key].clone()
missing, _ = model.load_state_dict(state, strict=False)
if missing:
    sys.exit(f"FAIL: {len(missing)} model tensors were not in the checkpoint")
if torch.equal(before, model.state_dict()[key]):
    sys.exit("FAIL: weights did not change; the model is still random")
print(f"    OK: {len(state)} tensors loaded, weights changed")
SMOKE
  )

  note "karmadock python: ${py}"
  note "karmadock repo:   ${repo}"
}

# =============================================================================
# unidock -- conda-forge package, built per Python ABI; must not enter prism.
# =============================================================================
setup_unidock() {
  if env_exists unidock; then
    note "env 'unidock' already exists"
  else
    # This package declares python/rdkit/numpy/pandas/mdanalysis/openmm deps and
    # is built per-Python-ABI. Installed into prism, conda would overwrite
    # prism's pip-installed rdkit and numpy. MolCascade only ever runs the
    # binary as a subprocess, so there is no benefit to co-locating it.
    say "creating env 'unidock' (Uni-Dock 1.2.0, CUDA build)"
    conda_create -n unidock unidock
  fi
  note "unidock binary: $(conda info --base)/envs/unidock/bin/unidock"
}

# =============================================================================
# aizynth -- pulls rdkit 2023.9.6 + numpy 1.26; cannot share prism either.
# =============================================================================
setup_aizynth() {
  if env_exists aizynth; then
    note "env 'aizynth' already exists"
  else
    say "creating env 'aizynth' (Python 3.12 + AiZynthFinder 4.4.1)"
    conda_create -n aizynth python=3.12
    # No extra needed: 4.4.1's base dependencies already include onnxruntime,
    # rdkit, tables and everything aizynthcli uses.
    "$(pybin aizynth)" -m pip install "aizynthfinder==4.4.1"
  fi

  if [ -f "${AIZYNTH_DATA}/config.yml" ]; then
    note "AiZynthFinder data already at ${AIZYNTH_DATA}"
  else
    say "downloading AiZynthFinder public data (~750 MB) to ${AIZYNTH_DATA}"
    mkdir -p "${AIZYNTH_DATA}"
    ( cd "${AIZYNTH_DATA}" && "$(conda info --base)/envs/aizynth/bin/download_public_data" . )
  fi
  note "aizynthcli: $(conda info --base)/envs/aizynth/bin/aizynthcli"
  note "config:     ${AIZYNTH_DATA}/config.yml"
}

# =============================================================================
# boltz2 -- Boltz-2 co-folding, for the optional last tier. MIT, code and weights.
#
# The conflict here is torch, not Python: boltz accepts prism's 3.12, but
# resolving it into prism re-pins the torch ADMET-AI predicts with in-process
# and KarmaDock was built against. So it gets an interpreter of its own, and
# MolCascade calls the `boltz` CLI in it by absolute path like every other
# ISOLATED backend.
#
# Not in the default target list: this is ~3 GB of weights on top of another
# torch, for a tier that ships switched off. Ask for it by name.
# =============================================================================
setup_boltz2() {
  if env_exists boltz2; then
    note "env 'boltz2' already exists"
  else
    say "creating env 'boltz2' (Python 3.12 + Boltz-2)"
    conda_create -n boltz2 python=3.12
    # The [cuda] extra is cuequivariance's fused kernels; boltz runs without
    # them, slower. It brings its own torch -- that is the entire point of the
    # separate environment.
    "$(pybin boltz2)" -m pip install "boltz[cuda]==2.2.1" \
      || "$(pybin boltz2)" -m pip install "boltz==2.2.1"
  fi

  # Boltz downloads on first use, inside whatever process got there first. That
  # is a 3 GB network call in the middle of a screen, so it happens here
  # instead, and the plugin refuses to start until this directory is populated.
  if [ -f "${BOLTZ_CACHE_DIR}/boltz2_conf.ckpt" ]; then
    note "Boltz-2 weights already at ${BOLTZ_CACHE_DIR}"
  else
    say "downloading Boltz-2 weights and the CCD (~3 GB) to ${BOLTZ_CACHE_DIR}"
    mkdir -p "${BOLTZ_CACHE_DIR}"
    BOLTZ_CACHE="${BOLTZ_CACHE_DIR}" "$(pybin boltz2)" -c "
import sys
from pathlib import Path
cache = Path(sys.argv[1])
try:
    from boltz.main import download_boltz2
except ImportError:
    sys.exit('boltz.main.download_boltz2 is gone; run one prediction by hand to populate the cache')
download_boltz2(cache)
" "${BOLTZ_CACHE_DIR}"
    [ -f "${BOLTZ_CACHE_DIR}/boltz2_conf.ckpt" ] \
      || die "the download finished but ${BOLTZ_CACHE_DIR}/boltz2_conf.ckpt is not there"
  fi

  note "boltz CLI: $(conda info --base)/envs/boltz2/bin/boltz"
  note "cache:     ${BOLTZ_CACHE_DIR}"
  note "Boltz-2 needs a target FASTA and an a3m alignment; it never fetches one for you"
}

# =============================================================================
# gnina -- a 1.9 GB release binary, not a package. GPL-2.0-or-later.
# =============================================================================
setup_gnina() {
  local bin="${BACKEND_ROOT}/bin"
  mkdir -p "${bin}"

  if [ -f "${bin}/${GNINA_ASSET}" ] \
     && echo "${GNINA_SHA256}  ${bin}/${GNINA_ASSET}" | sha256sum -c --status 2>/dev/null; then
    note "GNINA binary already present and checksum matches"
  else
    say "downloading GNINA ${GNINA_VERSION} (~1.9 GB)"
    curl -fL --retry 5 --retry-delay 3 -C - -o "${bin}/${GNINA_ASSET}" \
      "https://github.com/gnina/gnina/releases/download/v${GNINA_VERSION}/${GNINA_ASSET}"
    echo "${GNINA_SHA256}  ${bin}/${GNINA_ASSET}" | sha256sum -c \
      || die "GNINA checksum mismatch"
  fi
  chmod +x "${bin}/${GNINA_ASSET}"

  # Despite the "static" in its name, ldd reports exactly one unresolved
  # library: libcudnn.so.9. Give GNINA a cuDNN of its own rather than borrowing
  # the one some environment's torch happens to bundle. --no-deps skips the
  # cublas and nvrtc wheels: a real CNN-rescoring run produces bit-identical
  # scores without them, because GNINA links its own cublas statically.
  if [ -d "${BACKEND_ROOT}/deps/cudnn/nvidia/cudnn/lib" ]; then
    note "dedicated cuDNN already present"
  else
    say "installing a dedicated cuDNN 9 for GNINA (~1.3 GB)"
    "$(pybin prism)" -m pip install --no-deps \
      --target "${BACKEND_ROOT}/deps/cudnn" "nvidia-cudnn-cu12==9.*"
  fi

  say "writing the GNINA launcher"
  cat > "${bin}/gnina" <<'WRAPPER'
#!/usr/bin/env bash
# GNINA 1.3.3 launcher. Supplies the one library the "static" binary still needs
# (libcudnn.so.9) from a copy that belongs to GNINA alone.
# Point a cascade's `executable` at THIS file, not at the raw .static binary.
set -euo pipefail
_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for _d in "${_root}"/deps/cudnn/nvidia/*/lib; do
  [ -d "${_d}" ] && LD_LIBRARY_PATH="${_d}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
done
export LD_LIBRARY_PATH
exec "${_root}/bin/gnina.cuda12.8.static" "$@"
WRAPPER
  chmod +x "${bin}/gnina"

  note "gnina: $("${bin}/gnina" --version | head -1)"
  note "GNINA is GPL-2.0-or-later: runs need --allow-copyleft"
}

# =============================================================================
# openadmet -- OpenADMET's released hERG model, one arm of the default ADMET
# tier. Apache-2.0 by its LICENSE file; upstream's pyproject.toml still carries
# a cookiecutter MIT string, and the licence text is the one that governs.
#
# The conflict is torch again, and less negotiable than boltz2's: upstream's
# environment file resolves conda's pytorch-gpu together with chemprop,
# lightning and TabPFN, and any one of those re-pins the torch ADMET-AI
# predicts with in-process. So this gets an interpreter of its own and
# MolCascade runs the `openadmet` CLI in it by absolute path.
#
# That environment file is used verbatim rather than trimmed to an inference
# subset, because the trim is not safe: loading any model calls `to_class()`,
# which goes through `load_group("models")` and imports all nine architecture
# modules -- nepare and tabpfn included -- so a missing training dependency
# fails at model construction, well after the environment looks finished. The
# cost is worth stating plainly: ~10 GB, and four of its pip dependencies are
# git repositories, two of them at `@main`, which is a reproducibility hole
# nothing this script can do will close.
#
# Not in the default target list, for the same reason as boltz2. Ask by name.
# =============================================================================
# Fetch the CheMeleon featurizer, resuming across dropped connections.
fetch_chemeleon() {
  local dst="${HOME}/.chemprop/chemeleon_mp.pt"
  local part="${dst}.part"
  mkdir -p "${HOME}/.chemprop"

  if [ -f "${dst}" ] && [ "$(stat -c%s "${dst}")" = "${CHEMELEON_BYTES}" ]; then
    note "CheMeleon featurizer already present"
    return 0
  fi

  say "openadmet: CheMeleon featurizer (${CHEMELEON_BYTES} bytes)"
  local attempt have
  for attempt in $(seq 1 40); do
    have="$(stat -c%s "${part}" 2>/dev/null || echo 0)"
    [ "${have}" -ge "${CHEMELEON_BYTES}" ] && break
    [ "${attempt}" -gt 1 ] && note "resuming from ${have}/${CHEMELEON_BYTES}"
    # Each attempt is its own process, so -C - re-reads what is already there.
    curl -sfL -C - --connect-timeout 20 --max-time 600 -o "${part}" "${CHEMELEON_URL}" || true
    sleep 3
  done

  have="$(stat -c%s "${part}" 2>/dev/null || echo 0)"
  [ "${have}" = "${CHEMELEON_BYTES}" ] \
    || die "CheMeleon fetch stalled at ${have}/${CHEMELEON_BYTES} bytes; rerun to resume"
  echo "${CHEMELEON_SHA256}  ${part}" | sha256sum -c >/dev/null \
    || die "CheMeleon digest mismatch; delete ${part} and rerun"
  mv "${part}" "${dst}"
  note "CheMeleon featurizer verified"
}

setup_openadmet() {
  local src="${BACKEND_ROOT}/openadmet-src"
  local model="${BACKEND_ROOT}/openadmet-models/herg-chemeleon-baseline/anvil_training"

  command -v git >/dev/null 2>&1 \
    || die "git not found; openadmet's own environment file installs four dependencies straight from git"

  if [ -d "${src}/.git" ] \
     && [ "$(git -C "${src}" rev-parse HEAD 2>/dev/null)" = "${OPENADMET_COMMIT}" ]; then
    note "openadmet checkout already at ${OPENADMET_COMMIT:0:12} in ${src}"
  else
    say "fetching openadmet-models at ${OPENADMET_COMMIT:0:12}"
    mkdir -p "${src}"
    [ -d "${src}/.git" ] || git -C "${src}" init --quiet
    git -C "${src}" remote add origin https://github.com/OpenADMET/openadmet-models.git 2>/dev/null || true
    # A commit rather than a branch: `main` is where the environment file and
    # the CLI contract this adapter was written against keep moving. versioningit
    # declares default-version = "1+unknown", so a shallow fetch that carries no
    # tags reports that instead of failing the build.
    git -C "${src}" fetch --depth 1 origin "${OPENADMET_COMMIT}"
    git -C "${src}" checkout --quiet FETCH_HEAD
  fi

  if env_exists openadmet; then
    note "env 'openadmet' already exists"
  else
    local yaml="${src}/devtools/conda-envs/openadmet-models-gpu.yaml"
    [ -f "${yaml}" ] || die "upstream's environment file is not at ${yaml}"
    say "creating env 'openadmet' from upstream's own GPU environment file (~10 GB)"
    # Upstream's list is read rather than restated, so this cannot drift from the
    # file the commit above pins -- but it is installed with `conda create` and
    # --override-channels rather than handed to `conda env create`, which on
    # conda 23.7.x accepts neither -c nor --override-channels. Setting
    # CONDA_CHANNELS for that call does not substitute: channels is a sequence
    # parameter, so an environment variable *prepends* to ~/.condarc's list
    # instead of replacing it, and one observed run resolved against
    # conda-forge followed by four Tsinghua mirrors and `defaults` anyway. A
    # mirror of pkgs/msys2 answering 403 is enough to end the solve, which is
    # the same reason CONDA_CREATE carries the flag for every other environment.
    local plan; plan="$(mktemp -d)"
    "$(pybin prism)" - "${yaml}" "${plan}" <<'DEPS'
import sys
from pathlib import Path

import yaml

spec = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
conda: list[str] = []
pip: list[str] = []
for entry in spec["dependencies"]:
    # The pip section is the one mapping in an otherwise flat list.
    if isinstance(entry, dict):
        pip += [str(item) for item in (entry.get("pip") or [])]
    else:
        conda.append(str(entry))
if not conda or not pip:
    sys.exit(f"FAIL: {sys.argv[1]} did not parse into conda and pip dependencies")
out.joinpath("conda.txt").write_text("\n".join(conda) + "\n", encoding="utf-8")
out.joinpath("pip.txt").write_text("\n".join(pip) + "\n", encoding="utf-8")
print(f"    {len(conda)} conda dependencies, {len(pip)} from pip")
DEPS
    local conda_deps=()
    mapfile -t conda_deps < "${plan}/conda.txt"
    conda_create -n openadmet "${conda_deps[@]}"
    # This environment's own pip, so the wheels land beside the conda solve
    # rather than in prism. Two of these are git URLs at @main, which is why
    # nothing downstream can call this environment reproducible.
    "$(pybin openadmet)" -m pip install -r "${plan}/pip.txt"
    rm -rf "${plan}"
  fi

  # --no-deps: conda has just solved this environment, and letting pip re-resolve
  # from PyPI is how a conda-installed torch gets replaced by a wheel.
  say "openadmet: the package itself, editable, without re-resolving the solve"
  "$(pybin openadmet)" -m pip install --no-deps -e "${src}"

  # Fetched with curl over an immutable revision rather than cloned: the one
  # large file is LFS-tracked, git-lfs is not installed on every host, and a
  # clone without the filter leaves a 130-byte pointer of exactly the right name
  # in exactly the right place. A pinned digest settles it either way, and is
  # the same thing the GNINA asset above gets.
  if [ -f "${model}/model.pth" ] \
     && echo "${OPENADMET_HERG_SHA256}  ${model}/model.pth" | sha256sum -c --status 2>/dev/null; then
    note "hERG checkpoint already present and its digest matches"
  else
    say "downloading the OpenADMET hERG baseline (~50 MB) to ${model}"
    mkdir -p "${model}/recipe_components"
    for f in model.json model.pth \
             recipe_components/procedure.yaml \
             recipe_components/metadata.yaml \
             recipe_components/data.yaml; do
      curl -fL --retry 5 --retry-delay 3 -o "${model}/${f}" \
        "https://huggingface.co/openadmet/herg-chemeleon-baseline/resolve/${OPENADMET_MODEL_REV}/anvil_training/${f}"
    done
    echo "${OPENADMET_HERG_SHA256}  ${model}/model.pth" | sha256sum -c \
      || die "OpenADMET hERG checkpoint digest mismatch"
  fi

  # CheMeleon's featurizer weights, fetched here rather than left to the model
  # to fetch for itself. Upstream reaches for them with a bare urlretrieve on
  # first deserialize, which has no retry of any kind, and Zenodo has twice on
  # this machine answered 504 and twice dropped the connection around 8 MB.
  # Either one raises out of chemprop's build(), so without this the whole
  # install fails at the last step -- after the environment, the editable
  # package and the verified checkpoint have all succeeded -- or, worse, the
  # same call happens inside somebody's first screen.
  #
  # The retry loop is out here for a reason worth stating, because the obvious
  # spelling does not work: curl's own --retry re-opens the transfer but does
  # not re-read the partial file, so -C - resolves once at process start and an
  # internal retry silently restarts from byte zero. Only a fresh curl process
  # re-evaluates the resume offset, so each attempt has to be its own process
  # for the bytes already on disk to count.
  fetch_chemeleon

  # This prediction is not a formality. It is the only thing that proves the
  # environment resolved into something that can construct a model, and the
  # only place the featurizer fetched just above is proven loadable rather than
  # merely present. Left to the first screen, both failures land mid-run, which
  # is exactly what the boltz2 comment above exists to prevent.
  local accel="cpu"
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    accel="gpu"
  fi
  local scratch; scratch="$(mktemp -d)"
  printf 'SMILES\nCC(=O)Oc1ccccc1C(=O)O\nCN(C)CCCN1c2ccccc2CCc2ccccc21\n' > "${scratch}/in.csv"
  say "openadmet: one real prediction on the ${accel}, which also warms ~/.chemprop"
  "$(conda info --base)/envs/openadmet/bin/openadmet" predict \
    --input-path "${scratch}/in.csv" \
    --input-col SMILES \
    --model-dir "${model}" \
    --output-csv "${scratch}/out.csv" \
    --accelerator "${accel}" \
    || die "openadmet predict failed; the environment is installed but cannot run (inputs kept in ${scratch})"
  "$(pybin openadmet)" - "${scratch}/out.csv" <<'SMOKE'
import sys

import pandas as pd

# The column name is the model's own metadata tag joined to its target column,
# and it is what the adapter's endpoint mapping expects to find. Asserting it
# here means a re-tagged release is caught at install time rather than as an
# empty ADMET tier halfway through a screen.
column = "OADMET_PRED_chemprop-chembl_pchembl_value_mean"
frame = pd.read_csv(sys.argv[1])
if column not in frame.columns:
    sys.exit(f"FAIL: expected a {column!r} column, got {list(frame.columns)}")
values = frame[column].dropna()
if values.empty:
    sys.exit(f"FAIL: {column} is empty for every molecule")
print(f"    OK: {len(values)}/{len(frame)} predicted, pIC50 "
      f"{values.min():.2f}..{values.max():.2f}")
SMOKE
  rm -rf "${scratch}"

  note "openadmet CLI: $(conda info --base)/envs/openadmet/bin/openadmet"
  note "hERG model:    ${model}"
  note "Those are the two paths a cascade needs; export them once as"
  note "MOLCASCADE_OPENADMET_EXECUTABLE and MOLCASCADE_OPENADMET_MODEL_DIR and"
  note "every cascade on this machine finds them without naming either."
  note "The release is a no-split model: trained on everything, no held-out set,"
  note "no published metrics. It is one arm of an 'any' join for that reason."
}

# --- verification ------------------------------------------------------------
verify() {
  say "verifying"
  local py; py="$(pybin prism)"
  "${py}" -c 'import torch;print("  torch", torch.__version__, "| cuda", torch.cuda.is_available())'
  "${py}" -c 'import meeko, rdkit, admet_ai; print("  rdkit", rdkit.__version__, "| meeko + admet_ai import OK")'
  "${py}" -m molcascade doctor --allow-copyleft 2>&1 | head -30 || true
  cat <<EOF

  Isolated backends are addressed by absolute path, so 'doctor' -- which probes
  PATH -- will keep calling them unavailable. That is expected. The paths to put
  in a cascade are listed in envs/README.md.
EOF
}

# --- dispatch ----------------------------------------------------------------
targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(prism karmadock unidock aizynth gnina verify)

for t in "${targets[@]}"; do
  case "${t}" in
    prism)     setup_prism ;;
    karmadock) setup_karmadock ;;
    unidock)   setup_unidock ;;
    aizynth)   setup_aizynth ;;
    boltz2)    setup_boltz2 ;;
    openadmet) setup_openadmet ;;
    gnina)     setup_gnina ;;
    verify)    verify ;;
    *)         die "unknown target: ${t}" ;;
  esac
done

say "done"
