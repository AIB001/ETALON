# Current research notes

Read the project's explicit entry with the installed SDK; do not search the
machine for unrelated journals or skills. Prefer the entry's existing read
recipe, batch known exact pointers with `view="selected"`, or use `read_current`
for a bounded current brief; use an optional index for an unfamiliar or
large note. Never inject the whole knowledge library, and avoid repeated
`index` → `knowledge` → `full` conversions without a decision-relevant gap:

```python
from etalon.research.notes import read_current, read_entry

# Honors entry.read_recipe; otherwise delivers bounded current knowledge.
page = read_current(entry, max_bytes=16384)
```

When exact needed pointers are already known, request them directly instead:

```python
part = read_entry(entry, view="selected", selectors=["/knowledge/result"],
                  offset=0, limit=20)
```

Replace `/knowledge/result` with a known exact pointer. The returned `details`
pins the note. Follow `next_read` for omitted fields or later collection items,
keeping its exact escaping and byte limit. Use `read_entry(entry, view="index")`
when unfamiliar structure makes an inventory useful; it is not a prerequisite.

The brief includes declared counterevidence pointers and unread coverage. Select
the contrary summaries, limits or conditions that could change this choice, then
open their originals where needed. A count or pointer does not mean its argument
was delivered. Preserve omitted and unread scope in any conclusion it limits;
neither expanding every historical counter original nor a fixed audit is required.
Selection follows explicit structure, not semantic relevance ranking.

Through `etalon_research_context`, `view="summary"` uses the same current-read
policy. For a selected field use
`view_options={"note_view":"selected", "selectors":["/knowledge/result"], "offset":0}`
and the standard `limit`. Use the response's pin and exact continuation recipe.
Request `view_options={"note_view":"index"}` only when an inventory is useful.
For the CLI, use the existing `examples/read_research_work.py` and its emitted
commands, or known fields with `--view selected --select /knowledge/result
--offset 0 --limit 20`; retain the existing entry argument and byte limit.

`knowledge` and `full` remain available when the whole note or its verified
original basis is useful. A known small note can be read directly; an index is
not a compulsory extra call. Older graph entries use the paths in
[context access](context.md).
