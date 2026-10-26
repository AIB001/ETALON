# Continue a research question across native host exits

The host chooses scientific actions. A small optional process adapter keeps that
host running within an explicit task budget. Scientific jobs keep using their
existing native handles; research state keeps using the journal, checkpoint and
entry. No additional scientific router, scheduler or memory store is introduced.

## Give an independent host a small task

For a natural research goal, reuse a profile containing the existing native
runtime, authority, resource budget and host bounds:

```text
python examples/research_host_lifecycle.py ENTRY.json RUN_DIRECTORY --goal "Resolve the remaining paired-result interpretation" --profile PROFILE.json --branch task-local-improvement
```

`capsule_from_goal(goal, entry, profile, branch_id=...)` constructs the same
capsule from the current checkpoint. It reads a published work object when one
exists and otherwise builds compact context from the selected branch. It cannot
infer permission or a resource budget from the goal. The profile retains those
existing constraints. `session_mode="fresh"` starts independent contexts using
the latest work object; the default `"resume"` preserves native session history.
Neither mode requires a preset two-turn demonstration or changes the task's
scientific completion condition.

Use `examples/research_host_lifecycle.py CAPSULE.json RUN_DIRECTORY`. The capsule
has schema `etalon-host-task-capsule/1` and these fields:

| Field | Purpose |
| --- | --- |
| `task_id`, `entry`, `question`, `scope` | Stable task identity, absolute entry and current unresolved decision |
| `authority`, `budget`, `completion_condition` | Allowed work, explicit cost bounds and a meaningful stopping point |
| `host_instructions` | Essential exact artifacts, local/remote invocation and output ownership |
| `native` | `codex_executable`, `cwd`, `model`, `reasoning_effort`; paths are absolute |
| `bounds` | Positive `max_host_turns` and `max_wall_seconds` |

Start with the capsule, not a copy of the parent's conversation. Read the current
entry and the originals needed for this decision, including relevant counterevidence;
expand elsewhere when the question demands it. Parallel specialists own separate
outputs. Return a small finding delta, exact artifact references, pending handles
and cost; keep raw logs and calculation arrays in artifacts. A lead merges changes
that affect the question instead of rereading every specialist transcript.

## Exit is not completion

Each native turn ends with a structured `etalon-host-checkpoint/1`: `task_id`,
`state`, `entry`, `summary`, `basis_refs`, `wake_after_seconds`. States are:

| State | Next behavior |
| --- | --- |
| `continue` | Resume the same native session for authorized remaining work |
| `waiting` | Wait the stated interval, then inspect existing work on resume |
| `complete` | The capsule's scientific completion condition was met |
| `needs_user` | A concrete dependency requires information or new authority |

The adapter retains process/session identity, usage and output receipts and uses
native `exec resume` after normal exit. A fresh independent session can instead
receive the current entry and a new capsule; it must inspect the existing handles
and outputs. These are different operations, both reusing completed science.
One invocation owns its run directory. Reinvoking the same capsule/directory
continues the durable host state; it does not create a new scientific run.

The wall limit controls admission of another host turn. It does not kill an
in-flight host or external job. Scientific execution uses its separately stated
budget. An unknown host outcome needs inspection rather than an automatic new
process. Use an already reviewed native host installation; this adapter does not
install hooks, change account settings or grant trust.

## Let producers publish usable results

For a task-owned Python method that already knows its calculation batch, use the
optional `etalon.research.research_segment` adapter:

```python
from etalon.research.research_segment import work_from_entry, run_segment, read_work

work = work_from_entry(goal, entry, branch_id=branch,
    residuals=["Which competing explanation survives the paired comparison?"])
receipt = run_segment(work, output_directory, producer=calculate_known_batch,
    method_id="paired-analysis/1", entry=entry, operation_id="paired-analysis-01")
current = read_work(entry, branch_id=branch)["work"]
```

`calculate_known_batch(work, output_directory)` is ordinary Python. It can run
all understood deterministic steps before returning a new residual that needs
judgment. It returns `summary`, `measurements`, explicit `failures` denominators,
the existing `decision` fields, `residuals`, and `next_segment` (or `None` when
the next useful action is not yet executable). Optional `candidate_index`,
`decisive_examples`, `costs` and `originals` keep the result useful without raw
arrays. Use `result_file(path, name, ...)` to pin original files. A next segment
is a normal `{argv,cwd,reason,ends_when,inputs}` command with absolute executable
and directory and input `{path,identity}` pins. It is a host option, not a DSL
or automatic scientific router.

Optional `counter_refs` and `method_refs` pass through to the existing publisher;
`{"publication_ref": "original_name"}` can name a just-produced file. Thus a
new negative result becomes a checkpoint counter without copying the historical
evidence into the work card. Reading a current work object checks record schema
bindings before expanding its body; other declared result bodies stay available
for the relevant scientific decision.

For example, once mixed-sign sensitivity analysis is an understood method, keep
both operations in the producer instead of waking another host to choose a
mechanical followup:

```python
def calculate_known_batch(work, output_directory):
    paired = load_pinned_saved_pairs(work)       # ordinary task method
    summary = compare_all_pairs_and_best_case(paired)
    if summary["mixed_signs"]:
        summary["sensitivity"] = compare_endpoints_and_matched_quality(paired)
    # This task function returns the compact response described above, with
    # the genuinely new mechanism/experiment question as its residual.
    return compact_response(summary, output_directory)
```

These are illustrative task functions, not new ETALON APIs. Invoke an already
published ordinary `next_segment.argv` directly when its scientific reason and
dependencies remain appropriate; use a fresh host when the residual needs a new
judgment or ownership changes. An unchanged pinned method does not require
rereading its implementation, publication intent or completed history. A compact
result plus the existing publication receipt is the normal return; expand the
specific original needed to resolve a contradiction or error.

The adapter saves the producer response and work as ordinary Artifacts, then
automatically calls the existing result publisher. `read_work` verifies the
work selected in the branch's current `next_read_refs`; a fresh host need not
reconstruct the full source history before running its next known segment.
Pauses, pending handles and original evidence remain available. An absent next
command means an honest implementation or judgment gap, not an invented action.

Reentry with the same directory, work, method identity and operation reuses the
saved producer result and exact publication intent. Publication interruption
does not repeat calculation. A producer interrupted before saving its response
has an unknown outcome and requires inspecting its task-owned outputs/handles.
For an existing producer result, call `publish_segment_result(...)` directly;
there is no need to wrap or rerun the scientific method. Measured producer and
adapter elapsed times and compact bytes are limited counters, not token savings
or a scientific-quality result.

Emit a saved request for the existing `prepare_result_publication` API, including
the actual output pins and next scientific choice. Then run:

```text
python examples/research_publication.py publish-result REQUEST.json
```

This prepares and resumes the existing durable publication and returns a small
receipt. The saved operation ID and request are reused after interruption. A
publication failure is not a reason to rerun science. Keep one writer per entry;
give parallel branches separate entries and explicitly join their results.

Publish when a result changes a decision, a job will outlive the host, or work
passes to another owner. Routine deterministic substeps can run together. A
checkpoint restores progress; it is not an approval boundary or a stopping rule.

Put reusable calculation in versioned method code, applicability and observed
benefit in linked rule/experience artifacts, and current progress in the entry.
Skills describe how to choose and improve these resources. They do not accumulate
the experiment history. Scientific freedom includes changing the action family,
keeping a local method, trying an imperfect candidate, or deciding that no
modification is useful.
