# Host adapters and thin-harness migration

ETALON supplies composable science functions, MCP, reliable execution and evidence.
The host chooses questions and actions, including no skill, partial adoption,
combination, stopping and exploratory methods. The contracts in
`docs/thin-harness/` define action, result and method semantics.

## Setup

Use an isolated Python 3.11+ environment with ETALON's `mcp` extra. Invoke export
with the actual interpreter that should serve the tools:

```bash
python -m etalon.host catalog
python -m etalon.host catalog --detail summary --skills campaign
python -m etalon.host export --target /absolute/new/project --host codex
python -m etalon.host verify --target /absolute/new/project
```

The target must be new. For an existing research project, export to a separate
directory and deliberately integrate the generated files with its existing
configuration. Export does not modify global settings or overwrite instructions.

For a relocatable research adapter, use the intended environment's Python:

```bash
python -m etalon.host install --host codex --target /absolute/new/project --research-entry /absolute/CURRENT.json
python -m etalon.host doctor --target /absolute/new/project
```

Use `--host claude` for Claude Code. This ships native `mcp_tool` hooks plus a
portable installation recipe. After moving the unchanged export, repeat install
with the new target and server-local entry to rebind the runtime and paths.
Interrupted installs retain their intent and can resume with the same request.
No dependencies, global configuration or host trust are automatically changed.
The final host must discover and review its project/hooks. `doctor` checks
configuration integrity, not actual delivery or model consumption.

MCP may not be ready for the initial SessionStart (Claude skips that startup hook).
Give the new chat the entry; its first ETALON read can receive the baseline via
PreToolUse. After compaction, read the current summary and `resume_frontier`, then
the relevant latest-step/output originals. Preserve workset changes, counterevidence,
pending tasks and pauses. Hook receipts alone do not prove these were understood.
The existing repository `.mcp.json` is a historical Linux/conda configuration;
the exporter generates an interpreter-bound ETALON configuration for this machine.

| Host | Generated files |
| --- | --- |
| Codex | `.agents/skills/`, `AGENTS.md`, `.codex/config.toml` |
| Claude Code | `.claude/skills/`, `CLAUDE.md`, `.mcp.json` |

Both exports also carry `HOST_SETUP.md` and the optional `host-guides/` files.
The short AGENTS/CLAUDE entry links to them; it does not inline their contents.
The installation keeps the same resources alongside `etalon.host`. For an
existing project, add only a small pointer to its actual current status and
published evidence manifest when useful; the [project-entry example](host-guides/project-entry.md)
shows the shape. Do not inject the whole history or private evaluation answers.

## Published evidence in a new chat

An optional `etalon-workspace.json` locates released sources and status refs.
It is not an execution plan, authorization system or mandatory registration
step. A known SDK/file/ref remains directly usable. For a bounded lookup:

```python
from etalon.research.evidence import query_evidence, read_evidence
page = query_evidence(workspace, query="feedback", limit=5)
# Or read a previously saved EvidenceRef with no preceding query:
record = read_evidence(workspace, known_ref, view="record")
```

Here `workspace` is an existing manifest path or validated object, and
`known_ref` is a real source-bound reference, not a fabricated placeholder ID.
Read coverage/warnings and distinguish metadata from verified original bytes.
The `research` member `references/evidence-reuse.md` describes paging,
counterevidence, relocation and cross-source cost/role boundaries. The SDK and
MCP `etalon_evidence_query`/`etalon_evidence_read` share the same semantics.
The source-delivered `examples/evidence_reuse.py --workspace ... --query ...`
reads one page; `--ref ref.json --view record` bypasses query. Neither invokes a
model, creates a campaign or registers a method. No new chat is required to
read a skill or query before doing its actual task.

## Runtime binding

The generated server command uses the exporting interpreter and an explicit
`PYTHONPATH` to its ETALON source. An adapter does not freeze that source or deploy
scientific data. Rebind a portable installation at its destination; ordinary
exports still require re-export. Install only the scientific extras needed by
the selected task; read-only discovery needs no simulation engine.

