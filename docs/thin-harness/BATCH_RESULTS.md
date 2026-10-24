# Batch result archival

`etalon.research.batch_results` is an optional adapter over an already configured
`ResearchJournal`. It archives explicit native receipts, original outputs and
counterevidence, builds bounded indexes, and writes an ordinary `node`
research-step. It submits no scientific work, settles no execution charges,
chooses no method, and makes no adoption decision. There is no scheduler or new
database table. The strings below identify ordinary JSON Artifact formats, not
new kernel record types or mandatory stages.

```python
from etalon.research.batch_results import (
    ingest_batch_results, verify_batch_results, read_batch_results,
)

receipt = ingest_batch_results(
    journal, manifest, archive_directory,
    source_id="snd1-scale-p1-block-001", source_kind="new_execution",
)
audit = verify_batch_results(journal, receipt)
page = read_batch_results(journal, receipt, offset=0, limit=32)
# receipt: manifest_ref, index_ref, summary_ref, step_ref, receipt_path
# page: expected/row/archive descriptors, total, next_offset
```

The caller supplies an existing campaign. Archival-only campaigns may have a
zero budget and an unqueryable archive endpoint. `actions=[]` then means this
journal does not own the native execution cost ledger; it does **not** mean the
scientific computation cost zero. Native receipts remain the cost evidence.
`new_scientific_charge=0` describes the ingestion operation only. Its local CPU,
I/O and storage overhead are unmetered (`ingestion_compute_cost=null`).

## Input

```json
{
  "schema": "etalon-batch-results-input/1",
  "batch_id": "p1-block-001",
  "task_id": "snd1-scale-20261024",
  "question": "Can the frozen method preserve quality at lower cost?",
  "branch_id": "P1",
  "expected_attempts": [
    {
      "request_id": "request-001",
      "attempt_id": "trial-1",
      "required_originals": ["request", "pose_verifier"],
      "required_counterevidence": ["identity_diagnostic"]
    }
  ],
  "rows": [
    {
      "request_id": "request-001",
      "attempt_id": "trial-1",
      "status": "failed",
      "execution_mode": "new_execution",
      "receipt": {"path": "/absolute/receipt.json", "sha256": "<64 lowercase hex>", "bytes": 412},
      "originals": [
        {"name": "request", "path": "/absolute/request.json", "sha256": "<64 lowercase hex>", "bytes": 204}
      ],
      "counterevidence": [
        {"name": "identity_diagnostic", "path": "/absolute/diagnostic.json", "sha256": "<64 lowercase hex>", "bytes": 108}
      ],
      "actual_consumption": {"wall_second": 0.2, "gpu_second": null},
      "identities": {"protocol": "frozen-protocol-id", "chemical_state": "state-id"}
    }
  ],
  "scope_limits": ["Known-system conditional robustness; independent verifier remains missing"]
}
```

Paths must be absolute for the current host. Relocated deliveries should retain
their remote locator in the original receipt and use a locally hash-verified
copy as the input file. The example hashes are placeholders. `scope_limits` is
optional; all other fields shown are required. Files can be arbitrary bytes;
ingestion does not interpret a native receipt as an affinity or validity label.

Every file descriptor may also declare `role`: `diagnostic`,
`independent_validation`, or `sealed_holdout`. The original Artifact preserves
that role. Old descriptors with no role retain the compatibility default
`diagnostic`; this is a default for an undeclared old input, not permission to
relabel confirmation evidence. The optional ingestion argument `role=` sets a
minimum role for every original in that block (for example,
`role="sealed_holdout"` for a confirmation block). A per-file declaration cannot
lower that minimum. A diagnostic execution receipt can remain separate from a
restricted truth/verifier member when no block minimum was requested.

Any independent/holdout member raises the evidence role of the full input,
chunks, indexes, summary and research-step. Restricted originals and derived
Artifacts default to `visibility="sealed"`, so even confirmation counts and
file locators do not become development-visible via the batch index. A normal
development reader cannot access these records, and `Experiment.inputs` still
rejects independent/holdout evidence as diagnostic/design input. This API never
releases a confirmation set. The host's separate frozen-output evaluation and
explicit evidence release must establish later visibility; raw journal-owner
audit methods remain privileged access. Hosts must explicitly label all
confirmation-bearing originals or set the block minimum; the adapter does not
guess evidence roles from filenames or result values.

