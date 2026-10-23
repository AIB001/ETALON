# Pose tasks and normal-execution comparison

`research.pose_task.compile_task` compiles the existing scientific-input envelope
into an explicit, hashed execution request. It checks file/source binding,
visibility, finite box geometry, integer sampling parameters, and a declared
output contract. It does not submit a task. Keep experimental reference poses
and labels out of its conditioning sources. Known-pocket benchmarks must say
which site information the organizer intentionally releases.

`research.pose_verifier.verify_poses` reads predictions without repairing them.
Its current scope is known-pocket redocking in a shared receptor coordinate
frame. It combines symmetry-aware heavy-atom RMSD **without ligand fitting**
with PoseBusters validity checks, retaining malformed slots and missing checks
as failures. It does not establish affinity, cross-docking alignment or absence
from a model's training data. Reference coordinates belong to the evaluator.

`improvement.retrieve_experience(..., related_condition_keys=[...])` optionally
returns related, condition-mismatched experiences separately from exact
`matches`. They are hypotheses, not transferable adoptions. Existing callers
retain exact-condition behavior. `pose_task.select_pose_branch` demonstrates
a conservative optional policy: only unopposed applicable adopted experience
selects the candidate; missing, conflicting or related-only evidence keeps the
baseline. The host may choose another explicit policy.

The example `examples/research_posebusters.py` binds these interfaces to the
existing ResearchJournal, MethodVersion/Use/Evaluation, improvement opportunity,
execution budget, research-step, checkpoint and publication interfaces. It uses
MolCascade's native runner for a frozen GNINA executable. The paired methods
rank the same empirical pose pool; validation choices are archived before its
labels are read. Running alternatives creates an explicitly charged audit panel,
not free observations available to the selective policy.

Each native call has an immutable request, reservation, start marker, output
identities and terminal receipt. Recovery reconciles a saved receipt. A reserved
request without a receipt stops for inspection. Checkpoints are published to
immutable staged entries using the existing publication API, then the task's
entry is advanced by compare-and-swap; a durable handoff intent permits restart.
Existing publication's restriction against changing its selected parent is
preserved. Reusing a completed request never creates a new native execution.

The initial organizer `examples/prepare_posebusters.py` freezes a 24-case panel
from the final 308-member PoseBusters list. Selection is based on input
compatibility, distinct ligand scaffolds and a sequence similarity heuristic,
not prediction performance. The 8 development and 16 validation cases are a
small retrospective experiment; seeds are correlated within a case. Scientific
results, missing capability checks and host hook acceptance belong in the actual
run report, not in this interface description.

Source indexing and displayed graph size have separate bounds: at most 1024
typed projections / 8 MiB per published source, with the existing 1 MiB
per-artifact limit. A 128-node graph view does not imply that a journal may only
contain 128 typed records. Exceeding a read bound remains explicit incomplete
coverage; larger histories need scoped published sources, not silent omission.

`examples/close_pose_task_entry.py` prepares an explicit index of completed
task steps (24 references per typed node). Resume its prepared publication with
the existing API to attach the index to the checkpoint. A generic JSON summary
of node IDs is navigation data, not a substitute for typed provenance edges.
Continuation verifies up to 1024 linked records while the display stays bounded.
