# Host adapters and thin-harness migration

This increment implements the source-independent part of stage 1 and the bundle
versioning part of stage 3 of the 2026-10-19 thin-harness design. The host chooses
research questions, methods and next actions. ETALON retains execution, scientific
identity, budget and evidence boundaries. It does not start another agent loop.

## Setup

Use an isolated Python 3.11+ environment with ETALON's `mcp` extra. Invoke export
with the actual interpreter that should serve the tools:

```bash
python -m etalon.host catalog
python -m etalon.host export --target /absolute/new/project --host codex
python -m etalon.host verify --target /absolute/new/project
```

The target must be new. For an existing research project, export to a separate
directory and deliberately integrate the generated files with its existing
configuration. Export does not modify global settings or overwrite instructions.
The existing repository `.mcp.json` is a historical Linux/conda configuration;
the exporter generates an interpreter-bound ETALON configuration for this machine.

| Host | Generated files |
| --- | --- |
| Codex | `.agents/skills/`, `AGENTS.md`, `.codex/config.toml` |
| Claude Code | `.claude/skills/`, `CLAUDE.md`, `.mcp.json` |

The generated server command uses the exporting interpreter and an explicit
`PYTHONPATH` to its ETALON source. It is a local adapter, not a portable deployment
or a frozen copy of the executing code. Export again from the destination
environment after moving machines. Install only the scientific extras needed by
the selected task; read-only discovery needs no simulation engine.

Codex's local skill directories and stdio configuration follow the official
[skill discovery](https://learn.chatgpt.com/docs/build-skills) and
[MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) contracts.
Claude Code follows its [project skill](https://code.claude.com/docs/en/skills)
and [MCP configuration](https://code.claude.com/docs/en/mcp) contracts.

Open the exported directory in the intended host, establish any host-required
project trust, inspect the ETALON tool list, call `etalon_capabilities`, and read
one relevant skill. Check the reported interpreter, module path and digests.
Record this native interaction separately for each host. A successful configuration
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

`etalon_skill_read(slug, member)` reads an exact manifest member. MCP resources
remain available at `etalon://skills`, `etalon://skills/<slug>`,
`etalon://skills/<slug>/references/<name>` and `etalon://host`.

Six short skill entrypoints route to relevant historical references. The old
material remains in each bundle's `references/legacy-workflow.md` for interpreting
previous jobs; its hardware assumptions and measured outcomes are historical.
Neither the skill index nor the host guide mandates a global research sequence.

Bundle identities hash all maintained member paths and bytes, including references
and scripts. Catalog identity depends on the domain skills S; the short host guide
has a separate usage-method identity for phi. Added, changed and deleted supporting
files affect versions. Git fixes line endings for these instruction files so their
byte identities survive Windows/Linux checkout and wheel packaging.

`etalon-host-manifest.json` records exported versions and member hashes. `verify`
checks changed/missing/extra skill files and compares the export with the current
installation. It exits nonzero for drift. The manifest is a local integrity aid,
not a signed attestation. Matching bytes do not establish host invocation,
action/result linkage, independent evaluation or improved scientific performance.
User work outside managed skill folders is not treated as an unexpected skill file.

## Compatibility and rollback

Existing tool names, campaign stores, execution logic, budget guards, worker
recovery and scientific evidence are unchanged by this increment. Old skill URIs
still resolve; entrypoints are concise and references are available on demand.
The old `workflows` index is retained, but no mandatory `start_with` route is
advertised. Wheels retain legacy flat entrypoint copies as well as full bundles.

Keep older exports when comparing versions; export a new directory instead of
rewriting one used by a running session. To roll back, select the prior source and
matching adapter in a fresh environment. This change migrates no databases and
does not rewrite observations or restart paused campaigns.

## Source gaps and remaining stages

The inspected GitHub baseline is `5927aceb346475a6f97550f561e7eb3b291611da`.
Against the handoff's 306 selected ETALON fingerprints, this checkout had 91
missing files, 14 content differences and 201 newline-only differences before
this increment. Those counts describe selected fingerprints, not a full backup.

Missing code includes `research/`, `active/prism.py`, `boundary/prism_v1.py` and
design-candidate import contracts. The independent PRISM and integration working
trees are also absent. The handoff describes newer uncommitted changes to the
active runner/store and runtime executors. Their source must be reconciled before
claiming the design's architecture is implemented; this increment does not invent
substitutes for them.

Still pending:

- Native host discovery and invocation evidence for Codex and Claude Code.
- Shared host/algorithm action execution, dependency-scoped conflict checks,
  idempotency and unknown-outcome recovery against the migrated source.
- Method versions and actual use/evaluation events linked to the existing
  scientific history H; a content manifest alone is not that linkage.
- The molecular-edit chain, independent S/phi comparisons, G experiments and
  prospective scientific validation with the required inputs and authority.

The design's P01-P16 remain the implementation baseline. This increment addresses
optional capability use, progressive loading and version separation, while
preserving scientific boundaries. It does not establish architecture completion
(A), native host integration (H), learning efficacy (I/G) or scientific benefit (S).

## Verification

```bash
pip install -e '.[mcp,dev]' hatchling
python -m pytest -q tests/test_host_capabilities.py tests/test_mcp.py
```

Tests compare the actual stdio tool list against the cost index, launch both
exported configurations from a different working directory, read entrypoints and
references, reject traversal, detect modified/deleted/added files and separate
S from phi changes. The wheel test loads the package outside the checkout with
site packages disabled, then exports and verifies complete bundles.

The original Windows targeted baseline had 196 passes, 42 failures, 3 errors and
16 skips across nine modules. Failures include POSIX requirements, symlink
privileges and MolCascade Windows I/O. Vendored-asset verification also failed
before this change, with differences beyond line endings. These are tracked
baseline limitations, not passing validations of the refactor or scientific code.
