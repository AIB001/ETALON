# Archive a completed research branch

`examples/research_closeout.py` is an optional example that joins existing
`ResearchJournal`, artifact, research-step, checkpoint and publication APIs.
It does not discover jobs, schedule calculations, grant execution permission or
turn a producer's success exit into a scientific claim.

The host supplies an explicit task and branch list, selected historical entries,
and a producer-reviewed packet. Every packet includes pinned protocol, result,
execution receipt and counterevidence files. Accounting and detailed manifests
are optional additional artifacts. The producer remains responsible for native
completion, raw-artifact verification, scientific QC and the scope of claims.

```sh
python examples/research_closeout.py bootstrap /path/to/task --input config.json
python examples/research_closeout.py prepare /path/to/task/entry.json --input packet.json
python examples/research_closeout.py resume /path/to/returned/intent.json
```

Bootstrap binds its configuration before writing a journal or snapshot. An
interrupted bootstrap can resume with the identical configuration; a changed
configuration is rejected. A success receipt requires that the checkpoint can
actually be read from the published snapshot. Historical research entries remain
separately scoped navigation targets, preserving their original status and
authority without conflating a frozen migration checkpoint with live science.

Packet identity selects one publication operation. The ordinary node records
verified outputs and native receipts, and a separate decision supplies the
explicit, evidence-bound branch update. The existing publication API copies and
verifies bytes, snapshots the journal, and replaces the entry using its expected
identity. Use the saved intent to recover after interruption. Replaying it neither
starts science nor opens another proposal. A concurrent entry change is reported
as a conflict, never silently overwritten.

Results are an **as-of observation**, not a heartbeat. A resolved branch means its
declared archival question is answered; it does not mean affinity, convergence or
generalization has been demonstrated. An empty list of runtime handles is not an
inventory of external processes. Legacy native receipts stay receipts, without
inventing ETALON execution handles. Other branches, guards and negative evidence
survive a publication unchanged.

Independent scientific timing and journal timing also remain distinct. If a
protocol was frozen outside the journal before execution, preserve that evidence;
later archival recording must not be backdated to satisfy a prospective journal
contract. A failed candidate is still a useful result: record its observed costs,
regressions, missing comparisons and conditional reasons for reopening. Use the
existing method/evaluation/experience interfaces when those records exist, and
link their scoped entry instead of manufacturing a second RSI state machine.

Readers start from the mutable task entry, select a branch or exact references,
and follow the existing bounded context/evidence APIs. They must distinguish
navigation excerpts from complete evidence and retain counterevidence coverage
reports. This example adds no mandatory workflow or global resource allocator.
