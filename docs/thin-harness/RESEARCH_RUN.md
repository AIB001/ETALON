# Continue a research question across native host exits

For ordinary research, call the scientific method directly. Use
`etalon.runtime.reuse.execute_or_reuse` for exact-request reuse and an optional
explicit `recover` callback; native handles live in `etalon.runtime.native`.
Neither depends on a research graph or publication. A recovery reads the saved
handle/output and does not dispatch the original callback a second time.

Continue from each result toward the scientific objective. A completed phase or
retain/no-change decision does not by itself complete it. Known methods and
understood conditional followups run as code batches, with matching completed
requests reused; the host handles new residuals that could change the next action.
Paths may be reordered, omitted or replaced by cheaper methods when exact reasoning
or scoped evidence preserves the required decision and external quality. Keep the
conditions and fallback, and leave omitted outcomes unmeasured. Task notes retain
learned conditions and opportunities; skills retain the reusable procedure.

Save a significant finding with `research.notes.record_note(journal, note, directory)`
and advance its entry with `publish_note(entry, journal, record_id)`. One immutable
note in the existing journal contains `task_id`, `branch_id`, `question`, `summary`,
conditions, original/counterevidence pins and optional outcome, methods, residuals,
pending handles and next action. Use `notes.original(path)` for a file pin.
Superseding notes name prior record IDs; entry replacement is atomic. Failed
recording leaves completed science intact. Existing context MCP and hook delivery
read this entry directly. `find_notes` returns scoped negative/positive records and
explicit condition gaps; selection and adoption belong to the host.

Use `notes.resolve_method(entry, pointer)` to resolve a published callable's
existing description and pinned source without loading its implementation into
the conversation. Call it in the bound environment using its declared inputs and
conditions. `notes.open_original(entry, pointer)` opens one verified result;
inside that context, `saved.array(name)` loads and reuses only the named NPZ
member. Do numeric analysis in code and return decision-relevant differences and
exceptions. `notes.read_original(..., selector={"json": ["/counts"]})` gives
bounded exact fields; an omitted selector gives metadata. The same entry paths
are available through `etalon research method` and `etalon research original`.
Descriptors remain ordinary note fields; these helpers neither select methods
nor add a registry, execution stage or global cache.

For one-call recording and entry replacement, prefer `notes.save_progress`.
It checks original bytes before recording and immediately before entry replacement;
identical replay checks once without creating another note. Cite immutable run
outputs and receipts, not mutable `latest` or `last-call` files. Keep computed
outputs before interpretation/publication so a later failure can be repaired
without repeating the calculation.

The existing `plan_research_handoff` / `export_research_handoff` APIs accept note
entries as well as graph checkpoints. Notes carry history, selected resources and
explicit originals using a consistent SQLite backup and location maps. This does
not migrate or restart external jobs. On reading, use `notes.entry_journal` to
resolve portable locations; do not construct a store from the historical database
string. Current branch/definition inventories can be paged through their returned
navigation; a small context does not imply all controls or counterevidence were read.
Explicit `entry_journal(entry_value(entry), read_only=False)` continues a sealed
source through an atomic active copy of the same journal. Publishing advances the
current entry to that copy; archived database, mappings and original bytes remain
unchanged. Reopening after an interrupted publication reuses the active copy.

`examples/record_method_comparison.py` uses this one-note path by default.
`--detailed` retains the old graph attribution when needed. Existing graph entries,
paused tasks, source snapshots and public import paths remain readable; no bulk
migration or new experiment is needed to continue them. The adapters below describe
that existing detailed path, which is optional for new ordinary work.

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

For a continuing scientific question, the same profile may add:

```json
{
  "continuation": {
    "objective": "Explain the residual discrepancy under the declared conditions",
    "completion_condition": "The remaining explanations have a discriminating result or an explicit unresolved limit",
    "authorized_actions": ["Interpret existing receipts and run the already authorized task-local CPU controls within the saved budget"],
    "resume_after_host_exit": true
  }
}
```

