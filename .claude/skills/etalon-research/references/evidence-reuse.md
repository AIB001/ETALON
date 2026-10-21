# Published evidence, read on demand

Use a known file, SDK call or EvidenceRef directly when it answers the question.
Querying, reading this skill and recording a method are optional. A project can
point to `etalon-workspace.json` in a short entry without loading prior chats,
all source code or the entire evidence archive. The manifest locates published
sources; it does not choose the task, method or next action.

## Bounded SDK reads

The workspace argument is an existing manifest path or validated workspace
object. Relative locations are resolved against that manifest's directory.

```python
from etalon.research.evidence import query_evidence, read_evidence

# Optional lookup: request only one page and inspect coverage/warnings as well
# as items. These functions do not execute experiments or register methods.
page = query_evidence(workspace, query="preparation", limit=5)
# When an item is relevant, its ref can be saved and read in another chat.
if page["items"]:
    record = read_evidence(workspace, page["items"][0]["ref"], view="record")

# A known ref needs no preceding query. Keep the source_id and record_digest.
record = read_evidence(workspace, known_ref, view="record")
lineage = read_evidence(workspace, known_ref, view="lineage", limit=10)
```

The variables `workspace` and `known_ref` come from the project's published
manifest/ref; they are not fabricated IDs. `examples/evidence_reuse.py` in the
source distribution is a CLI for an explicit workspace and one query or known
ref. Examples are source-delivered, not installed wheel commands. The installed
SDK remains directly callable.

Query filters include `source_ids`, `record_types`, `task_ids`, `entity_ids`,
`method_refs`, `statuses` and `roles`. Unknown filters are errors. Query pages
are limited to 1..50 items. A response's `coverage`, `warnings`, verification
and availability qualify the findings: no match is different from an unreadable
source. Ask for `next_cursor` only when another page would help. If the query or
source watermark changes, a stale cursor needs a fresh query; do not splice
unrelated pages together or silently retry until a desired result appears.

`read_evidence` offers `record`, `summary`, `lineage` and `artifact` views, with
optional `member`, `selector`, `offset` and `limit`. Read the record to identify
the exact member/selector before asking for original bytes when those locations
are unknown. Metadata alone does not prove original-file verification: inspect
the returned verification/projection fields. A bounded
fragment and a verified whole-file digest are separate results. Inspect missing,
truncated or unavailable information before treating a page as complete.
The current artifact selector is `"bytes"`; artifact fragments are base64 with
byte offsets, total byte count and `next_offset`. Decode only the returned
fragment; whole-file verification is reported separately. Lineage returns one
bounded page of direct published relationships, not a recursive history dump.
MCP `etalon_evidence_query` and `etalon_evidence_read` expose the same semantics;
neither is a prerequisite for other tools.

## Reuse without changing what the evidence means

### Ordinary stored revision and claim content

An ordinary `record_method_revision` call writes the typed JSON and registers an
Artifact with `bindings.schema="etalon-method-revision/1"`. It does not put the
change summary into the record envelope. A claim follows the same ordinary
file-first, Artifact-second pattern, using `validate_claim` and
`bindings.schema="etalon-research-claim/1"`. The source example's optional
`record_claim` function shows this pattern; it accepts the caller's content and
source_kind, and does not invent a claim, method attribution or expected answer.

```python
from etalon.research.method_revision import record_method_revision

# journal and revision_payload are existing, caller-chosen objects with real refs.
revision_id = record_method_revision(
    journal, revision_payload, revision_directory,
    source_id="published-project", source_kind="new_execution",
)
# In examples/evidence_reuse.py:
claim_id = record_claim(journal, claim_payload, claim_directory,
                       source_kind="new_execution")
```

Use the actual source kind; writing a JSON file does not itself establish a new
scientific execution. The helpers are optional, and claim validation establishes
the content shape rather than its truth or scientific admission.

Publish only explicitly selected, safe-to-expose sources. The optional
`publish_journals` example uses a read-only SQLite backup to make fixed copies
of named existing journals and an `etalon-evidence-workspace/1` manifest. It
preserves record bodies, original file paths and IDs. It writes no search text,
expected answer, index or custom projection. Do not pass sealed journals: the
snapshot is a file publication, not a security filter. Original Artifact files
must remain available or have an explicit verified location_map.

```bash
python examples/evidence_reuse.py --publish-to /new/published \
    --workspace-id project-evidence --journal published-project=/existing/research.sqlite

# A separate process can receive just this manifest and the actual topic.
python examples/evidence_reuse.py --workspace /new/published/etalon-workspace.json \
    --query "the current research topic"
```

For schema-marked published Artifacts, the evidence reader verifies original
SHA256/byte count and bounded known-schema JSON before deriving temporary
search and relationship views. The returned ref still identifies the original
Artifact, not a new projection record. Read the discovered ref with `view=record`
for unchanged metadata, `view=lineage` for original relationships and
`view=artifact` for verified original bytes. Missing/corrupt content must be
reported as unavailable or a verification error, never as a successful empty
interpretation. Finding a record, verifying its original and using that evidence
in an answer are separate observations. No skill adoption or answer quality is
inferred from these SDK calls.

Preserve `source_id`, `record_id`, `record_digest`, and any original member,
selector, SHA256 and byte count. Content identity, the source-bound record and
its current location are separate. Equal bytes from two sources do not make
them independent repeats, give them the same scientific role or merge costs.
For relocated originals use an explicit, verified `location_map`; keep the old
record intact. Missing bytes or a wrong mapping remain failed verification.

For cross-campaign writing, a foreign record ID is not a local journal record.
Use an explicit provenance Artifact containing the original ref, hash, role,
current visibility and reuse type before creating local relationships. A local
MethodVersion can bind the complete verified external snapshot and retain its
source. A new local record ID alone is not a new algorithm. Referencing historical
costs does not charge them again; actual new analysis costs are separate.

Previously released independent validation is seen evidence in the new task.
Retain its original role and release lineage, and never relabel it as a fresh
holdout. Query role filters are not a security boundary. Sealed originals and
their summaries/index fragments must remain outside the published view and the
execution host's readable inputs. Project status references identify current
pauses; finding an old successful receipt does not authorize resuming a batch.

When recording an interpretation, the optional `etalon-research-claim/1` JSON
Artifact links the question, subjects/conditions, statement, epistemic status,
support and counter refs, limitations, method refs and superseded claims.
`extracted_report`, `hypothesis` and `interpretation` distinguish meanings,
not confidence scores or scientific admission. Keep literature reports,
calculations and experiments distinguishable. Append a revised interpretation
with counterevidence; do not edit the old fact or replace unknown values by zero.

## Honest use attribution

An available guide is not necessarily read, and a read guide is not necessarily
applied. A host may skip the skill, use one reference, combine methods or alter
an external bundle. When useful, append MethodUse with the actual selected
versions/fragments and reason; `selected_methods=[]` is valid. Current journal
use attribution is `host_reported`. Preserve native read/call/output traces
separately as evidence, without inventing `tool_observed` telemetry. Method
recording is not a gate for retrieval or action. Quality, costs and independent
benefit require their own subsequent evidence.
