# Research graph and selective memory

Search, Judge, Quick and Deep are workspaces in one research history. Deep covers
long-running, expensive and information-rich computation or lab collaboration;
no resource allocator or new lab executor is implied. Evidence retains one
identity even when several nodes use it. Completed, failed, skipped and pending
branches all remain relevant.

`etalon_research_context(entry=..., view="summary")` assembles a bounded,
read-only continuation card, including the checkpoint's last declared step.
Use `view="node", node_id=...` for a neighborhood, then exact evidence reads.
The existing `view="full"` remains the default for compatibility.
`etalon_research_graph(workspace=..., checkpoint_ref=...)`
provides the broader graph. Source watermarks, selection policy, exact references,
counterevidence coverage and pause state are part of the result. Empty or
truncated coverage is not a negative scientific finding. Use exact evidence reads
to expand conditions and inspect conflicts before applying an experience.

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

SessionStart sends a self-contained summary. Subsequent tool hooks send changed
fields or an unchanged notice while retaining current controls. A delta names its
required baseline and an exact entry reread route; if the baseline is missing,
request a fresh summary. Compaction invalidates the delivery baseline. Stop checks
the saved checkpoint and unfinished publications; it does not invent or publish
scientific progress on behalf of the host.

For Codex exports, run `python -m etalon.codex_hooks --target NEW_DIRECTORY`
before native acceptance. This only inspects config layers and hook metadata;
it creates no chat or trust changes. Complete the host's project trust and
`/hooks` review if required. MCP connectivity, hook discovery, hook trust and
observed native delivery are separate checks.
