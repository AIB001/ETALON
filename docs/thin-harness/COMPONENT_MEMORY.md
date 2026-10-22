# Component, method and workflow memory

This optional layer connects existing component outputs to `research-step` and
checkpoints. It introduces no executor, scheduler, new scientific ledger or
automatic RSI loop. Existing direct SDK calls remain valid.

## Existing interfaces and the new seam

| Owner | Existing producer/consumer | New read-to-memory seam | Boundary |
| --- | --- | --- | --- |
| MolQuarry | `data.service.plan_data/run_data`; sealed `data.artifacts.read_snapshot` | `inspect_component("molquarry.data", snapshot)` → original files, coverage, usage, source identity | Query/collect/download/catalog/sourcing/bundle; sourcing is not procurement or synthesis execution |
| ETALON | `data.library.prepare_library/import_candidates` | `inspect_component("etalon.library", snapshot)` → source/candidate mapping | This conversion belongs to the host integration, not a second MolQuarry implementation |
| MolCascade | `Screen.plan/run/read/recall/metrics/export_shortlist/handoff` | `inspect_component("molcascade.screen", workspace, run_id=...)` → native run status and verified committed manifests/files | Completed, exhausted, failed and nonterminal states remain distinguishable; no new run |
| PRISM | Producer-owned `prism-design-candidate-bundle/1`; consumer `data.design.read_bundle/import_bundle/verify_import` | `inspect_component("prism.design", bundle_json)` → all slots, raw/processed provenance, molecule/state/pose identities | Reads producer evidence; no generation, QC rerun, affinity label or scientific admission |
| PRISM execution | Existing restricted `prism_bridge_v1`, fixed-pool/preparation consumers and runtime action handles | Explicit node refs can point to their original handles and receipts | No new dispatch adapter; contact-count bridge, fixed-pool and old MM-PBSA semantics are not unified; general FEP/lab remains disconnected |

`components.capabilities()` is a bounded installed catalog. It names owners,
entrypoints, skills and integration limitations; availability in the current
execution environment must still be checked through the relevant component.
Only three component readers plus the ETALON library reader are added here.
No upstream wire schema changes; PRISM production remains in its own repository.

## Files → node → memory

```python
from etalon.research.components import inspect_component, record_component_step

# Pure read: returns native evidence and identity checks, creates nothing.
inspection = inspect_component("molcascade.screen", screen_workspace, run_id=run_id)

# Explicit append to an existing ResearchJournal; never calls Screen.run.
recorded = record_component_step(
    journal, capability_id="molcascade.screen", location=screen_workspace,
    run_id=run_id, directory=memory_directory, source_id="research",
    source_kind="existing_real_artifact", task_id=task_id, question=question,
    branch_id=branch_id, node_id="screen", reason="Existing run already answers this step",
    input_refs=prior_refs, workflow_ref=workflow_ref,
    method_refs=method_version_refs, use_refs=method_use_refs,
)
```

The helper verifies existing bytes, records original-file Artifacts and an
ordinary read receipt, then appends a typed node step. Repeating the same read
and attribution produces identical record IDs and no extra events or charges.
Changed inputs yield a new record or a verification failure, never overwrite.
Files and Journal records are separate commits: an interrupted append can leave
verified intermediate Artifacts; replay completes the append idempotently.

MolCascade's mutable status file is copied as exact bytes to an immutable
content-addressed snapshot. Its original path/hash and historical-state meaning
are preserved. Committed immutable payloads remain references to original
files. A historical waiting state is not a heartbeat; live status still uses the
existing component interface. Missing sources raise errors, not empty successes.

The ordinary component receipt retains complete native identities and failures;
the step carries a bounded identity summary with basis refs. A read links at
most 60 files, and the step summarizes at most 32 identities. Larger components
need a smaller explicit published view. Neither molecular identity nor matching
bytes alone proves two observations have the same endpoint or protocol.

The ordinary receipt is read with `read_evidence(view="artifact")` and its
base64 fragments. `read_content` is only for typed claim/step/checkpoint/workflow
content. Original component files are linked from the step, so checkpoint
continuation verifies their hashes without decoding arbitrary result schemas.

## Methods and workflow nodes

Reuse `MethodVersion`, complete `skills.persist_snapshot/verify_snapshot`,
`MethodUse`, and `ResearchJournal.record`. The six native MolQuarry bundles stay
under MolQuarry. Cross-component selection/composition instructions belong to
ETALON. Pure SDK functions need no extra SKILL file.

