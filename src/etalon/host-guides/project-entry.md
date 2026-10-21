# A small project entry for a new chat

A new chat needs the current task and a short pointer to relevant project
resources. It need not inherit an old session, developer answers, all source
code or the complete evidence history. The host may choose a known SDK/file,
optional query/read or any useful method, including no skill.

For example, a project owner can adapt these four lines to real project paths:

```text
Project: <one-line scope and current question>.
Current status and resource limits: <authoritative status file>.
Published evidence, when useful: <path to etalon-workspace.json>.
Choose or modify methods freely; use known files/SDK or optional query/read.
```

Angle-bracket values are placeholders, not ready-to-use locations. Do not paste
this template over an existing AGENTS/CLAUDE file. Link a released status file
without adding private evaluation answers, sealed labels or old transcripts.
Status references locate authority; a source manifest does not grant permission.

An optional `etalon-evidence-workspace/1` manifest names published sources with
stable `source_id`, kind, location and actual identity anchor. Relative paths
resolve from the manifest directory, independently of the new chat's working
directory. `location_map` binds relocated bytes to their existing identity;
`status_refs` points to current project state. The manifest is source-location
data, not a required registry or task plan. Known EvidenceRefs can be read
directly without querying first; raw known files remain usable too.

Keep the manifest small and expose only released sources. Do not recursively
scan a directory to populate it. Source roles and query filters do not isolate
held-out data: originals, derived summaries and indexes need actual access
separation when claiming an independent task. Read the relevant status or
source only when needed; an unavailable source is a limitation, not an empty
successful search or a zero cost.

The `research` skill's `references/evidence-reuse.md` gives SDK calls and
provenance rules. This reference is optional and is not automatically loaded
into a new chat. A short entry can reduce provided context, but measured host
quality, token use and benefit require separate observations.
