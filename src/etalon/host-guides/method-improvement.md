# Optional method changes and honest attribution

The host can change, combine or ignore methods within the current task's
authority. S denotes a scientific method, U a usage/selection method, and G an
improvement method. These are descriptive roles, not fixed agents or a router.
Historical scientific policies named phi retain their original meaning.

The `skill-improvement` bundle offers optional references for bounded revisions,
selection/composition and self-revision. A known SDK or explicit external bundle
can be used without catalog registration, promotion or this skill. Keep complete
old and new bundle bytes when binding a version to recorded work. Changing a
source bundle must not silently change a running task's bound snapshot.

For optional attribution, use existing MethodVersion, MethodUse and
MethodEvaluation records. A method's parents describe the target's ancestry;
the `etalon-method-revision/1` Artifact separately identifies the producer G,
its use, problem evidence, candidates, trials, costs and limitations. A justified
no-change has an empty candidate list and preserves the attempt's costs.

| Host behavior | What a truthful optional record can say |
| --- | --- |
| Direct SDK; no method selected | `selected_methods=[]`; actual reason |
| One fragment used | Selected version and exact fragment in `scope` |
| Several methods combined | Multiple selected version refs and their scopes |
| Available/read method skipped | Empty selection or later stopping record; do not label the read as applied |
| External bundle modified | Separate complete snapshot/version; preserve source and target ancestry |

The journal currently accepts `basis="host_reported"` for use attribution.
Preserve actual read/call/output traces separately, and keep unavailable host or
model telemetry null. A method being visible or read does not establish adoption.
Version identity does not establish benefit; that requires subsequent independent
comparison with failed attempts, generation costs and applicability included.

Committed events may inform a bounded next action. The existing
`etalon.host.consume_events` helper consumes one page with a log-bound cursor and
stable request IDs. It is optional, does not poll, and must not make ordinary
method notes recursively trigger improvement. Scientific facts remain committed
if a hook fails. Trial, skip, stop and no-change share the authorized budget;
none resumes a paused scientific batch or changes shared defaults automatically.
