# MolCascade

MolCascade is a local-first Python package for building and running modular,
hierarchical molecular-screening cascades. It starts from an existing molecular library,
applies explicit and replaceable screening components, and produces an auditable shortlist.

MolCascade does not generate molecules, and it does not run MD or free-energy
calculations. Those are deliberate handoff boundaries for downstream software such as
PRISM. Structure-based docking is available as an optional tier — GPU engines that score
against a receptor you supply — and its scores travel with the shortlist as evidence, not
as a claim of binding.

> Status: early alpha (`0.1.0a0`). Contracts are versioned, but public compatibility is not
> guaranteed before the first stable release.

## Installing

Python 3.11 or newer is required.

```bash
git clone https://github.com/AIB001/MolCascade.git
cd MolCascade
python -m pip install .
```

That installs MolCascade and the six packages it cannot run without. They are declared in
`pyproject.toml` and pip resolves them for you; the table is here so you know what is being
pulled in and why, not because you have to install them by hand.

| Package | Why it is mandatory |
| --- | --- |
| `rdkit>=2024.9` | The chemistry engine. Parsing, standardization and identity registration, plus every default block: structural validity, the physicochemical window, Rule of Five and QED, PAINS/BRENK/NIH/ZINC alerts, Morgan fingerprints, Murcko scaffolds, SA Score and the diversity picker. Nothing screens without it. |
| `numpy>=1.26` | Descriptor arithmetic, and the SCScore backend, which is evaluated in numpy rather than a framework so that a synthesizability score costs no extra install. |
| `pyarrow>=17,<26` | The artifact format every stage reads and writes, and the Parquet library reader. |
| `pydantic>=2.10,<3` | Configuration and contract schemas. Unknown fields and duplicate keys are rejected here rather than surfacing mid-run. |
| `PyYAML>=6,<7` | YAML configurations. |
| `openpyxl>=3.1,<4` | The streaming XLSX reader, for libraries that arrive as a spreadsheet. |

RDKit is the one worth installing deliberately. `pip install rdkit` works on all three
platforms, but if you already keep a conda environment for chemistry, install it there first
and let pip find it:

```bash
conda create -n molcascade python=3.11
conda activate molcascade
conda install -c conda-forge rdkit
python -m pip install .
```

### Screening tools installed per criterion

Everything below is optional and none of it is installed by the command above. Each one
backs a specific block in the builder, and one extra covers one criterion, named after the
criterion rather than after the package — you pick a *filter* in the HTML, and the extra is
what turns that choice into an install.