`expected_attempts` freezes the full block denominator. Every `(request_id,
attempt_id)` is unique; rows may be missing, but extra/duplicate rows are rejected.
`required_originals` and `required_counterevidence` are explicit member names per
attempt. A missing required member makes coverage `incomplete`, including when
the name was removed from a row. The input inventory itself must come from the
host's frozen panel/protocol: this module cannot discover undisclosed evidence
or prove that the inventory was declared before outcomes were seen.

Statuses are `completed`, `failed`, `not_applicable`, `censored`, and `unresolved`.
Unreported expected attempts are counted separately as `missing`. Every supplied
row except `unresolved` requires an original receipt. N/A is retained as N/A;
it is neither a failed optimization nor an assumed zero-cost observation.
`receipt=null` is allowed only for `unresolved`; it never grants a retry.

Execution modes are `new_execution`, `historical_replay`, `cache_hit`, and
`not_executed`. `completed`/`failed` cannot use `not_executed`. Use distinct
attempt identities for explicitly authorized independent repeats; an interrupted
attempt keeps its identity. Hosts map producer statuses and identities and must
check their scientific meaning; byte verification cannot certify those claims.

Consumption values are finite nonnegative numbers or `null`. Each unit is
reported separately, with known sums, known/unknown row counts and a nullable
total, separately for each execution mode. A unit absent in one row is unknown,
even if another row measured it. No values are imputed, unlike units are never
added, and sums of per-action wall time are not described as concurrent elapsed
time or full research cost. No consumption fields means undeclared consumption,
not free computation. Missing attempts retain unknown cost. Replayed historical
consumption is historical attribution, not new physical spending.

## Recovery and audit

The existing publication file primitives provide durable copies, immutable
intents and content-addressed originals. Each original is checked and recorded
through `ResearchJournal.record(Artifact)`. Reusing the same campaign, directory,
`batch_id`, source and manifest returns the same receipt without new journal
events. A changed manifest conflicts before new DB writes. Saved original bytes
can be used after their producer files disappear. Earlier successful appends
survive a process crash; retry archives only missing records. Failures remain in
append-only `failures/` files after recovery.

The idempotence scope is this immutable archival operation. It is not universal
scientific exactly-once execution, a cross-directory ingestion registry, or a
cross-block deduplicating statistician. Hosts keep block IDs stable across
recovery, identify overlapping attempts when aggregating multiple blocks, and
use a new block ID for later results. The adapter never retries a native job with
an ambiguous outcome.

Chunks contain at most 32 expected attempts and target at most 256 KiB. Index
pages contain at most 32 chunk descriptors. The root index binds every page,
chunk order, count and full manifest; the summary binds that index and manifest.
Full originals stay separate. Lists within rows are bounded at 64 named members;
oversized rows must link a separate original. Existing journal, evidence-reader,
evaluation and continuation byte/count limits are unchanged. Very large panels
still need separately scoped source snapshots under those existing limits.

`verify_batch_results` rechecks the immutable intent, manifest, every index/page/
chunk, every archived original, row identity and required-member inventory. It
recomputes all status/cost/coverage counts and checks the research-step links.
A changed late-page counterexample fails full audit even if the summary and
first page remain intact. `read_batch_results` verifies returned originals and
index/chunk bytes; its result explicitly does not claim a full-original audit.

New `/2` indexes also bind each page's attempt offset/count. Before selecting a
range, the reader validates the entire root directory's consecutive chunk and
attempt ranges and their totals. It opens only overlapping index pages and chunk
payloads, checking the page's complete chunk directory against its root span and
the selected chunk's exact ordinal/offset/count. Every returned row retains its
expected required-member inventory, and every supplied original and
counterevidence member is checked against its exact Artifact and current bytes.
No mtime or earlier audit substitutes for this current hash verification.
An unseen chunk is covered by the explicit full audit, not by a page result.
`range_coverage` counts the returned frozen expected inventory separately from
its supplied rows, including missing required originals and counterevidence.
An absent required member remains `incomplete`; the reader never infers the
required inventory from only the row's supplied files.

`next_cursor` carries `batch_version` and the next offset. Pass it as `cursor=`
on the following read; an altered campaign/intent/index/manifest rejects the old
cursor. `next_offset` remains available to old callers. A cursor and a nonzero
explicit offset cannot be combined. `/1` indexes remain readable: the reader
opens all small index pages to validate their global chunk directory, then only
the chunk payloads overlapping the requested attempt range. Both formats retain
the same full-audit coverage. Ordinary navigation verifies the small durable
receipt and its exact Artifact refs. Full current intent/manifest byte checking
is explicit full-audit work, outside the page result's stated scope; it never
substitutes for selected original/counterevidence hashes. Old receipts without
the default-role field still read the original intent to preserve its role
minimum. No old evidence is silently downgraded for a faster compatibility path.

