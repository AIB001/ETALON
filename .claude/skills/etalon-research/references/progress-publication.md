# Archive progress and advance the research entry

Use `etalon.research.publication` for completed local outputs and explicit
research steps. Publication copies existing bytes, records them through
ResearchJournal, writes a checkpoint and frozen workspace, then replaces the
entry only if its expected identity still matches. It calls no scientific
producer and triggers no RSI candidate.

1. Read the current entry, checkpoint and any unfinished publications. Reuse
   existing result files and pending handles; a publication failure is not a
   reason to submit another calculation.
2. Prepare an explicit intent with a stable operation ID and expected entry
   identity. Results include absolute path, SHA-256/bytes, source kind, role and
   bindings. Steps use the existing research-step contract. Bind an earlier
   named result or step with `{"publication_ref": "name"}`.
3. Call `resume_publication(intent_path)` to finish the archival operation. On
   interruption, inspect that same intent and resume it. Do not create a new
   operation ID to hide a conflict or missing output.
4. Read the published checkpoint through the entry. State what was reused,
   what was newly recorded, and which scientific question remains unresolved.

```python
from etalon.research.evidence_refs import file_identity
from etalon.research.publication import (
    discover_publications, prepare_publication, resume_publication,
)

pending = discover_publications(entry)  # read-only; errors remain visible
intent = prepare_publication(
    entry, operation_id="archive-comparison-01",
    expected_entry_identity=file_identity(entry),
    results=explicit_results, steps=explicit_steps,
)
published = resume_publication(intent["intent_path"])
```

An entry sidecar (`<entry>.publications.json`) identifies interrupted publication
without scanning historical directories. A selected checkpoint's empty
`pending_handles` means no recorded scientific jobs; it does **not** imply no
unfinished memory publication. Hooks/context expose both states separately.

An entry conflict preserves the saved results and intent. Inspect the newer
checkpoint, reconcile explicitly, and prepare a new operation against the new
entry identity. Never overwrite the newer pointer or merge conclusions silently.
Reusing an operation ID with different content is rejected.

Source artifacts must remain available until copied. The publication mechanism
does not recover a producer's unrecorded output or guarantee power-loss recovery
on every filesystem. Existing evidence is archival reuse, not an independent
observation. Keep pauses, permissions, branches and pending jobs explicit.

See `docs/thin-harness/RESEARCH_PUBLICATION.md` for the supported checkpoint
updates and `examples/research_publication.py` for a file-based CLI.