These are ordinary scoped instructions from existing authorization, not an action
router. The top-level `completion_condition` then describes a local segment;
`continuation.completion_condition` defines completion of the lasting question.
The host chooses useful next actions, methods and whether to improve them. No
mandatory probe, phase order or improvement is introduced. Existing pause and
resource constraints remain applicable. Without `continuation`, old profiles keep
their existing completion behavior. Neither profile expands permission inferred
from a natural-language goal.

`native.cwd` may be a narrow task workspace instead of the whole source repository;
absolute entry and method paths remain in the capsule. For a non-Git workspace,
explicit `native.skip_git_repo_check=true` supplies the CLI flag. This does not
change project trust, permissions or hook review. Profiles can retain the actual
host's configured permissions rather than hard-coding a wider sandbox.
Optional profile `context_max_bytes` passes the existing reader budget into the
initial capsule and every subsequent current-note read (4096..65536; default
16384). Omitted bodies retain exact navigation and coverage markers. This bounds
the knowledge response, not the entire host prompt or its total token usage.

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
| `segment_complete` | Continue after a local segment; the lasting question remains open |
| `waiting` | Wait the stated interval, then inspect existing work on resume |
| `complete` | The continuation completion condition, or the original capsule condition when absent, was met |
| `needs_user` | A concrete dependency requires information or new authority |

When an existing external handle is pending, the host may write `wait.json` in its
turn directory with schema `etalon-host-wait/1`, `poll_seconds`, and `checks`.
Checks support native attempts, explicit file markers or JSON status values.
The adapter reads status without another model call while unchanged; ready/failed
results return to the host, and unknown/check errors remain explicit. No new job
queue is created. Timer-only legacy waiting remains compatible.

Native token usage and exact-ID linked child receipts are counted once. Missing
usage or prices remain unknown. A fresh host receives the latest note and previous
checkpoint; a resumed-session claim requires an observed matching native session ID.

Native Codex `turn.completed.usage` contains cumulative thread counters on resume.
New native receipts tag `usage_scope="thread_cumulative"` and retain those raw
values. The summary charges successive monotone differences for each exact session,
then sums distinct sessions. A counter regression, missing resumed-session baseline
or mixed cumulative/delta scope remains unknown; it is not rebased to zero. After
abnormal interruption, a resumed CLI cumulative counter can omit an already billed
response from the interrupted tool call. A later complete snapshot therefore does
not clear that turn's unknown usage: coverage remains partial. Independently linked
exact response IDs can establish a separate measured total; the adapter does not
guess or manufacture the missing contribution.
`native.usage_scope` can explicitly select `thread_cumulative` or `turn_delta` for a
known transport. That explicit profile also interprets older untagged receipts;
otherwise untagged custom/legacy receipts retain their old delta semantics. No
counter-value heuristic or historical receipt rewrite is used. `model_response_count`
is available only for complete explicitly identified response records; host turns
and cumulative snapshots are not model response counts.

The adapter retains process/session identity, usage and output receipts and uses
native `exec resume` after normal exit. A fresh independent session can instead
receive the current entry and a new capsule; it must inspect the existing handles
and outputs. These are different operations, both reusing completed science.
One invocation owns its run directory. Reinvoking the same capsule/directory
continues the durable host state; it does not create a new scientific run.

The wall limit controls admission of another host turn. It does not kill an
in-flight host or external job. Scientific execution uses its separately stated
budget. If a controller exits before saving a turn receipt, reentry first inspects
the recorded host's creation identity. A live process is left alone; a failed
identity read by itself remains unknown. With explicit
`continuation.resume_after_host_exit=true`, proven process exit plus a matching
launch intent, capsule identity and recorded native session permits another host
turn to reconcile the existing entry, receipts, outputs and pending handles.
This uses the existing session in `resume` mode; `fresh` mode receives the same
recovery facts without native conversation history. It never resubmits scientific
commands. Missing or inconsistent evidence remains `unknown_host_outcome`.

