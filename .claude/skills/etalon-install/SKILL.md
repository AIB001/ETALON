---
name: etalon-install
description: Install ETALON and its three vendored packages (PRISM, MolCascade, MolQuarry) with every engine, model weight and external binary, into one self-contained conda prefix. Use when setting up a new machine, adding a missing backend, diagnosing a shadowed import or a "no kernel image" CUDA error, or deciding what a partial install can still do. Covers the digest-pinning constraint that forbids pip-installing the plugins, per-GPU-architecture torch pinning, and the isolated-environment layout.
---

# Installing ETALON, the way it actually goes

ETALON is a command layer over three digest-pinned source trees. Almost nothing about installing it is
about ETALON; it is about getting roughly fifty third-party backends, twelve isolated Python
environments and six model checkpoints onto one machine without breaking the pinning that makes the
numbers traceable.

This file is the sequence that worked, in order, with the four things that will otherwise cost you a day.

Verified on: 8 × RTX PRO 6000 Blackwell (sm_120), 2 × Xeon Gold 5418Y (96 threads), 2 TB RAM, system
CUDA 12.9, driver 595.84. Result: 52 GB in one prefix, `etalon doctor` green on six checks, MolCascade
43 backends available, 1888 tests passing.

## The four things that will cost you a day

### 1. Do not pip-install the three plugins

`asset/molcascade`, `asset/prism` and `asset/molquarry` are digest-pinned source trees.
`asset/MANIFEST.json` records each one's commit and a SHA-256 over the whole tree, and
`etalon.boundary.infra.load()` compares a module's *resolved path* after import and raises if it lands
outside `asset/`.

So `pip install -e asset/molcascade` breaks the install two ways at once: setuptools writes
`*.egg-info` **into** the tree, changing its digest, and it puts a second copy of the package on
`sys.path`, which `load()` then refuses.

The supported way is a `.pth` file plus hand-written console entry points:

```bash
P=/home/shizq/.conda/envs/ETALON
SP=$P/lib/python3.12/site-packages
A=/home/shizq/ETALON/asset

cat > $SP/etalon-vendored-assets.pth <<EOF
$A/molcascade/src
$A/prism
$A/molquarry/src
EOF
```

Then one small script per entry point (`prism`, `prism-builder`, `prism-generate`, `molcascade`,
`molquarry`, `molquarry-mcp`), each a `#!$P/bin/python` shebang and a two-line `main()` call.

Verify before continuing — this is the check that catches a shadowing editable install months later:

```bash
$P/bin/python -c "import prism; print(prism.__file__)"   # must be under .../ETALON/asset/prism/
cd /home/shizq/ETALON && python tools/verify_assets.py --deep
etalon_infrastructure          # the MCP tool that reports which copy would load, and whether it is pinned
```

### 2. Pin torch to the GPU architecture, once, everywhere

Blackwell (sm_120) needs binaries built with CUDA ≥ 12.8. Every upstream generative-model repo
recommends some torch 1.x / cu118 / cu124 combination, and all of them fail on such a card with
`no kernel image is available for execution on the device` — **after** the model has loaded, so the
failure looks like a model bug.

Every isolated torch environment was therefore rebuilt on **torch 2.8.0+cu128**, with PyG extensions
from the prebuilt `pt28cu128` wheels on `data.pyg.org`.

Consequence to set at environment level, not per script: torch ≥ 2.6 defaults
`torch.load(weights_only=True)`, and upstream checkpoints routinely pickle `EasyDict` and
`argparse.Namespace` config objects. Set `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` in the activation script.
This is not abandoning the check: PRISM's wrappers scan checkpoints by pickle opcode first.

Substitute your own architecture's minimum CUDA here; the shape of the problem is the same on any card
newer than the upstream recommendations.

### 3. One prefix, with the conflicting engines nested inside it

Several engines have mutually exclusive dependencies — KarmaDock needs rdkit 2022.09.1 and numpy 1.x
ABI, the main environment runs rdkit 2026.03.1 and numpy 2.4. MolCascade already supports this: it
discovers isolated engines at `<sys.prefix>/envs/<name>` and invokes them as subprocesses by absolute
path, never importing them.

```
/home/shizq/.conda/envs/ETALON/           52 GB total
├── bin/                                  python, prism, molcascade, molquarry, gmx_MMPBSA
├── envs/
│   ├── unidock/            393 MB         Uni-Dock 1.2.0, CUDA ≥ 12.8 build
│   ├── gnina/              7 KB           launcher scripts only; the binary lives in opt/
│   ├── aizynth/            765 MB
│   ├── gmxmmpbsa/          1.3 GB         needs numpy < 2, hence its own prefix
│   ├── karmadock/          4.4 GB         py3.10 + rdkit 2022.09.1 + torch 2.8.0+cu128
│   ├── boltz2/             4.4 GB
│   ├── openadmet/          7.0 GB
│   └── prism-gen-{flowr,molcraft,pocket2mol,pocketxmol,targetdiff}/   4.2–4.4 GB each
└── opt/
    ├── boltz/              5.0 GB         weights + CCD — keep mols.tar
    ├── molcascade-backends/ 2.4 GB        KarmaDock checkout, GNINA binary + cuDNN, AiZynth data
    ├── prism-models/       1.1 GB         six checkpoints + pinned upstream source
    └── molcascade-assets/   21 MB         SCScore weights, rd_filters tables
```

