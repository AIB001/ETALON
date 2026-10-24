# Portable project adapters

ETALON ships the same research reader and lifecycle handler for Codex and Claude
Code. Host configuration declares when to call the handler; the configured MCP
server owns the entry and receipt bindings. Installing a package does not grant
project trust, scientific execution or access to a remote evidence directory.

## Install and move

Use the intended environment's Python, with ETALON and its MCP extra installed:

```text
python -m etalon.host install --host codex --target NEW_PROJECT --research-entry /absolute/CURRENT.json
python -m etalon.host doctor --target NEW_PROJECT
```

Use `--host claude` for Claude Code. The default installation uses native
`mcp_tool` hooks; `--hook-transport command` keeps the command adapter available
for hosts without MCP hook support. Ordinary `export` remains backward compatible.

The portable recipe is `etalon-install.json`. After moving an owned export,
run the same install command at its new location with the receiving machine's
Python and explicit entry. This rebinds the MCP runtime, entry and receipts while
preserving reviewed hook definitions. The handler definitions contain no machine
paths. Existing user projects and modified owned files are not silently replaced.
Use a separate adapter directory when a host configuration needs manual merging.

No dependency upgrade, global configuration change, source deletion or trust
database edit is part of installation. Host review applies to the final exported
files. A new machine may require its own review even if hook bytes match.
Installation operations retain previous owned bytes, intent and completion
receipts; repeat the exact install after an interruption. A conflicting request
or user edit requires reconciliation instead of overwrite.

For remote execution, the entry and evidence locations must be accessible to the
MCP server's machine. Use the existing research-entry handoff and evidence-location
interfaces to transfer and verify evidence. Installing a host adapter alone does
not copy scientific originals, environments or running jobs.

## Native activation and first continuation

The existing MCP server exposes `etalon_research_hook(event)`. It reads
`ETALON_RESEARCH_HOOK_BINDING` from its server configuration and emits native hook
JSON through MCP TextContent. A failure sets the MCP protocol's `isError`; it
cannot masquerade as successful context injection. Callers cannot select another
entry or receipt directory through event arguments. Supplied events are
observations, not authenticated proof of a host identity.

Both adapters provide SessionStart, PreToolUse, PostToolUse, PreCompact and Stop;
Claude also provides PostToolUseFailure. The hook ignores its own tool to avoid
recursive observation. It never runs science or dispatches RSI.

Claude skips MCP hooks at startup/resume before the MCP client is available.
Codex also needs an existing MCP connection. Therefore first continuation starts
from the supplied entry and context skill; the first connected ETALON tool call's
PreToolUse can deliver the baseline. After real compaction, SessionStart or the
next PreToolUse rebuilds a baseline. A prepared receipt does not prove delivery;
host traces and the agent's actual source reads establish those separately.

`resume_frontier` exposes the latest saved steps and their exact output references
in summary reads. A new chat checks this frontier alongside scientific conclusions,
counterevidence, workset activation, pending tasks and pauses. Reading a new audit
does not itself create a scientific result. Workset changes replace the previous
injected baseline and name deactivated/activated resources; they do not erase text
already seen by the model.

The summary byte budget is a strict bound, not a guarantee that every request
fits the default 16 KiB. A longer question or more control metadata can exceed
it. If the reader reports `checkpoint and counter control metadata exceeds
context bound`, repeat that read with an explicit larger `max_bytes` (for
example, 32768), or narrow optional detail. Do not remove counterevidence or
checkpoint controls to make the request fit. A failed explicit summary read and
a successful hook-delivered baseline must be recorded as separate outcomes.

## Failure and acceptance boundaries

Use `inspect_publication` to inspect an unfinished memory publication. The existing
publication API validates proposed steps/checkpoints before writing results.
`close_publication` can append an explicit withdrawal, or link a prepared correction
to the exact failed intent. Old artifacts and failure stages remain. Successfully
published operations cannot be closed as failed, and terminal failures are not
automatically resumed. See [publication](RESEARCH_PUBLICATION.md).

`doctor` verifies generated configuration and bindings. Its `verification_stages`
separately reports static installation, app-server reachability, native discovery,
host review, login, MCP reachability, native event acceptance and model consumption.
Only static installation is checked by doctor; `acceptance_complete` remains
false. A receipt on disk does not upgrade these other stages.

For Codex, inspect `python -m etalon.codex_hooks --target PROJECT`, then use normal project trust and
`/hooks` review. For Claude, review the actual project hooks and MCP configuration
through the host. Inspection first reads bounded `app-server --help` output and
selects the advertised `--listen stdio://`, `--stdio`, or explicit default stdio
transport. It does not infer compatibility from a version number or upgrade the
host. An unsupported `hooks/list` keeps transport/configuration diagnostics and
reports discovery as unsupported rather than pretending hooks were accepted.
Use `--check-login` to read account metadata with token refresh disabled. Existing
credentials are reported as `credential_present_unvalidated`; this is not a model
access test. Missing login, project review, MCP reachability and actual event
delivery are independent states. Raw configuration, account identity and tokens
are not returned.

Neither stdio protocol tests nor manually constructed compact
events prove native compaction. Real acceptance requires a Codex contextCompaction
item or Claude compact_boundary, followed by verified entry reads and no duplicate
scientific actions. Keep failed host attempts and classify them separately.

Protocol references: [Codex hooks](https://learn.chatgpt.com/docs/hooks#mcp-tool-hooks)
and [Claude Code hooks](https://code.claude.com/docs/en/hooks#mcp-tool-hook-fields).
The transport and read-only account interface are documented in
[Codex App Server](https://learn.chatgpt.com/docs/app-server).
