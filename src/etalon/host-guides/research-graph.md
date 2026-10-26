# Research graph and selective memory

Search, Judge, Quick and Deep are workspaces in one research history. Deep covers
long-running, expensive and information-rich computation or lab collaboration;
no resource allocator or new lab executor is implied. Evidence retains one
identity even when several nodes use it. Completed, failed, skipped and pending
branches all remain relevant.

`etalon_research_context(entry=..., view="card")` assembles a bounded,
read-only continuation card, including the checkpoint's last declared step.
An explicit card avoids constructing an unused display graph. Source and
original checks still complete before return; full graph coverage is not claimed.
The existing `view="summary"` remains available for its broader navigation view.
Use `view="node", node_id=...` for a neighborhood, then exact evidence reads.
The existing `view="full"` remains the default for compatibility.
`etalon_research_graph(workspace=..., checkpoint_ref=...)`
provides the broader graph. Source watermarks, selection policy, exact references,
counterevidence coverage and pause state are part of the result. Empty or
truncated coverage is not a negative scientific finding. Use exact evidence reads
to expand conditions and inspect conflicts before applying an experience.

An explicit `scope={"root_refs": [exact_ref], "ordinary_hops": 0}` (or
`branch_id` instead of `root_refs`) limits ordinary graph expansion; declared
counter relations still close across the permitted sources. If the scoped graph
and required navigation exceed the existing byte budget, the graph/context call
returns `etalon-research-graph-page/1`. Continue with the same selection and
`next_cursor`; for context calls use the returned `read_context` as `entry` to
pin the source view. Rows and large UTF-8 fragments retain every original field,
including branches, boundary references and counterevidence. A final page alone
does not establish complete delivery.

`etalon.research.graph_scope.assemble_graph_pages(saved_pages)` reconstructs a
complete projection; `audit_graph_pages(saved_pages)` also accepts partial
delivery and reports the remaining cursor. These operate on saved page bytes;
they do not reread original scientific bodies. Normal continuation need not
download the whole graph: start with the card and the exact evidence needed for
the current decision, retaining any unread scope. Node limits still apply;
select a narrower root when a branch exceeds them.

For exact changed evidence, `view="impact"` accepts
`view_options={"changed_refs": [exact_ref], "max_nodes": 32}`. It follows declared
dependents and counter/supersession links with explicit direction and boundaries.
It does not infer causation, invalidate a conclusion or dispatch a retry. The
graph, card and scoped pages share a request-local declared-relation index;
source restrictions and original-byte verification still apply.

Consecutive scoped graph/context pages reuse one disposable, bounded frozen-source
decode snapshot inside the serving process. Each call still checks original bytes
before reuse and before returning, recomputes visibility, counter incidence and
current continuation state, and freshly resolves mapped locations. No graph,
execution state or successful hash verdict is cached across calls. A new host or
cache eviction uses ordinary decoding with the same cursor and budgets; cache
availability is not a continuation requirement. Live journals remain uncached.
Within each collection, dependency discovery and visibility pruning share bounded
explicit-reference extraction; the pruning decisions themselves are recomputed.

Completed output can be archived with `etalon.research.publication`:
`prepare_publication` saves a named intent; `resume_publication` completes its
result/step/checkpoint/workspace records and advances the entry with an expected
identity check. Reuse the same intent after interruption. This publishes memory,
not another scientific execution. A stale entry produces an explicit conflict.
The research skill's progress-publication reference describes this write path.

Check the entry's `publications` alongside scientific pending handles. Empty
scientific pending does not mean archival work is complete. Unknown publication
coverage must stay unknown. This publisher appends steps and explicit evidence
references; it preserves branch/job/authority state rather than silently resolving
pending science. Use the existing explicit checkpoint interfaces for those changes.

The `research-context` skill handles memory selection, `method-configuration`
handles tunable methods and comparison contracts, and `skill-improvement` handles
evidence-driven revision and experience. Direct SDK use and partial workflows
remain valid. Changed defaults are candidates with a scope, not a global update.

An optional `research_model` Artifact records testable rules, applicability,
checker references and stable unresolved issues. Its revision review compares
explicit rule mappings and case-bound observations, retaining negative witnesses.
It does not train a model, replace the existing finite-world research planner,
run checkers or automatically adopt/reopen RSI. Changed interpretation of identical
evidence is distinct from new evidence. Use the existing method evaluation and
experience records for a scoped adoption decision.

`pose_verifier.verify_pose_endpoints` illustrates this separation on fixed pose
originals: independent physical checks remain available when reference identity
prevents RMSD evaluation. Per-slot input qualification is verified through exact
Artifacts. Joint success still requires all frozen physical checks and qualified
RMSD; historical unknowns remain unchanged. This protocol does not establish
affinity, new pose accuracy or independent scientific transfer.

Trace declaration, compilation, actual execution and admission separately.
Observed source receipts and agent-declared reasons are distinguishable. A
rejected result still belongs to the history. A new workflow may omit redundant
steps while satisfying the same required checks; a silently missing backend does
not establish a speedup. Compare methods using a fixed external evaluation
contract, and retain unknown outcomes instead of inventing counterfactual gains.

Optional project-local lifecycle hooks can be exported with
`python -m etalon.host export --host codex --target NEW_DIRECTORY --research-entry ENTRY`.
Use `--host claude` for that host. Export does not modify global memory or settings.
Native hosts may require their own project trust before hooks run. Without hooks,
the same read-only MCP/SDK context entry remains usable. Hook receipts attest to
context preparation and observed tool payloads, not to model consumption or
scientific truth. Resume never dispatches or retries work.

SessionStart sends a self-contained card. Subsequent tool hooks send changed
fields or an unchanged notice while retaining current controls. A delta names its
required baseline and an exact entry reread route; if the baseline is missing,
request a fresh card. Newly listed references may carry an optional exact impact
read; this is navigation, not an automatic review or evidence of new science.
Compaction invalidates the delivery baseline. Stop checks
the saved checkpoint and unfinished publications; it does not invent or publish
scientific progress on behalf of the host.

For Codex exports, run `python -m etalon.codex_hooks --target NEW_DIRECTORY`
before native acceptance. This only inspects config layers and hook metadata;
it creates no chat or trust changes. Complete the host's project trust and
`/hooks` review if required. MCP connectivity, hook discovery, hook trust and
observed native delivery are separate checks.
