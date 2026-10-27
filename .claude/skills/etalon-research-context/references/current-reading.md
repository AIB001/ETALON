# Current research notes

Read the project's explicit entry with its installed interpreter; do not search
the machine for unrelated journals or skills. The standard package commands are:

```sh
python -m etalon research current --entry ENTRY.json --max-bytes 16384
python -m etalon research detail --entry ENTRY.json --branch BRANCH --record-id RECORD --select /knowledge/result --select /counterevidence
python -m etalon research runtime
```

Use the returned branch and record to pin follow-up reads. `detail` defaults to
bounded selected fields; `--offset`, `--limit` and `--max-bytes` accept the existing
next-read recipe. `--view index` is available when exact field names are unknown.
`current --verify-originals` checks all cited original identities while keeping the
brief bounded; it does not deliver their bodies. `detail --view full` explicitly
requests the whole note and verifies its originals. `runtime` reports the actual
Python executable and loaded reader paths/hashes; use the task's bound interpreter
and source environment rather than changing a shared installation.
If definitions or the task workset may change, save the returned `details` object
as JSON and pass that file to `--entry`. It carries the original bindings as well
as the record pin. Selecting an old record through an entry with incompatible new
definitions fails explicitly; it does not silently reinterpret the old result.

The SDK remains available. Prefer the entry's existing read
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
The older `examples/read_research_work.py` remains compatible with its existing
arguments; ordinary note reads use the package commands above.

`knowledge` and `full` remain available when the whole note is useful. Original
identity verification is independent of full delivery: pass `verify_originals=True`
to a selected SDK read or use the CLI flag above. A known small note can be read directly; an index is
not a compulsory extra call. Older graph entries use the paths in
[context access](context.md).

## Callables and intermediate results

Resolve a known descriptor with `research method --entry ENTRY --pointer POINTER`
or `notes.resolve_method(entry, pointer)`. It returns the original descriptor and
resolved source; it does not import code, choose a method or execute it. Use its
declared function/signature, inputs and conditions in the existing environment.
The pointer can name any task-defined descriptor, including one under feedback.

`research original --entry ENTRY --pointer POINTER` gives metadata for an original
file named by a note. With `--selector SELECTOR.json`, examples are
`{"json":["/summary","/exceptions"]}` or
`{"npz":[{"key":"scores","slice":[[0,3],1]}]}`. The latter selects rows 0..2,
column 1; missing selectors list headers, not the arrays. Keep the same byte cap.

For calculation, selected arrays can stay out of model context entirely:

```python
from etalon.research.notes import open_original

with open_original(entry, "/result_read/source") as saved:
    scores = saved.array("scores")
    # Repeated access reuses this member; unselected members are not decompressed.
    candidate_ids = saved.array("candidate_ids")
    result = known_method(scores, candidate_ids, **task_parameters)
```

`known_method` is the task's declared function, not another ETALON API. This reader
verifies the original before access. Use one reader for the batch; save newly
useful results with their meaning and request identity. Resume through the
existing execution/reuse receipt rather than calling the function again merely
to regenerate a summary. Relevant contrary results still require interpretation.