If the host left a normal failure receipt (a nonzero exit or missing checkpoint),
the same explicit authorization, positive exit evidence and exact session binding
permit reconciliation. Its original receipt stays unchanged; `recovery.json`
records the continuation evidence. Missing session identity never manufactures a
new session. Without this authorization, failure retains the old stopping behavior.

When interruption lost the receipt, exit code, duration and unobserved usage remain
unknown; measured records and `known_host_wall_seconds` remain separately available.
An existing failure receipt retains its measured exit code and duration. All
interrupted turns count toward the original host bounds, and unobserved usage is
marked partial. `objective_complete` reports the host's checkpoint
claim only when the lifecycle stopped on `complete`; it is not external scientific
verification. Use an already reviewed native host installation; this adapter does not
install hooks, change account settings or grant trust.

## Let producers publish usable results

For an interrupted producer, `run_segment(..., recover=resume_saved_handles)`
allows an explicit task-owned recovery callback. The adapter never calls the
original producer again on an unknown outcome. The callback verifies completed
outputs and native handles, then returns the ordinary result. Original duration
remains unknown if interruption prevented measurement; recovery time is separate.

`examples/read_research_work.py ENTRY --branch BRANCH --counter-bodies --packet`
delivers current choices, scoped experience heads and all counter-evidence refs.
Use `--receipt packet.json`, then `--previous packet.json` to deliver changes.
Original reads and verification still run; this reduces output/context bytes,
not verification coverage. A new host without the base receipt reads a full
packet. Full historical material remains reachable through the work reference.

For note entries, a task-owned method can use native execution/reuse and save its
meaningful result through `notes.save_progress` or `notes.patch_progress`. The
optional `etalon.research.research_segment` adapter runs a known batch with either
a note entry or an existing graph checkpoint entry:

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

Optional `feedback` is a compact task-specific object: it can preserve contrasts,
learned conditions, counter cases, untested questions and possible next actions.
An `actions` list contains `{id, command, ...}` options; `command` uses the ordinary
next-segment shape or `null` for no execution. If `selected_action` names an option,
its command must match `next_segment`. The adapter checks that linkage and passes
feedback to the current work/note; the host interprets it and can choose differently.
The next result replaces current feedback, while prior results remain evidence.
No feedback field makes an omitted comparison measured or forces a new algorithm.

Optional `counter_refs` and `method_refs` pass through to the existing publisher;
`{"publication_ref": "original_name"}` can name a just-produced file. Thus a
new negative result becomes a checkpoint counter without copying the historical
evidence into the work card. Note entries accept original file pins or these named
outputs as counters; graph evidence refs stay on the graph path. Reading a current
work object checks record schema
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

The adapter saves the producer response and work, then publishes through the
entry's existing note patch or graph result interface. For graph entries,
`read_work` verifies the work selected in the branch's current `next_read_refs`;
notes carry a pinned segment work file. A fresh host need not reconstruct the
full source history before running its next known segment.
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
# Stable project knowledge

Ordinary progress uses the existing configured journal and one project entry:
`research.notes.save_progress(journal, entry, note)`. Branch heads share that
entry; identical writes replay without adding a record. Keep execution artifacts
under their original runs, task-local algorithms in methods, and note bodies in
memory. Existing historical paths remain valid. No additional database schema,
global research graph or forced workflow is introduced.

The context MCP's card/summary and native hooks deliver condensed knowledge.
`read_entry(..., view="knowledge")` checks the knowledge body; it explicitly
reports original bodies as unread. Expand the returned details with `view="full"`
for original verification and all evidence/counterevidence. Resource catalog
reads current branch heads; `find_notes` retains historical and negative knowledge.
Both use the same journal. Generated views/cursors can be rebuilt and are not
scientific observations. Runtime receipts remain the execution authority.

MolQuarry's existing curation accepts optional agent-authored knowledge;
`build_target_bundle(view="knowledge")` returns it without preparing a structure
bundle. Internal research feedback is written directly to the project journal.
