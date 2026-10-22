# SND1 molecular method improvement during normal execution

The optional examples `snd1_pose_workflow.py` and `research_snd1_improvement.py`
exercise normal-use improvement on real SND1 C26 structures. They add no core
scheduler, required Probe stage, model call or global method default.

MolQuarry supplies RCSB entry/polymer/CCD evidence. PRISM supplies map-neutral
chemical identity and original protein-frame geometry parsing. RDKit executes
ETKDG and MMFF. This example does not call the full PRISM design producer,
MolCascade docking, MD, MM-PBSA or an affinity endpoint.

The comparison uses a common experimental template and a target chemical graph:
free conformer optimization followed by graph-core alignment versus a coordinate
map and fixed-core optimization. An experimental target pose enters only the
post-generation evaluator. All failed slots, convergence, severe clashes,
full-heavy-atom pose RMSD and measured costs remain visible. Alignment moves the
reference protein/ligand frame, never fits a prediction onto its answer.

`research_snd1_improvement.py` records the context, method versions, graph patch,
evaluation plan, waiting assessment, actual uses/results/costs, disposition and
conditional experience through existing ResearchJournal and batch-4 APIs.
Subsequent branches inspect graph applicability and retrieved dispositions before
selecting a method. The example host rule honors explicit supersession and stops
for conflicting unsuperseded advice. Retrieval itself does not adopt a method.

The preparation input layout is explicit: `PROTOCOL.md`, MolQuarry query snapshots
under `data/data/{entry-7knw,polymer-7knw,ccd-qoy,ccd-qov}`, and unmodified RCSB PDB
representations under `data/originals/{7KNW,7KNX}.pdb`, each with its download receipt.
Current MolQuarry downloads mmCIF; the example acquisition retains an explicit
official PDB-download receipt for that representation rather than claiming the
adapter exposed it. Pose methods and the protocol must be frozen before outcomes.

Use an interpreter with RDKit, NumPy, ETALON and PRISM's declared design
dependencies. The driver takes explicit `--root`, `--etalon-source`,
`--prism-source`, `--authority`, and optional `--installed` paths. Its stages are
`init`, `compare`, and `followup`. These are example CLI commands, not compulsory
research phases. Each owned generation batch is bounded and charged through the
existing resource-execution ledger. No resource assignment policy is added.

Generation requests bind input bytes, actual library/recipe identities, method
and seeds. Reading a completed identical request verifies artifacts and reuses
it; unfinished requests remain pending and changed requests are rejected. The
research entry contains only a workspace and checkpoint reference. A later chat
can read evidence, experience and resolved branches without launching generation.

The 2026-10-22 local SND1 case improved successful preparation slots from 4/12 to
12/12 under its predeclared pose/clash/cost criteria; the selected reverse-series
branch also produced 12/12. Twelve fixed-core runs represented one distinct heavy
pose, not twelve independent molecules. A chemically distant aspirin graph was
outside the template's two-sided core-coverage domain and received no matching
experience. These are conditional geometry and engineering results on two related
public structures, not affinity, diversity, independent transfer, or general RSI
benefit. Full evidence and costs live in the local batch-5 delivery record.