| Builder block | Install | Package | What it adds |
| --- | --- | --- | --- |
| PAINS and reactivity alerts · Rule of Five and QED · ADMET property rules | `pip install "molcascade[alerts]"` | `medchem>=2.0` | 22 named published rule sets and about 2,400 curated SMARTS across 23 alert collections, including the NIBR rules |
| ADMET endpoint | `pip install "molcascade[admet]"` | `admet-ai>=2.0` | Local multi-endpoint ADMET prediction. Brings `chemprop`, `lightning` and `torch` with it — around 900 MB, most of it torch |
| Physicochemical window (Mordred option) | `pip install "molcascade[descriptors]"` | `mordredcommunity>=2.0` | 1,613 2D descriptors, for windows RDKit does not compute |
| Structural validity (ChEMBL option) | `pip install "molcascade[standardize]"` | `chembl_structure_pipeline>=1.2` | EBI's curation checker on its published 0–9 penalty scale — a second toolkit's opinion, not RDKit's |
| Similarity to reference leads (indexed option) | `pip install "molcascade[similarity]"` | `FPSim2>=0.7` | Popcount-bounded nearest-reference search; worth switching to above roughly 1,000 references |
| Custom model prediction | `pip install "molcascade[custom-model]"` | `onnxruntime>=1.17` | Runs your own model from an ONNX bundle. ONNX is a data format, so nothing in the bundle executes as Python |
| Docking score (Uni-Dock option) | `pip install "molcascade[docking]"` | `meeko>=0.7`, `pdbfixer>=1.9`, `gemmi` | Meeko turns a conformer — and the receptor, once — into the PDBQT Uni-Dock reads. PDBFixer repairs that receptor first: solvent and co-crystallised matter out, unresolved side chains rebuilt, metals kept, unresolved loops left alone and named in the run notes. The engines themselves are not pip packages; see [Docking engines](#docking-engines-live-in-their-own-environments). `gemmi` is meeko's own undeclared import, named here because installing meeko without it produces a package that imports fine to `doctor` and raises on first use |

`pip install "molcascade[all-backends]"` installs all seven at once, which is the right choice
for a machine that has to run whatever a user assembles in the builder. The quotes matter:
an unquoted bracket is a glob in most shells.

You do not have to work this table out in advance. A cascade that names a tool you have not
installed is refused **before the run reads its first molecule**, and the error names every
missing package at once together with the single `pip install` that fixes all of them —
see [Missing tools and weights](#missing-tools-and-weights).

### Open Babel

Open Babel is *not* in any extra, and this is deliberate rather than an oversight. Two
backends are catalogued against it — format rescue and its descriptor set — but Open Babel
is GPL-2.0, and the default deployment policy refuses copyleft backends:

```
$ molcascade doctor
  blocked     bitbirch                       — deployment policy blocks copyleft backends
  blocked     openbabel.descriptors          — deployment policy blocks copyleft backends
  blocked     openbabel.rescue               — deployment policy blocks copyleft backends
```

Nothing in the shipped cascade needs it: RDKit reads every format the library readers accept,
and the descriptor criterion is served by RDKit and Mordred. If your project's licensing
allows GPL dependencies, `pip install openbabel-wheel` and relax the policy; if it does not,
you lose nothing by leaving it out.

### Docking engines live in their own environments

The docking tier runs three GPU engines, and none of them is a package this environment can
hold. Uni-Dock and GNINA are compiled CUDA binaries; KarmaDock pins `rdkit==2022.09.1`
against this project's `rdkit>=2024.9`, so it needs an interpreter of its own. That is what
makes them *isolated* backends: MolCascade never imports them, it runs them as subprocesses
and records the digest of everything it handed over.

So they are installed the way their own projects document, and the cascade carries the
absolute path to what it should run — the same shape AiZynthFinder already uses:

| Engine | Licence | What goes in `executable` | Weights |
| --- | --- | --- | --- |
| [Uni-Dock](https://github.com/dptech-corp/Uni-Dock) | Apache-2.0 | the `unidock` binary | none — its scoring functions are physics |
| [KarmaDock](https://github.com/schrojunzhang/KarmaDock) | Apache-2.0 | the `python` inside its environment, plus `repo_path` | committed to the checkout as `trained_models/karmadock_screening.pkl`, so there is nothing to fetch: `virtual_screening.py` loads them from that fixed path with no flag to redirect it |
| [GNINA 1.3](https://github.com/gnina/gnina) | GPL-2.0-or-later | the `gnina` binary | compiled into the binary |

GNINA is copyleft only because it links Open Babel, and the default deployment policy
refuses copyleft backends — so it is blocked until a run says otherwise:

```bash
molcascade screen --config cascade.json --library molecules.csv --allow-copyleft
```

The flag is recorded in the audit log and in the exported provenance, because a screening
campaign has to be able to state in its methods section which licences it accepted.

`molcascade doctor` reports all three the same way it reports everything else, and
`--allow-copyleft` works there too — which is how you tell a licence refusal apart from an
engine that is simply not installed yet:

```
$ molcascade doctor
  blocked     gnina                — deployment policy blocks copyleft backends
  unavailable unidock              — local command is not installed: unidock

$ molcascade doctor --allow-copyleft
  unavailable gnina                — local command is not installed: gnina
```

### Setting an engine's path once per machine

Where an engine is installed is a fact about the machine, not about the campaign. Writing it
into every `cascade.json` makes the file non-portable — the person you hand it to has to edit
it before it will start — and makes you retype the same two paths into every config the
builder generates. So a cascade may leave every installation path blank, and three things
answer in turn, in this order:

1. **the cascade**, if it names one;
2. **`MOLCASCADE_<ENGINE>_<FIELD>`**, exported once in your shell profile;
3. **where `envs/bootstrap.sh` installs** — a sibling conda environment named after the
   engine, `~/molcascade-backends`, `~/aizynth-data`. Nothing to configure: an operator who
   ran the bootstrap script has already said where things are by putting them there.

Step 3 looks in `$MOLCASCADE_CONDA_ENVS` if you set it (a `:`-separated list that *replaces*
the guesses — for a `--prefix` tree, a mamba root or a venv), otherwise in `$CONDA_ENVS_DIRS`,
beside this environment and below it. `MOLCASCADE_BACKEND_ROOT` and `MOLCASCADE_AIZYNTH_DATA`
move the other two trees, and the bootstrap script reads the same variables with the same
defaults, so the two cannot drift apart.

The variables for step 2:

| Variable | Answers |
| --- | --- |
| `MOLCASCADE_UNIDOCK_EXECUTABLE` | the `unidock` binary |
| `MOLCASCADE_KARMADOCK_EXECUTABLE` | the `python` inside KarmaDock's environment |
| `MOLCASCADE_KARMADOCK_REPO_PATH` | the KarmaDock checkout |
| `MOLCASCADE_GNINA_EXECUTABLE` | the `gnina` binary |
| `MOLCASCADE_AIZYNTHFINDER_EXECUTABLE` | the `aizynthcli` inside AiZynthFinder's environment |
| `MOLCASCADE_AIZYNTHFINDER_CONFIG_PATH` | the AiZynthFinder YAML naming your policy and stock |

A path written in the cascade always wins, so a config that names its own is never silently
redirected by the machine it lands on, and a host with two KarmaDocks gets the one it named. A
path that came from a variable or from the search is validated, hashed into the stage's
identity and recorded in the provenance exactly as a typed-in one would be — nothing becomes
less reproducible for having been found rather than typed. What is found is always a specific
absolute path this project's own installer writes, never a `PATH` search: an isolated engine's
`bin` is deliberately not on this environment's `PATH`, so resolving a bare command name there
would find the wrong installation or none, and that stays refused.

Only *machine* paths work this way. The receptor, the reference ligand and your lead set are
the questions a campaign is asking, so they stay in the file that describes it; a variable
that supplied a receptor would let a cascade naming no protein run against whatever the last
project left exported.

**Nothing is decided when the cascade is written.** The builder runs on a laptop and the
cascade runs on the box with the cards, so the page never bakes in one machine's answer: an
installation path is exported blank, marked *found when the run starts*, and settled on the
host that can actually look — once, before the first molecule is read. A gap there stops the
run immediately, names every gap at once, says where it looked, and gives you both ways out:

```
1 installation path(s) this run needs are not resolved on this machine:
  docking_score: the Uni-Dock binary was not found on this machine, and the cascade does
      not name it ('executable' is blank). Looked in: /opt/conda/envs/unidock/bin/unidock,
      /home/you/molcascade-backends/bin/unidock.
      answer it once for this machine: export MOLCASCADE_UNIDOCK_EXECUTABLE=/path/to/it
hint: Not installed yet? Run: bash envs/bootstrap.sh unidock. …
```

A path that is present but wrong is reported the same way and names where it came from:

```
nothing exists at the configured 'executable': /opt/envs/aizynth/bin/aizynthcli
  (from MOLCASCADE_AIZYNTHFINDER_EXECUTABLE)
```

The other two kinds of missing tool are checked at the same moment, coarsest first: engines,
then model weights that are not in the asset root (`molcascade assets fetch …`), then Python
packages that are not importable in this environment (`pip install …`). Each names every gap it
finds, so you install one class of thing per attempt rather than one file per attempt. See
[Missing tools and weights](#missing-tools-and-weights).

### torch and the GPU

The ADMET extra brings a CUDA-enabled `torch` on its own — a plain `pip install` of it
resolved `torch 2.13.0+cu130` and saw the card without any `--index-url` argument. If you
need a specific CUDA minor, install it first and pip will keep it:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cu124
python -m pip install "molcascade[admet]"
```

`molcascade doctor` prints what it found, including whether torch sees a GPU.

### A worked, pinned installation

[`envs/README.md`](envs/README.md) records one machine's complete provisioning — exact
versions for all four environments, the GNINA binary's checksum and its one non-static
library, the absolute paths each isolated criterion wants, and `envs/bootstrap.sh` to
reproduce the whole thing on a new host.

## Quick start

```bash
molcascade generate config --output molcascade-builder.html
```

Open `molcascade-builder.html` in a modern browser. The builder is one offline file: it
loads no CDN resources, sends no network requests, and does not execute screening code.

You assemble a cascade by dragging blocks from the left tray into tiers. Each tier is one
level of the funnel and can hold several blocks, combined in series or put to a vote. Every
block names both the *question* it answers and the *tool* answering it, so swapping RDKit
for `medchem`, `rd_filters`, or your own model is a change of block, not a rebuild. Tools
whose package or weights are not installed here are shown greyed out, with the exact command
that would provide them.

A page opened from a file cannot choose where its download goes, so `cascade.json` lands in
the browser's downloads folder and has to be moved next to the library every time. Add
`--serve` to skip that: the same offline HTML is still written to `--output`, and a loopback
server hands out a second copy of it whose **Download config** button writes `cascade.json`
into the same directory as the HTML.

```bash
molcascade generate config --output ./molcascade-builder.html --serve
# Serving it at http://127.0.0.1:PORT/ — Ctrl-C when you are done.
```

The server answers 127.0.0.1 only, accepts one request the page it served can make (a token
it never writes to disk), refuses anything that is not a cascade file before writing a byte,
and always writes to the one path named on the command line. The HTML it leaves behind
carries no token and no endpoint — mail it to a colleague and they get the offline builder.

Download the configuration from the builder, then screen a library with it:

```bash
molcascade validate cascade.json
molcascade screen --config cascade.json --library molecules.csv \
  --workspace .molcascade --run-id screen-001
```

A configuration is a reusable screening *policy*; the library is supplied per run, so one
cascade can screen many generated batches without being edited. The run prints its funnel —
what each tier removed, and whether the shortlist reached the size the cascade was
configured for.

### A cascade with docking needs a receptor at run time

The cascade is a screening policy and is meant to be reused against the next target, so the
receptor is not in it. Name it on the run, together with one statement of *where* on the
protein to dock:

```bash
molcascade screen --config cascade.json --library molecules.csv \
  --workspace .molcascade --run-id screen-002 \
  --receptor stk17b.pdb --reference-ligand cocrystal.sdf
```

`--reference-ligand` (mol2/sdf/mol) takes the ligand's extent plus 4 Å of padding as the
search box, and the derived numbers are written into the run's provenance so the campaign
stays reproducible without the ligand file. `--pocket pocket.pdb` and
`--box cx,cy,cz,sx,sy,sz` are the other two ways to say the same thing. Name the site once:
two site files is a disagreement rather than a belt and braces, and a box given alongside a
file has to contain what that file points at — Uni-Dock and GNINA read the box while
KarmaDock reads the file, so a box the file's atoms sit outside of means the engines are
docking into different places, and the run stops instead. `--receptor-pdbqt` is the escape
hatch for a structure the automatic meeko preparation cannot cope with.

A run whose cascade holds a docking tier and whose command line holds no receptor stops
before the first molecule is read, naming the flags that would fix it. The receptor is read,
repaired, and hashed — and every repair is named in the run's target notes. Missing
side-chain atoms are rebuilt from rotamer templates, waters and co-crystallised matter are
dropped, and modified residues such as selenomethionine are converted back; metals are kept.
What it will not do is guess. A residue absent from the model entirely — an unresolved loop —
is reported and left absent rather than built from a template, and protonation states and
tautomers are never assigned, because those decide the science and a screening run has no
business inventing them. `--no-receptor-prepare`, `--receptor-keep-waters` and
`--receptor-keep-heterogens` turn the individual decisions off.

`molcascade validate` is the other side of that: a docking cascade with no receptor in it is
*valid*, because that is the shape it is supposed to have, and validating it says so in one
line rather than failing.

```
This cascade names no target; its docking stages were checked without one.
hint: Pass '--receptor RECEPTOR.pdb' together with one binding-site definition: …
```

Which is the same courtesy the library already gets — neither the protein nor the molecules
belong to the policy, so a file that names neither is finished, not broken.

### The docking scores come out beside the shortlist

`molcascade export` writes one portable file, and a `.smi` has nowhere to put a scores
table. So a run that docked gets a sibling directory:

```
shortlist.smi              the shortlist itself, unchanged
shortlist-docking/
  dock.unidock.parquet     docking_score/v1, verbatim, for that stage
  dock.karmadock.parquet
  best-pose.parquet        one row per molecule per stage, at pose_rank 0
```

One file per docking *stage* rather than per engine, because a cascade may run the same
engine twice — two boxes, two scoring functions — and the `engine_id` column inside each
file still says which engine it was. Only shortlisted molecules are carried: a molecule
docked in one tier and dropped by a later one has scores in the run's artifacts and the run
report, but not here, so the bundle and the file beside it always agree on `parent_id`.

`best-pose.parquet` is long, not pivoted — adding an engine adds rows, not columns — and it
carries `direction` per row because a Vina score is stronger when lower and KarmaDock's MDN
score is stronger when higher. Pose geometry stays in the per-stage file; join back on
`(parent_id, engine_id, receptor_id)` at `pose_rank = 0`. A run with no docking tier writes
no directory at all, and `shortlist_export/v1` is unchanged either way.

### Using every core, and every card

Screening is one molecule at a time by nature, so the work splits into contiguous shards
that run in parallel and resume independently:

```bash
molcascade screen --config cascade.json --library molecules.csv \
  --workspace .molcascade --run-id screen-002 --workers 8 --device auto
```

`--device auto` is the default: with usable NVIDIA cards it gives the GPU stages one lane
per card, and without them it falls back to CPU **and records why**, so an eight-hour CPU
run is never something you discover afterwards. `--device cuda` refuses to start rather than
falling back. Shard count follows the library, not the machine, so the same cascade produces
the same artifacts on a laptop and on a GPU node — and an interrupted stage resumes from the
last completed shard rather than from zero.

Inspect or resume the durable run and materialize its outputs:

```bash
molcascade status screen-001 --workspace .molcascade --events
molcascade screen --config cascade.json --library molecules.csv \
  --workspace .molcascade --run-id screen-001 --resume
molcascade report screen-001 --workspace .molcascade --output screen-001.html
molcascade export screen-001 --workspace .molcascade --output shortlist
```

### Missing tools and weights

A cascade is a *policy*, and it is normal to author one on a laptop and run it somewhere
else. So a cascade may well name a tool the current machine does not have. Two things can be
missing, and both are checked together, before the run reads its first molecule:

**A Python package is not installed.** The run stops immediately, names every stage that
wanted it, and prints the one command that fixes all of them at once:

```
$ molcascade screen --config cascade.json --library molecules.csv
molcascade: error [BACKEND_PREFLIGHT_FAILED]: 2 stage(s) need a backend that is not usable
on this machine:
  druglike_oral_consensus (medchem published rule sets): Python module is not installed: medchem
  alerts_medchem (medchem alert collections and NIBR rules): Python module is not installed: medchem
hint: pip install "molcascade[alerts]"; MolCascade never installs anything during a screening
run, so install these first, then start the run again. Removing the block from the cascade is
the other way out.
```

The package goes wherever your environment puts packages — site-packages, or conda's
`lib/python3.11/site-packages`. MolCascade does not manage it and does not need to: once
it is installed there, every later run imports it and nothing is downloaded again.

**A weight file or rule table is missing.** These are not shipped in the package — they are
large and carry their own licences — so they are fetched once into a folder MolCascade owns
and verified against a pinned SHA-256:

```bash
molcascade assets status          # what is needed, what is present
molcascade assets fetch --all     # download and verify everything missing
```

That folder is `vendor/` when you are running from a clone, the platform user-data directory
when you installed a wheel (`%LOCALAPPDATA%\molcascade\assets` on Windows,
`~/.local/share/molcascade/assets` elsewhere), and whatever you point `MOLCASCADE_ASSET_ROOT`
at if you want the weights on a shared volume. Fetching writes a `.molcascade-verified.json`
stamp recording each file's size, mtime and digest, so a later run confirms the tree without
re-hashing several gigabytes — and never re-downloads.

Downloads happen only in `molcascade assets fetch`, never in a screening run: no stage in the
pipeline can even import the module that reaches the network. `molcascade doctor` reports the
same readiness for backends, weights and hardware without running anything, and `molcascade
validate` reports both without failing, so you can check a cascade on the laptop it will not
run on.

### Retrosynthesis: a tool that must not be installed here

One backend is the exception to "pip install it and it works". AiZynthFinder pins RDKit back
to 2023.09 and NumPy back to 1.26; resolving it into this environment would change every
descriptor, every fingerprint, every policy digest and every ADMET checkpoint in the cascade
so that its last tier could run. So it goes in an environment of its own, and MolCascade
talks to it through `aizynthcli`:

```bash
conda create -n aizynth "python>=3.10,<3.13"
conda run -n aizynth python -m pip install aizynthfinder
conda run -n aizynth download_public_data ~/aizynth-data   # policy + stock, ~1 GB
conda run -n aizynth which aizynthcli                      # the path the stage needs
```

The **Retrosynthesis route search** block then asks for two absolute paths: that `aizynthcli`,
and the `config.yml` that `download_public_data` wrote. Nothing is resolved on `PATH` — the
whole point is that this environment cannot see that one — and both paths are checked before
the first molecule is read, so a wrong one costs a second rather than a tier. Both are
per-machine, so both can come from the environment instead of the cascade: see [setting an
engine's path once per machine](#setting-an-engines-path-once-per-machine).

Two things are worth knowing before you enable it. Tree search is **seconds to minutes per
molecule**, so it belongs at the very bottom of the funnel; the stage refuses more than
`max_molecules` (1,000 by default) rather than quietly starting a week of compute. And the
score is a step count, so a molecule with no route to your stock cannot simply be left
blank — it is recorded as a large sentinel, flagged `ROUTE_NOT_FOUND`, and dropped by the
step threshold rather than ranked as if it were easy.

### Authoring a flat pipeline directly

`molcascade generate config --pipeline` emits the older node-and-port graph editor, which
writes a schema-1 pipeline instead of a cascade. It exposes stage wiring the tier builder
deliberately hides — explicit port-to-port connections, side evidence channels, and manual
policy joins — and is the right surface when you need a graph the funnel shape cannot
express. `molcascade run` executes what it produces.

## Input libraries

`molcascade screen --library` supplies the molecules for one run, replacing any source path
stored in the configuration. (`molcascade run` spells the same thing `--molecules`, since a
flat pipeline names its source stage explicitly.) Automatic format detection supports:

| Input | CLI format | Large-library behavior |
|---|---|---|
| CSV, TSV, or `.smi` | `delimited` | streamed in bounded batches |
| XLSX | `xlsx` | read-only worksheet streaming |
| SDF | `sdf` | supplier-based iteration |
| Raw molecule Parquet | `parquet` | Arrow row-group batches |
| A directory containing many MOL2 files | `mol2-directory` | disk-backed SQLite inventory; files opened one at a time |

Pass `--format` when a path is ambiguous. Multi-million-file MOL2 collections
should live on a fast local filesystem; the inventory avoids holding every pathname or
molecule in Python memory, but filesystem traversal and structure parsing remain real
costs.

### Reading a library laid out for a human

A configuration is a reusable screening policy; the library changes every run, and so does
its layout. These flags describe the file without editing the configuration:

| Flag | Use |
|---|---|
| `--smiles-column` | Name of the structure column. Matched ignoring case, so `SMILES` and `smiles` both resolve; two spellings in one header are refused rather than guessed. |
| `--id-column` | Column (or SD property) naming each molecule. Carried through to the exported shortlist. |
| `--sheet` | Worksheet to read. Without it the first *visible* sheet is used, which in a curated workbook is often a README. |
| `--skip-rows` | Leading rows to discard before the header, for exports that open with a title or a merged section banner. |

```bash
molcascade screen --config cascade.json --library binders.xlsx \
  --sheet 2_Master_Compounds --skip-rows 1 --id-column Compound_Name \
  --workspace .molcascade
```

`--id-column` is what makes the shortlist joinable back to the library it came from. When
it is supplied, `molcascade export` adds a `source_id` column to the SMILES output and a
`MOLCASCADE_SOURCE_ID` property to the SDF output; without it the output is unchanged.
Standardization deduplicates before export, so a structure that appeared under several
names is exported once, under one of them.

## Screening model

Plugins exchange immutable, typed Arrow/Parquet datasets. They never exchange pickled
toolkit objects. Every stage has a pinned plugin identity, exact input/output contracts,
validated configuration, determinism declaration, and content-addressed artifact record.

The shipped default cascade demonstrates:

1. streamed ingestion;
2. parent registration, normalization, and exact deduplication;
3. parallel chemistry gates for structural validity, physicochemical ranges, Rule of Five
   and QED, plus PAINS/BRENK/NIH/ZINC alerts;
4. an executable **ALL-required** policy join;
5. physicochemical descriptors and molecular fingerprints;
6. RDKit synthetic-accessibility evidence followed by an explicit, configurable score
   threshold gate, joined by a reaction-trained SCScore arm once its weights are fetched;
7. Murcko scaffolds and diversity groups;
8. deterministic scaffold-aware budget selection; and
9. a verified SMILES shortlist handoff.

Prediction and synthesis modules intentionally separate evidence from policy. For example,
ADMET-AI emits endpoint/model prediction rows; a prediction threshold gate turns one
reviewed endpoint into a complete PASS/REJECT decision stream. Missing evidence is rejected,
not silently accepted. The same pattern applies to synthetic-accessibility scores.

### Ring topology, for libraries a model drew

Property windows measure how heavy a molecule is, which on a generated library turns out to
say almost nothing about what shape it is. Structure generators produce fused polycyclic
sheets -- seven or eight rings in one system, no rotatable bond anywhere -- and those
molecules pass Lipinski, score well on QED, raise no PAINS match, clear every ADMET
endpoint, and then dock *better* than the flexible molecules beside them, because a rigid
surface makes many contacts and pays no conformational entropy for them. Over one
2,000-molecule run here, molecules with four or more fused rings were 71.5% of the input,
48.7% after the synthesis tier, and 83.3% of the final shortlist: the funnel does not
select against the shape, docking selects for it.

The **Ring topology and rigidity** criterion is the optional block for this, and it sits in
the physicochemical tier so it runs before anything expensive. It exists because the
obvious existing rules do not do what their names suggest: `medchem`'s
`N_FUSED_AROMATIC_RINGS_TOGETHER` and Mordred's `nFRing` both count fused *systems*, so
pentacene scores 1 and passes any cap you can set. This gate counts the rings *inside* the
largest system, ignoring aromaticity, and reports seven other numbers beside it.

Three bounds ship on, each taking a published value rather than a preference: `max_rings`
6 and `max_ring_system_size` 18 atoms are FAF-Drugs4's own drug-like thresholds, calibrated
by its authors so that up to 90% of 916 approved oral drugs pass, and `max_fused_rings` 4
is one step looser than Toxtree's three-fused-aromatic-ring alert, which is what lets the
steroids through. Together they leave 30 of 33 approved drugs standing, costing morphine,
camptothecin and artemisinin.

The other five -- largest single ring, a rotatable-bond floor, ring-atom fraction, sp3
fraction and bridgehead count -- ship empty, and that is the finding rather than an
omission. Each is a reasonable thing to want on a generated library and none has a
threshold the literature supports against real drugs: a floor of three rotors would reject
caffeine, estradiol and olanzapine, which have none; a 0.75 ring-atom ceiling would reject
olanzapine at 0.91; and Lovering's sp3 figures are cohort means, so a 0.25 floor rejects
imatinib at 0.24. They are configurable because on a model-generated library they are
exactly the right knobs, and they are off because a default has to survive the drugs that
already work. Every bound is nullable, every PASS decision carries all eight measured
values into the trace CSV, and `tests/plugins/test_ring_topology_gate.py` pins the drug
panel so that moving a default names the drugs it costs.

### Parallel and Serial criteria

Several criteria may occupy one tier.

- **Parallel (ALL)** gives every criterion the same parent population. Their complete
  decision streams converge at one policy-join node, and a parent survives only when all
  required criteria pass.
- **Serial** sends the survivors of one criterion into the next criterion in that tier.

These modes compile to materially different DAGs and different pipeline revision IDs.
Changing the canvas layout alone is metadata; changing a connection, tool, threshold, or
mode changes the executable revision.

## Local tools and scientific scope

MolCascade currently includes executable adapters for native Python/Arrow components,
RDKit chemistry, the ChEMBL Structure Pipeline, Mordred descriptors, Lilly Medchem Rules
and rd_filters alert sets, SCScore synthetic complexity, FPSim2 similarity search, optional
Open Babel descriptors, an optional ADMET-AI v2 endpoint panel, three GPU docking engines
run out of process, and two bring-your-own-model paths. The builder and `molcascade doctor`
distinguish three states:

- **available executable adapter** — registered and runnable under current local policy;
- **optional local adapter** — implemented but unavailable until its dependency or trusted
  assets are present;
- **research option only** — reviewed software with no MolCascade adapter yet; visible for
  planning but impossible to export as an executable node.

```bash
molcascade plugins
molcascade doctor
molcascade doctor --run-version-commands
```

The backend catalogue covers alternatives such as the ChEMBL Structure Pipeline, Open
Babel, Lilly Medchem Rules, MordredCommunity, Chemprop, ADMET-AI, FPSim2, RAscore,
AiZynthFinder, scaffold tooling, and multi-objective selection libraries. `molcascade
doctor` reports which of them can run on this machine and what each missing one needs;
`molcascade cite` prints the whole catalogue with the paper behind every entry.

There is no universal “SOTA” tool, threshold, or screening order across targets. Rule of
Five, QED, structural alerts, ADMET models, similarity, SA score, and retrosynthesis answer
different questions and have different domains. The default synthesizability cutoffs are
transparent starter policy, not evidence of route feasibility: SA score counts how common a
molecule's fragments are and SCScore learns complexity from reaction records, so they
disagree by construction and the default drops a molecule only when both call it hard.
Neither is a route or a step count. PAINS matches are triage signals, not experimental proof. Endpoint direction, units, calibration, applicability, and project
costs must be reviewed before a model score becomes a rejection rule.

A docking score is the same kind of evidence, and no stronger. Two engines agreeing is
worth more than either engine's own number, which is why the docking tier defaults to a
consensus vote rather than to one tool — but a pose is a hypothesis about geometry and a
score is a ranking, not a measured affinity. The scores travel with the shortlist so that
whatever comes next, a binding model or an assay, starts from a ranked and auditable set.

The ADMET-AI adapter is deliberately strict: it does not download models or install
packages. Before enabling it, inspect and pin both the installed package code and complete
model tree, review the configured endpoints, and explicitly acknowledge trusted local
Torch checkpoint deserialization — a checkpoint runs code when it loads, and pinning its
digest records which bytes ran rather than making running them safe.

### Your own model

A model you trained on your own data can be one tier of a cascade. Put it in a directory
with a manifest, take its digest, and select it in the builder like any other engine:

```bash
molcascade model-bundle /path/to/bundle
```

Two formats exist because two kinds of model do. An **ONNX bundle** takes a fixed-width
vector that MolCascade computes, which covers anything exportable with `skl2onnx`. A
**Chemprop bundle** takes SMILES and lets Chemprop featurize the molecular graph itself,
which is the part an ONNX bundle cannot express. Loading a Chemprop checkpoint executes
code, so it additionally requires an explicit trust acknowledgement — a digest says which
bytes ran, not that running them is safe.

`molcascade model-bundle` is also the reference for the manifest itself: it validates every
field and names what is wrong, so pointing it at an incomplete directory is the fastest way
to learn the format. A multitask model must say which output column it means with
`task_index`, and a molecule the model cannot score is reported as unscoreable rather than
quietly dropped.

## Vendored assets

Some backends read published model weights or rule tables that MolCascade uses but does not
author. Those files are declared in a catalogue with their SHA-256 digests, sizes, upstream
URLs, licences, and the paper to cite, and they are fetched by an explicit command:

```bash
molcascade assets list            # what is declared
molcascade assets status          # what is here and whether it verifies
molcascade assets fetch --all     # download everything missing
molcascade assets fetch scscore   # or just one
molcascade assets where 'asset:scscore/standalone_model_numpy.py'   # reference -> real path
```

Only `fetch` touches the network. **A screening run never downloads.** A run that needs an
absent asset stops and prints the `fetch` command that would supply it, so results depend on
files whose digests are recorded rather than on what a remote host served that afternoon.

Refer to a file from a cascade with `asset:<asset-id>/<path>` rather than an absolute path.
The reference resolves to the same content on every machine, and resolution verifies the
digest before handing the path to a backend.

Each asset also declares whether loading its payload executes code. A digest proves a file is
the one its author published; it does not make deserializing it safe. Gzipped JSON weights
that MolCascade parses into numbers itself and a Torch checkpoint that runs arbitrary code on
load are both pinned, and the catalogue keeps them clearly distinct.

Assets live in `vendor/` in a source checkout and in the user data directory from an installed
wheel. `MOLCASCADE_ASSET_ROOT` overrides both. See [`vendor/MANIFEST.md`](vendor/MANIFEST.md).

## What to cite

A screen ends in a methods section, and the methods section has to name the tools that decided
which molecules survived — not MolCascade, which orchestrates them.

```bash
molcascade cite                         # print every reference
molcascade cite --output citations.md   # write a copy to drop beside a manuscript
```

The output covers every backend the builder can place in a cascade and every vendored weight
file, with its licence, its pip requirement, and its paper. It is generated from the
catalogue rather than maintained by hand, and a test fails if any option is added without a
reference — so the bibliography cannot quietly fall behind the tools.

## Driving it from an agent (MCP)

MolCascade ships an MCP server so an agent can plan, run and audit a screen through
structured tool calls instead of parsing terminal output. Same library, same contracts,
same artifacts as the CLI; nothing about a run changes because an agent started it.

**Where it lives**

| Path | What it is |
| --- | --- |
| `src/molcascade/mcp_server.py` | Entry point. `python -m molcascade.mcp_server` |
| `src/molcascade/mcp/__init__.py` | Builds the shared `FastMCP("molcascade")` instance and registers every submodule |
| `src/molcascade/mcp/_common.py` | stdout guard, error envelope, absolute-path checks, workspace resolution |
| `src/molcascade/mcp/environment.py` | `doctor`, `environment`, `plugins`, `citations`, `assets` |
| `src/molcascade/mcp/configuration.py` | `validate_config`, `inspect_bundle`, `generate_builder` |
| `src/molcascade/mcp/screening.py` | `plan_screen`, `screen`, `run_status` |
| `src/molcascade/mcp/results.py` | `export_shortlist`, `trace_run_stages`, `generate_report` |
| `src/molcascade/mcp/audit.py` | `summarise_decisions`, `explain_molecule`, `audit_stereochemistry`, `measure_recall` |
| `src/molcascade/mcp/resources.py` | `molcascade://overview`, `molcascade://contracts`, and the `calibrate_a_funnel` prompt |

Eighteen tools, grouped by the question they answer: *can this machine do it*, *is this
configuration valid and what would it build*, *run it and how did it go*, *give me the
product*, *why did it decide that*.

**Installing and registering**

```bash
pip install -e '.[mcp]'          # the SDK is optional; a screening run needs none of it

claude mcp add --transport stdio molcascade -- \
  /path/to/env/bin/python -m molcascade.mcp_server
```

Name the interpreter explicitly. MolCascade's backends live in the environment it was
installed into, and a bare `python` resolves to whatever is first on the client's PATH —
which is how a server comes up reporting every backend unavailable.

```bash
mcp dev src/molcascade/mcp_server.py     # browser debug UI
```

**Two properties worth knowing before driving it in a loop**

*The expensive tool is asynchronous.* `screen` runs the cascade on a worker thread, so a
run that takes an hour does not take the session with it — the event loop stays free and
the client can still be talked to. A synchronous tool function would be invoked inline on
the event loop and would freeze the server for the duration, which is not a theoretical
concern for a tool whose job is to screen a library.

*A retry is safe, and that is the runner's property rather than the adapter's.* Artifacts
are content-addressed and immutable, stage checkpoints are keyed by cache key rather than
by run id, and resuming re-verifies every committed checkpoint's bytes and lineage before
re-using it. So the correct response to a call your client gave up on is to call `screen`
again with the same `run_id` and `resume=true`: completed stages are verified and re-used,
and only unfinished work re-executes.

Nothing in the server fetches an asset, installs a package, or reaches the network. A
configuration naming an unprovisioned asset fails on the way in with the command to run.
Read `molcascade://overview` before the first call; it carries the five properties of a
screening result an agent is most likely to get wrong.

## Reproducibility and safety

- Raw source records are never overwritten.
- Parent identity is established once and is distinct from downstream simulation states.
- Hard rejects cannot be rescued by later scores.
- Stage outputs are immutable, content-verified artifacts.
- A run records durable status and machine-readable audit events.
- Cache reuse and resume verify artifact identities before accepting prior work.
- Vendored weights and rule tables are pinned by digest and verified before a backend sees
  them; a run stops on an absent or altered asset rather than fetching one.
- Duplicate YAML keys, unknown configuration fields, unknown plugins, contract mismatches,
  cycles, path escapes, and untrusted executable adapters fail closed.
- Assets a run will need are resolved before the first molecule is read, so a missing weight
  file costs nothing instead of surfacing halfway down the funnel.
- The HTML builder writes configuration and nothing else: it invokes no package manager,
  no model downloader, no docking engine, and no remote service. Choosing a docking engine
  there records the choice; it runs only when a run does.

## Development

```bash
python -m pip install -e '.[analytics,dev]'
pytest -q
python -m ruff check src tests
python -m mypy
python -m build
```

Linux or WSL2 is recommended for large production workers and optional native backends.
The generated HTML is intended to remain portable across Windows, Linux, and macOS
browsers.

### Independent redock consistency (default)

The starter cascade now runs `t9_redock` immediately after the docking gates,
using `docking.redock_consistency@0.1.0`. It re-searches the retained Uni-Dock
candidates from fresh ETKDG geometry and a different random seed. The chemical
state is read from the original pose and checked against the parent, preserving
any stereoisomer selected when the parent left stereochemistry unspecified;
the original coordinates are discarded before independent embedding.
Acceptance requires **symmetry-corrected heavy-atom RMSD < 2.0 Å**, with both
poses in the same receptor coordinate frame and **no ligand alignment**.
Exactly 2.0 Å fails; missing poses, nonfinite coordinates, incompatible chemical
identity, exhausted symmetry mapping, and failed re-search cannot pass.

This checks agreement between predictions, not accuracy against an experimental
crystal ligand and not binding affinity. The 2 Å boundary is a configurable
engineering default inspired by common crystal-redocking evaluation; calibrate
it for each target with known actives and separate crystal-ligand redocking.
See [DockRMSD](https://doi.org/10.1186/s13321-019-0362-7),
[spyrmsd](https://doi.org/10.1186/s13321-020-00455-2), and
[PoseBusters](https://doi.org/10.1039/D3SC04185A).

The gate reads `derived_metric/v1`, metric `dock_redock_rmsd`, unit `ANGSTROM`.
The evidence retains the original pose digest, original method, both seeds,
repeat pose, score, and comparison policy. Original docking scores and exported
poses remain unchanged; the gate decides which parents reach later metrics and
MD. The source method and receptor must match the declared configuration, so
changing the source engine settings requires copying those settings to redock
and retaining the old seed as `source_seed`. Search uses the new `seed`.

The builder exposes the RMSD ceiling and independent seed. To explicitly opt out,
disable or remove `t9_redock` in the cascade JSON, or call
`default_cascade(include_redock=False)`. This skips a second GPU search and is
visible in the saved plan. Only the default retained Uni-Dock pose is tested;
other engine poses need their own validation before being substituted downstream.
