# Optional SDK entrypoints

Use the installed SDK signatures; do not invent MCP tools or replace the host's
normal planning. All stored objects reuse ordinary ResearchJournal Artifacts.

- `etalon.research.research_decision.build_research_decision(workspace, ...)`:
  exact task/scope/branch, problem refs, structure, attribution, alternatives,
  predictions, selected/skipped/deferred actions, method/evaluation refs and gaps.
  A prediction links an action and alternatives to an observable, expected result
  and distinguishing interpretation. Unknown expectations are permitted.
- `build_decision_update(workspace, ...)`: exact decision, previous update,
  executed node refs, verified evaluation refs, every prediction's judgment and
  evidence. Preserve counterrefs and unresolved gaps. `disposition` is
  retain/revise/reject/defer/no_change, never method adoption.
- `etalon.research.evidence_window.build_evidence_window(workspace, ...)`:
  visible refs, excluded refs, workset, coverage, methods and timing mode.
  `retrospective` and `host_declared` establish no prospective timing.
- Controlled timing is opt-in: durable window → `deliver_evidence_window` →
  explicit `acknowledge_evidence_window` → durable decision → existing producer
  and receipt → `release_outcomes`. It verifies local publication order, not
  model ignorance. A future outcome slot binds the planned action, inputs,
  protocol, methods, producer and receipt contract before outcome bytes exist.
  Use the existing sealed-data contract; do not relabel sealed outcomes.
- `etalon.research.capability_profile.build_capability_profile(workspace, ...)`:
  explicit studies, each with decision/update/evaluation-contract/case refs and
  case/family/repeat labels. It replays evaluations and groups scoped evidence;
  it is neither an ability score nor an independence certificate.
- `etalon.research.revision_guidance.build_revision_input(workspace,
  decision_ref=..., prediction_id=..., context_ref=..., differences=...,
  update_ref=None)`: read-only bridge to the existing revision context. A
  difference selects exact JSON fields from declared diagnostic inputs and states
  its relation to named alternatives plus uncontrolled factors. Missing/null are
  distinct from zero/false. Later update observations remain separate.
  `build_candidate_from_decision` adds an existing opportunity, actual
  before/after edit, object kind and falsifier; it returns the existing candidate
  brief with the original observable, normal action and fixed check identities.
  It does not generate a method, select a candidate or reopen a terminal.
  Unknown predictions cannot become testable by supplying favorable prose.
  Existing evaluation/experience links require the exact attributed update.
  Readiness additionally binds the actual after-content/workflow to the selected
  action's candidate version and a fixed evaluation subject with the exact base
  comparator. An unmatched edit remains incomplete instead of borrowing the old
  action as its execution; verified identical-content aliases remain supported.

Persist supported content via `continuation.record_content`; connect it to
existing research-step outputs and publish/checkpoint through existing APIs.
Read with the ordinary evidence/context interfaces. A new chat follows exact
refs from its entry; selection or delivery is never proof of consumption.

Scientific input standards and method parameter contracts remain authoritative
for actual execution. This skill adds decision structure, not execution rights,
new resource allocation or a fixed Probe. R4b remains paused unless explicitly
resumed by the user.
