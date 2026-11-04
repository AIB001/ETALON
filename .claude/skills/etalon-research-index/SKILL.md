---
name: etalon-research-index
description: Select relevant methods, skills and conditional experience for the current decision; distinguish reusable parts from task-specific settings and resume an unchanged selection without rereading the library.
---

# Research resource index

For a small note-based project, use the existing resources view or
`python -m etalon research resources --entry ENTRY`. Explicit note resources
separate executable methods, tuned configurations, usage skills and conditional
knowledge without a second library. Read [layered methods](references/layered-methods.md)
when retaining a useful improvement or finding an executable entry.
`etalon_method_publish` saves a callable method; `etalon_method_resolve` produces
its existing action request. `etalon_research_progress` writes note changes and
handles. These are real MCP tools with the same SDK/CLI implementations.

Use this when deciding what earlier research can contribute to the current task,
or when organizing new resources for later retrieval. The host owns the decision;
direct SDK use, partial adoption, a task template or no change remain valid.

Start from the research entry with `etalon_research_context(view="summary")`.
Keep its `read_context` for `etalon_evidence_read/query`. If `task_workset.status`
is active and the question/conditions have not changed, inspect and reuse that
workset; a new chat is not a new improvement opportunity. Check completed receipts,
pending and publication status before considering any execution. An unavailable
workset is not an empty one.

For a changed question, describe the target, chemical/template relation,
objective, method/protocol and decision stage. SND1 is a target/series;
PoseBusters is a dataset/evaluation context. Neither name alone determines reuse.
Bind important metric/state meanings with `make_task_profile(definitions=...)`
and the applicable resource part's `definitions`, using producer definition
digests rather than matching labels. Missing legacy definitions remain unknown.
A portable part can explicitly declare `definition:<name>` irrelevant; a changed
meaning needs adaptation/reference or a compatible template, not silent reuse.
Read only permitted sources; evaluation-only answers cannot become conditioning
input just because their molecular identity matches.

## Organize resources

For discovery use `etalon_research_context(view="resources")` with the entry or
its pinned `read_context`. Supply `view_options.query` with current `conditions`,
`critical_conditions`, and relevant `definitions`/`available_stages` to see
matched, conflicting and unknown parts together. These are declaration checks,
not scientific validation or an instruction to execute the whole resource.
The broad note catalog exposes a compact `family_navigation`. Use
`view_options.families` for the relevant declared operation families; unclassified
and other families stay accessible through broad discovery. This is an explicit
scope, not a claim that excluded resources have no value. Use
`view_options.resource_ids` for already-known method names; their feedback is
included unless your explicit kind filter excludes experiences. Resolve selected
bodies and meaningful counterexamples, not every catalog body. Broad discovery
uses bounded pages: keep the cursor/coverage when the next decision needs more.
Inventory membership supplies neither applicability nor activation. For one
branch use `view="branch", branch_id=...`; task-wide selection, exact usage links,
unknown consumption and later decisions remain distinct. These are optional
views over the same records, not additional memory stores.

Use `etalon.research.meta_context.describe_resource` and `index_resources` on an
explicit bounded set. Keep originals in their existing libraries. Each part names
its conditions, required dimensions, explicitly irrelevant dimensions, available
stage and dependencies. Skill parts name exact bundle members. Unknown premises
remain unknown. Separate source-declared conditions from your proposed scientific
interpretations; do not rewrite an old claim's scope. Preserve support and counter
references. Source errors must not be summarized as no useful resources found.

## Review for this task

For a durable multi-resource workset, use `make_task_profile` and
`prepare_adaptation_context` for mechanical checks.
These checks do not establish scientific benefit. With `review_adaptation`, state
for each considered part: reuse, adapt, reference or exclude, with a reason and
reconsideration condition. Reuse requires matched declared conditions. Adapt is a
candidate, not permission to run the old method under incompatible assumptions.
New candidate versions and normal-execution evaluation use existing RSI APIs.

Use `build_task_workset` to freeze the chosen metadata. Include a compatible task
template and the dependencies of each enabled part. Template fallback needs an
explicit reason; it does not prove the template is best. `publish_task_workset`
prepares an existing publication; `resume_publication` completes archival only.
This formal workset route is optional; ordinary note-based research can retain
selected exact content references, parts, conditions and fallback in its existing
progress note. Neither route requires another model review on unchanged use.
The formal workset is active only through its published checkpoint decision. Read it back
with `read_task_workset` and retain its exact refs, selected parts and exclusions.

When tracing a selected resource into actual work, use the optional resource-use
association in the API reference. Bind exact MethodUse, node, evaluation and later
decision refs; keep selected, read, consumed and executed separate. A delivered
skill or completed node does not show instruction consumption. Old records lacking
phase observations remain unknown in explicitly retrospective annotations.

For details and callable signatures, read `references/api.md` only when building
or publishing a workset. Those workset builders remain Python SDK helpers;
ordinary note progress and method publication have the MCP tools above.

When an independent context is useful, hand an agent the task, entry and permitted
scope rather than the full old conversation. Short reviews can use subagents;
ongoing research branches favor visible chats when the host supports them. A fork
that copies history is not an isolation test. Preserve one execution owner and
existing request identities. Hook receipts show delivery, not comprehension.
