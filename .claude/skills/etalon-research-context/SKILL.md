---
name: etalon-research-context
description: Read current ETALON research progress and select the knowledge needed for the next choice, with exact pointers for optional evidence expansion.
---

# Read the current research choice

Start from the project's explicit entry with
`python -m etalon research current --entry ENTRY.json`; use the task's installed
interpreter. This honors its existing read recipe and otherwise returns a bounded
brief. The result is already the current context; read a named omitted field when
it could change the next choice. Use `research detail --help` for exact selection
and `research progress --help` to save changed fields without reconstructing old notes.
The corresponding SDK calls remain available:
Choose the read needed for the current decision: batch known exact pointers with
`read_entry(entry, view="selected", selectors=["/knowledge/<key>"])`, read current
knowledge once with `read_current(entry)`, or use `view="index"` to locate fields
in an unfamiliar or large note. An index is optional. Follow returned exact
pointers and paging recipes; avoid `index` → `knowledge` → `full` conversions
without a decision-relevant gap. Never inject the whole knowledge library.
[Current reading calls](references/current-reading.md) covers SDK and CLI use.
An already delivered current view is usable input; do not fetch it again merely
because another tool ran. Read the changed record or missing decision field.
For an original calculation, read its needed fields or arrays directly using
`research original`; use `research method` for a declared callable's contract.
Keep array processing inside code and return the scientific difference, not raw
arrays or a reconstruction of the source implementation.

Use already delivered knowledge when it answers the choice, including relevant
contrary results and their exact pointers. Read original support, conflicting
results or conditions when they could change the next action or its interpretation.
To check cited byte identities without a full note dump, add `--verify-originals`
to `research current` or `research detail`; this does not deliver original bodies.
Use that verification result instead of independently rehashing or printing the
whole original-file manifest for routine continuation. Complete receipts can
stay on disk: read their result/status, exceptions and exact path, then expand
only decision-relevant fields. This does not replace reading contrary scientific
arguments. For progress, append new originals with `--evidence` or
`--counterevidence`; existing references and identity checks are handled by the
CLI, so preserving them does not require rereading the old evidence list.
Carry the returned unread coverage into the conclusion when it limits the claim;
a counter count alone is not delivery of those counterarguments. No full-history
read or separate audit stage is required.
An index, digest or delivered body does not establish scientific validity or host
understanding. A read does not create a new observation or run research.

Carry forward the objective, current authorization, completed outputs, pending
handles and next useful action. Existing authorization continues across a read
or handoff. The reader's `execution_permission="not_granted"` means that this read
grants no permission; it does not revoke the active user's or task capsule's
authorization. An explicit pause still takes precedence. A recording failure needs
record repair, not another scientific run. Save meaningful progress through the
existing journal/entry interfaces and continue authorized unfinished work.

Keep task-specific methods, fitted values and memory definitions bound to their
declared task and conditions. On a task or workset change, read the new baseline
and check applicability before reuse; a shared skill name or target name is not
enough. An algorithm may transfer while its local thresholds or evidence do not.

For graph entries, impact review, rule applicability or older continuation paths,
open only the relevant part of [context access](references/context.md) or
[extended continuation](references/continuation.md). For mechanism reasoning use
`etalon-research-decision` when useful; if absent, request
`etalon_skill_read(slug="research-decision", member="SKILL.md")`.
Optional skills are named in `skill-dependencies.json` and are not installed or
read automatically. Their absence does not block this skill's reading capability.
For host setup only, see [selected installation](references/skill-installation.md).