Deleting the prefix uninstalls everything. That is the point of the layout.

**DiffSBDD has no environment of its own** — PRISM runs it inside `prism-gen-molcraft`. Killing
`prism-gen-molcraft` processes kills DiffSBDD too, which is confusing during an incident.

### 4. Freeze conda's solve before letting pip near it

conda-forge builds the scientific stack against each other; pip will happily replace a compiled numpy
or rdkit with a wheel that does not match. Freeze first, then constrain every pip call:

```bash
$P/bin/python -m pip list --format=freeze | grep -v -E "^(pip|setuptools|wheel)==" > constraints-conda.txt
$P/bin/python -m pip install -c constraints-conda.txt <packages>
```

## The sequence

### Step 0 — mirrors, if your network needs them

Check before assuming. On this machine PyPI, conda-forge, zenodo, `files.rcsb.org` and `data.pyg.org`
were reachable directly, while **github.com, raw.githubusercontent.com and huggingface.co were not** —
only through a local proxy that dropped connections intermittently. `api.github.com` and
`codeload.github.com` *were* direct, so release assets could be fetched via the API with
`Accept: application/octet-stream`.

Mid-install, `files.pythonhosted.org` and `conda.anaconda.org` fell to 25 KB/s. Switching to regional
mirrors (here ZJU: 4.6–9.4 MB/s pip, ~2.8 MB/s conda) is what let the install finish in a day rather
than a week. Keep a separate condarc that bypasses any local proxy for conda.

### Step 1 — the main environment

One `conda create` with the whole scientific stack: `python=3.12`, `cuda-version=<yours>`,
`pytorch=*=cuda*`, `openmm-torch=*=cuda*`, ambertools, acpype, openmm, pdbfixer, openff-toolkit,
mdtraj, MDAnalysis, parmed, alchemlyb, pymbar, propka, openbabel, rdkit, numpy/scipy/pandas, pyarrow,
gemmi, biopython, scikit-learn, plus `pip` and `setuptools<81`.

Then freeze (item 4) and pip-add MolCascade's backends and the analysis/MCP extras under the
constraint file: `FPSim2`, `admet-ai`, `chembl_structure_pipeline`, `medchem`, `meeko<0.8`,
`mordredcommunity`, `onnxruntime`, `posebusters<1`, `mcp[cli]<2`, and ETALON itself editable with all
extras.

Two non-obvious additions: `lilly-medchem-rules` from conda-forge (four executables; the rules
themselves ship inside `medchem`), and `pdb2pqr==3.7.1`, which `packmol-memgen` uses for protonation
and conda does not provide.

### Step 2 — GROMACS

Check for an existing install first: `gmx --version`. An existing CUDA-enabled GROMACS 2025.4 was
accepted here rather than compiling 2026.1.

**State the cost if you accept a thread-MPI-only build**: no `gmx_mpi` means PRISM's FEP
replica-exchange mode and REST2 cannot run, because both need `mpirun + gmx_mpi`. Ordinary MD,
MM-PBSA and PMF are unaffected. Compile an MPI build later if the campaign needs replica exchange.

### Step 3 — the isolated engines

