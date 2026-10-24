# Local research handoff

`etalon.research.handoff` exports an explicitly published research entry into a
new local directory. It reuses the existing entry, evidence workspace,
`EvidenceResolver`, checkpoint and publication contracts. It does not connect
to SSH, transfer files over a network, select resources, execute science, or
resume a publication.

```python
from etalon.research.handoff import (
    plan_research_handoff, export_research_handoff,
    verify_research_handoff, relocate_research_handoff,
)

plan = plan_research_handoff("/published/task/entry.json",
    publication_intents=["/published/earlier-failure/intent.json"],
    skill_bundles=[{"slug": "research-index", "manifest_sha256": "<exact published SHA256>",
                    "root": "/original-export/etalon-research-index"}],
    limits={"max_files": 6000, "max_bytes": 128 * 1024 * 1024})
# Review plan["counts"], plan["originals"], plan["publication_history"],
# plan["continuation_at_export"] and plan["workspace"]["location_map"].
receipt = export_research_handoff(plan, "/new/handoff")
verify_research_handoff("/new/handoff")  # checks both bundle and originals
relocate_research_handoff("/new/handoff", "/another/new/handoff")
```

The entry may be a file or the existing one-hop locator file. A read-context
receipt, including one serialized into a file, is rejected: exporting its
underlying unfiltered workspace would discard its policy. This API is for an
owner of the explicit original files. It copies source bytes, including their
historical contents; it is not a redaction, disclosure or access-control API.
The caller must select an appropriate published workspace before export.
An entry selector may traverse a deployment's `current` symlink: its resolved
target and bytes are pinned, and later retargeting invalidates the plan. Symlinks
are not copied into the archive or accepted as output destinations.

## Identities and scope

Planning checks every selected source, all published records, every Artifact,
published data members, existing location maps, saved method skill bundles and
the selected checkpoint's authority files. Sources must use
`snapshot_sha256`; a mutable journal must first be published using the existing
publication API. Incomplete source coverage or missing/corrupt originals fails
closed. All records in the workspace are retained, including counterevidence,
failed receipts, branch dispositions, costs and null unknowns. Nothing is
selected by whether it supports the current conclusion.

Source IDs, record IDs/digests, source snapshot bytes, Artifact bytes and
checkpoint content are unchanged. Only the new workspace's source locations
and explicit `location_map` / `skill_bundle_map` change. Relative package paths permit relocation
between directories and operating systems without rewriting scientific
records. Original foreign paths still need a verified local mapping before
planning; this API cannot fetch an inaccessible server path.

Published workset Artifacts are inspected for exact skill slug/hash references,
including resources that were excluded during adaptation. Full bundle members
are retained, not just the members enabled in the workset. The optional bounded
`skill_bundles` argument locates an exact old version using an explicit absolute
root. Resolution uses that override first, then an existing workspace mapping,
then a saved MethodVersion bundle snapshot, then the original bundle reference
or installed library only when its complete manifest hash matches. Missing
versions, corrupt mappings and unexpected supporting files fail closed; a newer
installed skill never substitutes for the published version.

The generated workspace declares `skill_bundle_map` entries with
`{slug, manifest_sha256, root: "skills/<digest>/bundle"}`. These are locations,
not adoption decisions. Workset and checkpoint bytes/digests stay unchanged.
The handoff plan records each full semantic bundle manifest and every member's
original SHA256/size. Source membership is rechecked before copying, and bundle
verification checks both all mapped bytes and closure of all published workset
skill references. An older package missing these skill bytes is rejected with
an instruction to re-export from its original entry; intact scientific
Artifacts alone do not establish a complete research handoff.

The plan contains the original entry/workspace bytes, every original file's
SHA256 and size, source/record/checkpoint digests, the exact copy inventory and
the observations from `resume_research`. Its digest is an integrity checksum,
not authentication or execution permission. Bounds fail rather than truncate:
64 sources, 100,000 published records, 10,000 copy targets/original files,
512 MiB of copied payload and 128 linked publication operations. Callers can
lower these limits. Existing entry/workspace reader limits also apply; the
handoff manifest is bounded to 16 MiB.

## Publication history

Discovery follows only the entry's publication pointer, its explicit sidecar
index, publication parent-entry/correction/replacement links, and optional
`publication_intents`. It does not scan unrelated directories for operations.
An unlinked failure must be supplied explicitly; the report records this
discovery scope rather than claiming a global history census.

Every discovered operation directory is copied, including SQLite journals,
partial results, stage receipts, terminal dispositions and other binary files.
Original metadata remains byte-identical and its observed status is retained.
A pending or failed publication may be archived without retrying or erasing
it. File changes or new/deleted members between planning and copying invalidate
the plan.

The new entry exposes `publication_history` with `archive_only=True`. It has
neither an active `publication` pointer nor a publication sidecar. Archived
intent bytes contain their historical original paths; they are evidence, not
retry handles for the new entry. The SDK never calls `resume_publication` on
them. Further copies use `relocate_research_handoff`; exporting an already
archived handoff as a fresh publication is rejected.

Legacy deployment entries with `publication.archive_only=True` and an explicit
`publication_history` can be repackaged. Their declared `archived_intent` files
must match `intent_identity`. Only those declared archive directories are
copied; the SDK never follows their foreign original request paths or calls the
live publication inspector. Old status/phase fields are labelled prior-export
declarations. `history_coverage` states that this preserves the available
legacy archive bytes, without implying that an external failure archive or
missing original operation journal was included.

## Pause and verification boundaries

Selected status references must match unchanged `paused` or `stopped` guards.
An active guard cannot silently become a copied execution authority. Guards
are copied to separate task-scoped locations, and changing these copied bytes
invalidates ordinary continuation checks. Historical checkpoints retain their
original content; this does not assert that every historical control file is
still live or unchanged.

Copied authority files are a frozen handoff state, not a live connection to the
old task's controls. Every receipt retains `execution_permission=not_granted`
and `automatic_retry=False`. Migration grants no new execution authorization.

`verify_research_handoff(directory)` checks the full bundle and pinned original
files by default. After moving to a host without originals, explicit
`verify_originals=False` verifies the package's file/record/checkpoint identities
and returns `status=bundle_verified`, `originals=not_checked` and
`verification_scope=exported_bundle_only`. It does not claim original-server
verification. `relocate_research_handoff` uses this explicit offline scope;
call default verification separately when originals remain accessible.
Receipts also retain continuation `issues`, `warnings` and `pending` entries.
For example, an existing bounded reference-walk truncation is not repaired by
copying all source bytes: a verified archival identity is not a claim of a
complete continuation graph walk or permission to proceed.

All targets must be new directories. Existing files/tasks are never merged or
overwritten. Redirected archive/output paths and path traversal are rejected.
A failed copy retains its partial directory for diagnosis; it is not silently
cleaned up or retried. `handoff.json` is written only after copying and final
original verification. Verification still checks all its bytes and semantic
identities; marker existence alone is not proof of completion.

These checks demonstrate lossless archival engineering. They do not establish
scientific correctness, independent replication, transfer benefit or RSI gain.