Native execution handoff is separate from archival retry. The optional
[`execution_recovery`](EXECUTION_RECOVERY.md) helper can inspect an already
running native attempt or publish its completed receipts without re-execution.

`coverage=complete` means all expected attempts have a terminal/censored receipt
and their declared required members. A complete inventory can contain scientific
failures. It says nothing about scientific success, convergence, validity,
independence or adoption. Missing an independent verifier remains incomplete.

## Existing readers, evaluation, experience and publication

These are generic Artifacts. Their full indexes use exact refs that ordinary
`read_scope`/`read_evidence` readers can follow with existing byte pagination.
The local `read_batch_results` helper is for a journal owner; published-source
visibility is enforced by the existing evidence APIs. Generic payloads are not
silently projected into every continuation context. The checkpoint can retain
`step_ref`, the summary as a branch basis, and `index_ref` in `next_read_refs`/
`counter_refs`; the host then pages the detailed inventory explicitly.

The summary is small enough for an existing `evaluation_contract` result input.
Declare inventory checks (e.g. coverage, expected denominator, missing required
counterevidence) explicitly. Scientific aggregates, intervals, equivalence
thresholds and status interpretation belong to external frozen statistical
code and existing contracts. Full batch audit and original verifier obligations
remain separate from numeric rule replay. An evaluation of the summary alone
does not independently recheck every underlying scientific calculation.

The resulting refs may be used by existing `improvement` opportunity/patch/
evaluation/experience APIs. No opportunity or experience is automatically
created, and there is no automatic adoption or reopening of terminal candidates.
The host still supplies the particular defect, prediction, falsifier, comparison
and supported scope. An archival failure is not evidence for a scientific method
revision.

Use the existing `record_content` checkpoint writer and an explicitly scoped
snapshot/workspace to publish a block. A snapshot containing an earlier source's
records should use `record_ids` to publish only the new source's intended records;
do not duplicate old checkpoints under a new source ID or replace an old frozen
source's identity. A new research task starts its own checkpoint. It cannot be
published as a continuation of an unrelated paused task.

`prepare_publication` preserves the parent task and adds its own isolated source;
it cannot silently add arbitrary external sources to an existing parent. Include
the batch source in an explicit staging workspace, then publish a new destination
entry from that staging parent, or publish a small set of files with the existing
`publication_ref` aliases. Publication remains responsible for its durable stages
and entry compare-and-swap. Host entry aliases need their own explicit CAS;
the batch adapter does not move them.

## Validation and current integration

`tests/test_batch_results.py` covers real process death after durable original
copies, producer disappearance, zero duplicate record events, immutable request
conflicts, missing required checks/counters, late counterexample mutation,
failed/N/A/pending denominators, unknown cost, chunk pagination and the existing
checkpoint → publication → continuation → evaluation chain. All scientific data
in those tests are labelled fixtures.

Additional role tests preserve both independent-validation and sealed-holdout
originals, reject them and their summaries as diagnostic experiment inputs,
and confirm that a development evidence reader cannot retrieve the derived
summary/index/manifest/step.

A 2026-10-24 generic fixture measurement uses the unchanged production limits:
4,097 expected attempts, 97 supplied rows, 129 chunks and five index pages. A
one-row read at offset 4,096 opens one root, one index page, one chunk, the small
receipt and that row's two current originals: six files and 15,108 Python-level
bytes read. A reference traversal of all chunk payloads with the same selected
original checks reads 3,487,036 bytes across 138 files (bounded/reference ratio
0.00433). This counts repeated reads for hashing and parsing; SQLite C-level I/O,
OS cache and physical disk traffic are excluded. It is a fixture I/O result,
not a scientific accuracy or end-to-end cost improvement. The explicit full
audit still verifies all 4,097 expected attempts and retains 4,000 missing rows
and their missing required counterevidence. The measured source hash and
per-file byte inventory are in the separate integration `continuation/BATCH_IO.json`.

The separate 2026-10-24 integration under
`integration/etalon-snd1-scale-run-20261024/harness/` uses two **actual new** P1
native receipts: fixed-core completion and an identity-precheck failure. It
preserves their exact request/protocol/freeze/output bytes and measured cost.
The missing independent verifier is explicit; it is not a scientific quality
pass. The baseline entry stays separate from the new published entry, and the
old SND1 task and R4b remain paused. These local archival/publication checks do
not establish native hook delivery, actual context compression or scientific
improvement; those require their own host/scientific evidence.
