# Evidence, panels and optional scientific revisions

Use the smallest existing record/query needed. Constructing CampaignStore may
initialize a database: opening an unknown path is not a read-only existence test.
For an existing configured store, `ResearchJournal(store)` exposes `get(id)` and
`record(value)`; recording appends immutable content-addressed records/events.
Diagnostic records do not automatically become active-learning scalar labels.

## Evidence and interventions

`Artifact(path, sha256, bytes, source_kind, role, bindings)` separates bytes and
provenance. Roles are diagnostic, independent_validation and sealed_holdout;
development is a task role, not an Artifact role. Do not relabel data to pass a check.
`Experiment` identifies task/method, inputs, action references and kind:
file_audit, reanalysis, replication, intervention and recovery are different uses. Inspect
`schema.py` for the exact dataclass fields rather than guessing from a run name.
`Observation` retains measured/missing/failed/rejected/unsupported/not_evaluated
states; values and units are not filled for missing observations.
`journal.accounting(experiment_id)` reports existing cost provenance; it does
not launch or settle a job. `audit_json` in the journal module appends an
Artifact, file_audit Experiment and extracted Observation:

```python
from etalon.research.journal import audit_json
# Actual artifact must be in scope and readable, with the same declared bytes.
experiment_id, observation_id = audit_json(
    journal, artifact, task_id=task_id, selectors={"reported_status": ("status",)},
)
```

For factorial/finite/paired models, use existing campaign references. They
already explain `Contrast`, `Coupling`, `factorial`, numeric binding and joint
diagnostics; those methods are not reimplemented here.

`journal.apply_revision(revision, configuration)` checks exact base digest and
before-fields, appends Revision and a new configuration receipt, and leaves old
configuration/endpoints untouched. A `Change(section, path, before, after,
evidence, reason)` belongs to scientific design/preparation/evaluation/budget;
it is not a generic skill text change. `panel.propose_joint_revision(journal,
task_id=..., configuration=..., updated_fields=..., evidence=...,
contrast_ids=...)` additionally requires concrete changes in design and
preparation. Output is proposed_not_promoted; it does not execute the new system.
Graph/charge/topology changes cannot reuse old prepared checkpoints blindly.

`TaskSplit(development, validation, transfer, frozen_initial_configuration, independent_objective)`
records distinct tasks/starting configurations. `journal.policy_version(split,
search_configuration, parent_id=None, development_evidence=())` records a
scientific search configuration; updates need changed search and eligible
development observations. `record_validation` retains a held-out comparison,
not automatic transfer superiority. GroupSplit in transfer is a different
group/family learning contract; do not interchange its role names with TaskSplit.

## Preparation/evaluator panels

`panel.PanelSpec(task_id, role, candidates, preparations, evaluations,
slots_per_candidate, receptor_state_id, coordinate_frame_id,
independent_objective)` declares development/validation role, chemical state
identities, P0/P1 and E0/E1, attempted-slot denominator and fixed objective.
Evaluation definitions name kind/unit/direction/method_id/source_identity.
Only current implemented docking-score or polar-pair readouts are accepted.

`freeze_panel(journal, spec)` appends this definition; `record_cell(journal,
panel_id, candidate=..., preparation=..., evaluation=..., slots=...,
execution_ids=...)` consumes admitted Observation IDs with actual slot/pose/
mapping identities, not caller-supplied invented score values.
`summarize_panel(journal, panel_id)` is a read-only view of complete and absent
cells, finite workflow scores and attempted denominators.

`choose_development_policy(journal, panel_id, losses=..., evidence=...,
loss_definition=..., baseline="P0/E0")` appends a finite proposal from independently
evaluated development losses. Comparing energies across scorers is not that
objective. Incomplete losses retain baseline with
abstain_incomplete_independent_loss; this is not learned improvement-policy phi.
`preparation_contrast(journal, panel_id, candidate_order=(a,b), evaluation=...)`
records a descriptive contrast at a fixed evaluator, retaining absent cells.

`freeze_selection(journal, development_panel_id=..., selection=...,
revision_receipt=..., validation_panel_id=..., validation_request_sha256s=())`
appends exact pre-exposure decision identity and predeclared controls. It refuses
already observed validation panels. `execute_frozen_policies(journal,
decision_sha256)` replays ranking rules on a shared admitted panel and appends
predictions; despite its name it does not run a native producer or read labels.
Cost provenance remains shared, not four independent experiments.

## Optional finite controller

`controller.register_controller(journal, name=..., schedule=...,
source_evidence=..., mode="audit_then_compare")` appends an explicitly requested
hand-authored finite schedule. Each segment has id, stage, depends_on,
request_sha256 and quote_wall_second. No model or general router is created.
`next_segment(journal, controller_id, decision_sha256=None, check_budget=True)`
reads current evidence and may return query, stop_budget, stop_model_challenge,
awaiting_development_decision_freeze or awaiting_independent_feedback.
`record_segment(journal, controller_id, segment_id, admission,
decision_sha256=None)` appends only a matching actual admission and is replay
idempotent. A next segment is not dispatched by either function. The host may
skip this whole controller and use any public method directly.

Concrete isolated builders and behavior checks already exist in
`tests/test_research_journal.py`, `test_research_panel.py` and
`test_research_controller.py`; historical examples remain compatibility callers.
