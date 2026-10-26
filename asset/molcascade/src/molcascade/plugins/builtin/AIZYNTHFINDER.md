# Retained AiZynthFinder routes

`synthesis.aizynthfinder` 0.2.0 preserves the existing `primary` and
`synthesis_scores` outputs and adds two ordinary artifact datasets. The existing
runner validates and commits all four ports atomically; no extra routing service
or execution framework is required.

The catalogue selects `synthesis.aizynthfinder@0.2.0`. Existing pipeline files
that explicitly pin `@0.1.0` need an explicit version update to request retained
routes; their historical artifacts and frozen deployments are unchanged. The
`synthesis_score/v1` schema, numerical convention, and failure/timeout behavior
remain compatible.

| Port / contract | Meaning |
| --- | --- |
| `synthesis_routes` / `synthesis_routes/v1` | One row per parent and search attempt. `record_json` retains every native field, including the complete returned `trees`, reactions, intermediate molecules, stock membership and reaction metadata. |
| `synthesis_search` / `synthesis_search/v1` | One row per attempt containing exact input/config/output/log bytes, the native-output SHA256, command, elapsed process time, input artifact reference and dependency identities. |

“Complete” means all bytes and trees exported by that native invocation. It does
not mean all explored internal search nodes or every chemically possible route.
Native `post_processing.max_routes` and search limits still bound the output.
The shared `search_id` joins targets to the byte-exact source. `target_index` is
the input position; canonical target identity and row count are checked before
any native record is attributed. `SOLVED` means the backend found a route to its
configured stock, not laboratory validation, reagent availability today, yield,
cost, toxicity or proof that the reaction succeeds. `NOT_FOUND_WITHIN_BUDGET`
is conditional failure to find a route, not chemical impossibility. The legacy
score remains the configured sentinel with `ROUTE_NOT_FOUND` in that case.

Supply `dependency_files` as explicit role-to-absolute-path mappings for the
selected policy model, template table, stock, filters, and an actual backend
runtime manifest if available. The adapter hashes the files before and after
execution and refuses changed dependencies. These declarations do not prove
that the caller listed all backend dependencies: `dependency_scope` remains
`explicit_files_only`. An empty mapping is marked `unresolved` and scores carry
`ROUTE_DEPENDENCIES_UNPINNED`. The configuration bytes, declared backend version,
adapter/evidence-writer bytes and executable identity are separately retained.
The adapter neither installs software nor downloads models.

Timeouts, nonzero exit codes, malformed output, target misalignment and dependency
drift still fail the call. They never return a successful `StageResponse`.
Configure `failure_evidence_dir` to a stable caller-owned directory. The existing
runtime error context then carries `route_evidence` with a unique directory,
manifest SHA and explicit `published_artifact=false`. This directory contains
the same two evidence Parquet files and a manifest. Target records are null when
alignment or execution failed; the entire partial native output remains in the
search dataset. Without `failure_evidence_dir`, the retained bundle is under
system temp and explicitly reports `retention=system_temporary`. The host must
archive it before temp cleanup. A failure bundle is evidence, not an artifact
published as successful science. Failures before targets are accepted or the
native attempt begins remain ordinary preflight errors.

Read a successful route using the existing store:

```python
ref = manifest.dataset_ref("synthesis_routes")
root = store.resolve_dataset(ref, verify=True)
rows = pyarrow.parquet.read_table(root / ref.file_paths[0]).to_pylist()
native_record = json.loads(rows[0]["record_json"])
```

Read `synthesis_search` the same way to recover native output bytes. Recovery is
an artifact read and does not rerun search or count as an independent replicate.
Any new search gets a new `search_id`; both successes and failures keep their
own elapsed-time evidence. Environment installation and model acquisition costs
belong in the caller's preparation ledger, separately from process search time.