Codex's local skill directories and stdio configuration follow the official
[skill discovery](https://learn.chatgpt.com/docs/build-skills) and
[MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) contracts.
Claude Code follows its [project skill](https://code.claude.com/docs/en/skills)
and [MCP configuration](https://code.claude.com/docs/en/mcp) contracts.

Open the exported directory in the intended host and establish any host-required
project trust. Use a relevant known tool directly or request scoped discovery;
reading a skill is optional. For host setup validation, check the reported
interpreter, module path and digests and record actual calls separately for each
host. A successful configuration
parse or MCP SDK handshake does not prove native skill discovery or use.

Durable local workers still require Linux `/proc` identities and POSIX locks.
Windows read-only MCP tests do not validate worker execution. For workers, export
and run inside the intended Linux/WSL environment; scientific executors need their
own explicit environments and inputs.

## Capabilities and versions

`etalon_capabilities` reports the actual registered tools, their cost classes,
installed operations, full skill manifests and server paths. Server construction
and cost indexing use one registration function, including sweep tools. A missing
cost classification or duplicate tool name fails index construction.

For a smaller response use, for example:

```python
# MCP arguments; tool descriptions and costs use the actual registration index.
etalon_capabilities(detail="summary", skills=["campaign"],
                    tools=["etalon_action_result"], operations=[])
# SDK equivalent for skill versions alone:
from etalon.skills import catalog
catalog(detail="summary", skills=["campaign"])
```

`detail` is `full` (the compatible default) or `summary`. Each category selector
is an exact list: `None`/omission includes the category, `[]` excludes it. Unknown
or duplicate names refuse rather than silently broadening the query. Selection
does not authorize methods or limit explicit external bundles. A scoped skill
catalog reads/hashes only the selected bundles; a summary omits their member
inventories but retains the full bundle content digest, description and entrypoint.
Expand with `detail="full"` and the same skill names when member hashes are needed.
The scoped catalog digest covers selected skill versions, not unrequested skills.
The host guidance keeps its separately computed digest.

Summary tools are keyed by callable tool name with `cost` and `description`;
full mode retains the original name-to-cost mapping. Operations retain their
required/optional argument descriptions. Known tools and exact skill members
can be called/read without this discovery step. No payload reduction certifies
host adoption, scientific validity or a speed benefit; those require actual
measurements. Full MCP tool schemas remain the authority for call arguments.

`etalon_skill_read(slug, member)` reads an exact manifest member. MCP resources
remain available at `etalon://skills`, `etalon://skills/<slug>`,
`etalon://skills/<slug>/references/<name>` and `etalon://host`.

The original six short ETALON entrypoints retain their historical material in
`references/legacy-workflow.md` for interpreting previous jobs; hardware
assumptions and measured outcomes there are historical. The catalog also exposes
the original six MolQuarry bundles as `molquarry-<name>`, plus data/design,
research and optional skill improvement. MolQuarry bodies are reused from their
asset source, including references; the old `etalon://skills/molquarry` and
`etalon://skills/molquarry/<name>` resources remain readable.
Neither the skill index nor the host guide mandates a global research sequence.

Bundle identities hash all maintained member paths and bytes, including references
and scripts. Catalog identity depends on the domain skills S; the short host guide
has a separate usage-method identity U. Historical scientific policies named phi
are unchanged. Added, changed and deleted supporting
files affect versions. Git fixes line endings for these instruction files so their
byte identities survive Windows/Linux checkout and wheel packaging.

`etalon-host-manifest.json` records exported versions and member hashes. `verify`
checks changed/missing/extra skill files and compares the export with the current
installation. It exits nonzero for drift. The manifest is a local integrity aid,
not a signed attestation. Matching bytes do not establish host invocation,
action/result linkage, independent evaluation or improved scientific performance.
User work outside managed skill folders is not treated as an unexpected skill file.

## Explicit local bundles and preserved versions

Catalog entries are defaults, not a method whitelist. A local bundle must
have a `SKILL.md` with name and description frontmatter. Explicit reads reject
symlinks/junctions, traversal and absolute member paths. Reads allow at most 512
members, 256 KiB per file and 4 MiB total. The SDK can inspect an authorized local
bundle before registering anything:

```python
from etalon import skills
ref = {"root": "/absolute/local-method"}
manifest, contents = skills.snapshot("local-method", bundle_ref=ref)
content_ref = skills.persist_snapshot("local-method", "/absolute/new-snapshot", bundle_ref=ref)
assert skills.verify_snapshot(content_ref) == manifest
```

`persist_snapshot` requires a new target and saves all observed bytes plus the
manifest. Changing or deleting a source reference cannot change that snapshot.
`verify_snapshot` reads the actual saved bytes; changing a saved member invalidates
it. Keep snapshots referenced by live or historical records. The saved manifest
is integrity evidence, not a signature or proof the host followed instructions.
MCP `etalon_skill_read` also accepts `bundle_ref`; its explicit form requires both
absolute `root` and the expected `manifest_sha256`. It reads only an exact member
of that bounded skill bundle, never an arbitrary standalone server file.

Create `MethodVersion(content_ref=content_ref, verification_basis="content_verified", ...)`
through `ResearchJournal.record` or `etalon_method_record`. The journal calls the
same byte verifier. Use `host.record_method_use(journal, **fields)` to append
`MethodUse` via that journal. Supply an existing campaign scope in each subject:
`{"kind": "request", "id": "next-step", "scope": str(journal.store.path)}`.
A planned request is not an already executed action. `selected_methods=[]` means
no selection; multiple version references and optional scope record composition
or partial adoption. A stopping reason or later attribution appends a new record.
Availability, server content reads, host-reported use and observed execution are
different evidence. The current journal accepts `basis="host_reported"`; preserve
native execution/read traces separately rather than claiming `tool_observed`
support from a content read. Unknown model/host fields stay null.

The host guide's digest is independent of S. Register snapshots of a usage method
or improvement method with their own descriptive roles and dependency references;
changing S does not implicitly change U, G or scientific knowledge M. Candidate
methods can be tried within authority without promotion. Effects require separate
independent subsequent evaluation, failures and complete cost accounting.

For exact lookup without a broad source scan, use the installed skill member
that matches the task or inspect the known module directly. `research` covers
transfer/validation/feedback/pose/preparation/fixed_pool/online. The `campaign`
members `references/finite-diagnostics.md` and
`references/paired-models-and-segments.md` cover `bind_outcome`, `condition`,
`exact_program` and `empirical_model`; `data-design` covers local bundle import
and evidence roles. SDK signatures and references are available independently
of a campaign workflow. An exact-member read needs no preceding catalog call.

`skill-improvement` gives an optional concrete revision method; its
`references/revision.md` describes separate content and usage lineages, bounded
partial trials, honest attribution, costs and subsequent evaluation. The source
distribution includes `examples/skill_improvement.py`: run it with a new output
directory to exercise actual complete snapshots, MethodVersion/Use/Evaluation
and one-page event consumption on synthetic inputs. It creates diagnostic
records, preserves unknown monetary cost, and stops without independent benefit
or changing a default. Examples are source-delivered, not wheel commands.

## Optional committed-event consumption

Without a native hook, explicitly read `etalon_events_read` and choose a next
step. `host.consume_events` is a synchronous one-page helper, not a background
agent loop. It calls `runtime.api.read_events` against a campaign or runtime log:

```python
from etalon.host import consume_events
report = consume_events(
    {"kind": "campaign", "database": "/absolute/campaign.sqlite"},
    state_path="/absolute/host-workspace/cursor.json",
    handler_method_version="methodversion-<record-id>",
    handler=handle_committed_event,
    event_types=["research_recorded"],
    record_types=["etalon-method-evaluation/1"], limit=100,
)
```

`handle_committed_event(event, stable_request_id)` receives the committed event
and a deterministic request ID. When submitting an action, use that ID and stable
request semantics on replay. Action submission idempotency handles the window
where submission succeeded but cursor saving failed. Other handler side effects
need their own idempotency. This is at-least-once delivery, not distributed
exactly-once. The helper saves a cursor after each successful or skipped event;
exceptions leave the failed event for replay and do not roll back scientific facts.
An OS file lock prevents simultaneous cursor writers and is released on crash.

Cursor state binds the source log, handler version and filters. Use a distinct
state path when changing those choices. Research hooks require explicit record
schema filters, so an evaluation-triggered proposal does not trigger again on its
own MethodVersion. An empty event filter explicitly skips all events. Consumption
never expands budgets, resumes paused science or replaces the runner's recovery
receipt observer. A hook can generate a candidate, stop or skip within existing
authority; it must not invent a successful result from an unresolved event.

## Compatibility, verification and rollback

Old skill URIs and executor/workflow entrypoints remain available. Exports are
new-directory adapters; existing roles/configuration are never overwritten.
Wheels retain legacy flat skill entries and full bundles. Export verification
checks managed files against both the saved manifest and current source. A changed
source can legitimately make an old export differ; the old bytes stay available.

Run in the existing actual environment; this task does not require a dependency
upgrade:

```bash
python -m pytest -q tests/test_host_capabilities.py tests/test_host_bundles_hooks.py
```

These tests distinguish source/export integrity, MCP stdio protocol calls,
component fixtures and actual native host behavior. A Python MCP client using a
Codex/Claude configuration is not the corresponding native host. Native Codex
and Claude Code validation, paid scientific execution and independent S/phi/G
benefit require separate evidence; see `docs/thin-harness/DELIVERY_C.md` and V's
report for actual runs. Windows can perform the source and protocol checks;
durable local worker execution remains subject to its OS capability checks.

Rollback selects the prior preserved source/snapshot and a matching new adapter.
No historical database migration, scientific evidence rewrite or paused campaign
restart is performed by host setup or method snapshot creation.