| engine | the thing that bites |
|---|---|
| Uni-Dock 1.2.0 | needs a build against CUDA ≥ your card's minimum |
| GNINA 1.3.3 | the 2 GB "static" release is not static — it lacks `libcudnn.so.9`. `pip install --no-deps --target .../deps/cudnn "nvidia-cudnn-cu12==9.*"` and write a launcher that sets `LD_LIBRARY_PATH`. GPL-2.0, so runs require `--allow-copyleft` |
| KarmaDock | py3.10 + rdkit 2022.09.1 + numpy 1.26.4 (rdkit 2022's C extensions are numpy 1.x ABI) + torch 2.8.0+cu128 + PyG `pt28cu128`. Weights ship in the repo; no Zenodo download needed |
| AiZynthFinder 4.4.1 | 754 MB of data via its own `download_public_data` (figshare, may need the proxy) |
| Boltz-2 | `boltz[cuda]==2.2.1` alone resolves a CUDA-13 torch. Pin `torch==2.8.0` **and** `cuequivariance==0.10.0` — 0.12's kernels need torch ≥ 2.11. **Keep `mols.tar`** or every prediction re-downloads 1.9 GB |
| OpenADMET | build from upstream's `openadmet-models-gpu.yaml`; the hERG model comes from HF at a fixed revision with a SHA-256 check, and CheMeleon needs a separate 34,859,448-byte `chemeleon_mp.pt` |
| gmx_MMPBSA 1.7.0 | requires numpy < 2, so its own prefix plus a wrapper on the main `bin/` so PRISM's generated `mmpbsa_run.sh` finds it on PATH |

### Step 4 — the generative model checkpoints

966 MiB total, each verified by SHA-256 after download:

| model | file | size |
|---|---|---|
| TargetDiff | `pretrained_diffusion.pt` | 33 MB |
| Pocket2Mol | `pretrained_Pocket2Mol.pt` | 45 MB |
| MolCRAFT | `molcraft.ckpt` | 45 MB |
| PocketXMol | `pocketxmol.ckpt` + `train.yml` | 243 MB |
| FLOWR | `flowr_noHs.ckpt` | 629 MB |
| DiffSBDD | `crossdocked_fullatom_cond.ckpt` | 18 MB |

Upstream source is shallow-cloned at the commit pinned in the manifest. The machine-specific
`generation.local.yaml` goes in `opt/prism-models/`: absolute environment prefixes, and batch sizes
raised for large-memory cards.

### Step 5 — activation, so nothing depends on a shell's history

`etc/conda/activate.d/zz-etalon.sh` exports `ETALON_HOME`, `MOLCASCADE_BACKEND_ROOT`,
`MOLCASCADE_ASSET_ROOT`, the `MOLCASCADE_*_EXECUTABLE` paths, `PRISM_MODELS_DIR`,
`PRISM_GENERATION_CONFIG`, `BOLTZ_CACHE`, `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`, and
`NUMEXPR_MAX_THREADS=$(nproc)`; it sources `GMXRC` when `gmx` is absent. A matching `deactivate.d`
script restores all of it.

That `NUMEXPR_MAX_THREADS` line is not cosmetic: on a 96-thread machine numexpr prints a line reading
`Error.` **to stdout**, which corrupts `etalon doctor`'s JSON and, under MCP stdio transport, the
protocol stream.

## Local adaptations this machine needed

| change | why |
|---|---|
| symlink `site-packages/rdkit/Contrib` → `share/RDKit/Contrib` | conda's RDKit puts Contrib outside the package; MolQuarry imports `rdkit.Contrib.SA_Score` per the pip layout |
| move `site-packages/examples/` aside | conda-forge's `python-constraint` (pulled in by openff-toolkit) installs its samples as a **top-level** `examples` package, shadowing ETALON's own |
| symlink `~/molcascade-backends` and `~/.boltz` into `opt/` | so default paths resolve with the environment deactivated |

## Verifying

```bash
python -m etalon doctor --prism-python $CONDA_PREFIX/bin/python
molcascade doctor --allow-copyleft            # per-backend availability
etalon_infrastructure                         # which copy loads, and whether it is pinned
python -m pytest tests/ -q
```

Reference result: doctor six-for-six with 53 plugins; MolCascade 43 available / 14 unavailable / 2
degraded — **the 14 are catalogue entries with no adapter yet, not failures**; 1888 tests passing, 1
skipped (that GROMACS build lacks the amber14sb force field).

## Known problems, in the order they will bite

1. **PRISM's generated run scripts use `-pme gpu -bonded gpu` for energy minimisation**, which GROMACS
   refuses for `integrator = steep` (2025.4 and 2026.1 alike). EM therefore fails on any GPU machine and
   every later step fails with it — while the script still prints completion. Work around it by running
   EM manually with `gmx mdrun ... -nb gpu` and re-running the script, which skips finished stages.
2. **A relative `--workspace` for MolCascade is self-contradictory**: it derives a relative receptor path
   and its own `TargetConfig` rejects relative paths. Always pass absolute paths.
3. **MolCascade writes its error envelope to stderr**, so a `--json` output file may not contain the
   error code. Automation must read both streams. (ETALON's adapter returns failures as data and
   classifies exhaustion, so code driving `boundary.screen` does not face this.)
4. **Zero survivors exits non-zero.** A run where every molecule was gated out raises from whichever stage
   first finds nothing left — three different codes were observed in one campaign. It is a measurement,
   not a fault; all scores are committed. `ScreenResult.outcome` returns `"exhausted"` for it.
5. **PRISM's DiffSBDD wrapper omits `argparse.Namespace`** from its runtime allowlist while its own static
   checkpoint scan permits it, so torch ≥ 2 refuses the official checkpoint. Worked around with a `.pth`
   shim in that one environment, permitting only that class; remove it when the plugin is fixed.
6. **KarmaDock requires a mol2 reference ligand.** An SDF is rejected by a suffix check before the message
   about automatic conversion applies. Convert once with `obabel`.

## Reclaiming space

About 25 GB of caches after a full install: `~/.conda/pkgs*`, `~/.cache/pip`, and any `wheels/`
directories under the build tree. The 52 GB prefix itself is the installation.
