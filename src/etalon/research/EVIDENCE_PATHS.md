# Evidence file arguments

New `evidence` and `counterevidence` **file arguments** are relative to the
parent directory of the entry path supplied by the caller. For example,
`entry=/research/work/CURRENT.json` with `evidence=["work_out/check.json"]`
attaches `/research/work/work_out/check.json`, regardless of the MCP server or
CLI process working directory.

This also applies when `CURRENT.json` is a one-hop locator into a handoff:
the caller's `CURRENT.json` directory remains the base, not the internal
journal/entry directory. Absolute file arguments keep their existing meaning.

The same rule applies to SDK `patch_progress`, method publication's evidence
arguments, resource-use feedback (including nested MCP/CLI feedback arguments),
CLI complete-note evidence additions, and advice reports. Original identity
checks and duplicate-reference handling still apply.

Existing `{path, identity}` original references inside notes or patches are
already pinned and are never rebased. Resources, historical `resource_entry`
bindings, executable source/input paths, and arbitrary strings in research
records are also unchanged. `original(path)` remains a general path utility;
it has no entry context and retains its existing behavior.

Only these explicit attachment arguments gain this meaning. A CLI JSON payload
file's location is not an evidence base. No directory search, missing-file
fallback, or implicit rewrite of historical records occurs.
