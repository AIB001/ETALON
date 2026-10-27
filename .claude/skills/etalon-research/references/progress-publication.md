# Continue a note

Use the task's bound interpreter and existing entry:

```sh
python -m etalon research current --entry ENTRY.json
python -m etalon research detail --entry ENTRY.json --branch BRANCH --record-id RECORD --select /counterevidence
python -m etalon research progress --entry ENTRY.json --patch PATCH.json --expected-record RECORD
```

Use delivered knowledge, completed outputs and pending handles to choose the next
useful authorized action. Reuse a completed result when its identity and conditions
still apply; do not recompute after a recording failure. Run a known method and its
understood conditional followups as one code batch until new evidence needs
judgment. Keep learned conditions, informative failures and next opportunities in
the task note; reusable operators belong in code and improvement procedures in
skills. A negative/no-change result updates the choice: continue with the current
method, try a different operation or observation, or stop under the actual task
conditions. A finished phase or unchanged receipt does not complete the goal.

Read named omitted fields only when they could change that choice. Add
`--verify-originals` to current or detail to check cited bytes without dumping the
whole note; verification does not deliver original bodies. Follow a relevant
original reference when its contents matter. `--view full` explicitly requests the
whole note. A counter count is not delivery of the counterargument.

For a published callable or saved calculation, use its exact note pointer:

```sh
python -m etalon research method --entry ENTRY.json --pointer /method_choices/0
python -m etalon research original --entry ENTRY.json --pointer /result_read/source
```

The first resolves a declared source and call contract without executing or
importing it. Call the named function in the task's bound environment; its input
definition and scope determine whether it fits. The second gives original-file
metadata; `--selector SELECTOR.json` reads named JSON fields or NPZ slices.
Pointers above are examples, not required field names or a registration format.
Existing `/feedback/reusable_methods/0` or other declared pointers work as well.
When publishing a callable, include the actual interpreter, working directory,
required environment and ready argv when they affect execution; keep full runtime
manifests behind a pin. Placeholders that require another source search are not
a runnable handoff. Link the completed result separately so continuation can read
it without calling the method again.
For numeric analysis, use `notes.open_original` in one Python batch: it verifies
the file and caches selected NPZ members for repeated array access. Feed arrays
to the calculation and return differences, decisions and exceptions; leave full
arrays on disk. [Reading examples](../../etalon-research-context/references/current-reading.md)
show the exact calls. No separate method approval or second receipt traversal is
needed for an unchanged applicable method within the active task's authority.

`PATCH.json` contains changed top-level fields, for example
`{"summary":"The new case reverses the sign.","next_action":"Compare boundary conditions."}`.
Omitted knowledge, references, conditions and pauses remain; a supplied nested
object replaces that field. Use the returned record for `--expected-record`.
`--branch` selects a branch, `--background` retains the active branch, and `--note`
accepts a complete note or new branch. A producer's stable `--publication-id`
preserves later interpretations on replay. Cite immutable outputs. The command
handles portable writable continuation through the same journal.
Append new originals with `--evidence RESULT.json` or `--counterevidence CHECK.json`
(repeat for more files). Existing references remain; the command computes their
identities through the existing original-file API. No manual hash assembly or
readback of the old evidence array is needed.

Known SDK callers can use `notes.read_current` and `notes.save_progress` directly.
For SDK note construction, an existing producer/lifecycle, native job handle or
graph publication, open only its named section in
[advanced publication](progress-advanced.md).