Availability, selection, reading and use are distinct. A node's `method_refs`
declare exact methods; `detail.use_refs` refer to existing MethodUse records.
Those uses retain the original subject enum (task/request/action/result), and
must include the node's task. Partial adoption uses the existing selected-method
scope. Empty methods are valid. `tool_observed` remains rejected without actual
telemetry; this layer does not invent stronger attribution.

`etalon-research-workflow/1` is ordinary immutable JSON with:

- `name`, `scope`, `method_refs`, `supersedes`;
- `nodes`: `node_id`, `capability_id` (or null for host work), `method_refs`,
  `views` (E/J/X/R), `purpose`, `skip_when`;
- `edges`: `from`, `to`, `kind` (data/support/control), `condition`.

Store it with `continuation.record_content`. A workflow can itself be the
content/evidence of an existing MethodVersion. It does not require all nodes to
run or claim a complete global DAG. Control feedback is a relationship, not a
new automatic scheduling rule. To switch an allowed method, append a new
workflow definition/version or record a standalone node; old nodes retain their
exact bindings. The four scientific views share these same references.

`composition.node_step` supports planned/completed/reused/skipped/failed/waiting/
partial states. Planned/skipped nodes cannot contain outputs, execution receipts,
action handles or applied MethodUse records. Failed/partial/waiting states need
actual receipts or handles. Completed/reused states require existing outputs
and receipts. These conditions validate records, not scientific success.
Branch decisions continue using the existing decision step and its alternatives,
basis refs and reconsideration condition.

## Scoped retrieval and new chats

Existing `query_evidence` and `etalon_evidence_query` accept `branch_ids`,
`node_ids`, `capability_ids`, `workflow_refs` (record IDs), `evaluation_targets`
and node `statuses`, alongside existing source/task/method filters. Filtering
does not expand the requested source scope except for necessary explicit
dependencies. `read_evidence` exposes original bytes and lineage; workflows are
projected after type and hash verification. Claim subject names are scientific
entities, not MethodEvaluation record dependencies.

A checkpoint references the relevant node steps. A fresh process follows their
workflow/method/use/receipt/file refs and current authority. It does not rerun a
component or awaken stopped branches. No new MCP execution tool is introduced;
the same read-only query/read tools expose these additions.

## Evaluation entry points for future RSI

An `evaluation_plan` step binds:

| Target | Versioned subject/comparator | Interpretation |
| --- | --- | --- |
| `method` | domain MethodVersions | Small method S |
| `branch_selection` | usage MethodVersions | Decide whether to read, branch, skip or stop |
| `workflow` | MethodVersions representing composite methods | Whole route; attribute its change to S/U according to semantics |
| `workflow_composition` | usage MethodVersions | Rule U that assembles the route |

Each plan contains concrete `trigger_refs`, `subjects`, `comparators`,
`protocol_ref`, `task_split_ref`, `initial_conditions_ref`, `use_refs` and
`metrics`. Metric families `regression`, `retention`, `funnel`, `cost` remain
separate; each names its unit, denominator and missing-data policy. A stopped
unmeasured branch is not a negative label. These are comparison specifications,
not a generic numerical scorer or an independent-validation certificate.

`composition.record_evaluation` appends the existing MethodEvaluation and an
evaluation step binding it to the plan. It checks exact subjects, baseline,
protocol, split and starting conditions. Diagnostic/inconclusive results remain
legal. The original independent-validation path still requires pre-release
frozen policies, held-out tasks, original validation receipts, evidence roles
and accounting; arbitrary workflow reports cannot bypass it. Evaluation does
not promote, optimize, change shared defaults, or resume science.

Future RSI can query these plans/results and existing MethodRevision records.
Automatic trigger aggregation/deduplication, candidate generation, broader
golden/evaluation adapters, adoption and rollback policy are not implemented in
this batch. A workflow revision uses existing S/U semantics rather than a new
parallel RSI store. Resource allocation remains outside this layer.

## Reproducible example and validation scope

`examples/research_component_memory.py` takes explicit `--design-bundle`,
`--paused-status`, `--output`, and optional `--released-entry`. It reads the
producer bundle, runs a bounded local catalog and CPU property calculation,
preserves source/candidate mapping, records skipped expensive work and four
comparison plans, then publishes a short checkpoint. Any supplied fixture
provenance remains fixture. Earlier scientific claims are separate references,
not biological labels for these molecules. Failures and their costs are kept.

Tests exercise real local component readers, corruption and identity mismatch,
method/workflow binding, skip/partial/failure states, replay, scoped retrieval,
comparison boundaries and checkpoint recovery. Such engineering evidence is
separate from new docking/MD/lab validation or measured RSI improvements.
