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

## Optional recorded continuation

For a research checkpoint already published as an ordinary Artifact, a short
entry may contain only `workspace` and `checkpoint_ref`. With the installed SDK:

```python
import json
from etalon.research.continuation import resume_research, read_content

entry = json.load(open("entry.json", encoding="utf-8"))
context = resume_research(entry["workspace"], entry["checkpoint_ref"])
# Read a relevant claim or step only when useful:
# claim = read_content(entry["workspace"], context["claim_refs"][0])
```

`resume_research` only reads. It verifies referenced evidence, checks captured
source watermarks and current status-file bytes, and re-reads existing pending
action handles through `read_action_result`. It never submits, retries,
reconciles, changes a branch, or grants execution. A resolved/stopped branch
does not become runnable because a new chat reads it. Existing direct files,
SDK calls and optional skills remain valid entry paths without a checkpoint.

Inspect `authority`, `source_changes`, `issues`, and `pending` before choosing
the next step. Status guards bind a host's interpretation to exact authority
bytes; they do not parse arbitrary prose. Changed, missing, newly declared, or
unreviewed authority requires review. A current paused/stopped guard takes
precedence over the checkpoint's branch description. Never treat
`execution_permission="not_granted"` as permission to execute.

Step/checkpoint content is searchable and readable through the existing
evidence query/read SDK and MCP tools. The combined continuation helper is a
Python SDK convenience, not a new MCP execution tool. The content contract,
publication example, stale-state and evidence boundaries are documented in
`docs/thin-harness/CONTINUATION.md` in the source distribution; installed users
can use `etalon.research.continuation` directly.

`read_content` accepts typed claim/step/checkpoint/workflow content. Use
`read_evidence(workspace, ref, view="artifact", limit=...)` for ordinary result
JSON or component receipts; decode the returned base64 fragment and check its
verification and `next_offset`. A schema rejection is not a reason to rerun the
producer. Optional component/node and comparison interfaces are described in
[Component memory and composition](component-memory.md).

For improvement context, method/workflow patches, observations from normal use,
and scoped experience retrieval, see [Normal-use improvement](normal-use-improvement.md).
An existing improvement checkpoint is read first; it does not trigger another revision.
