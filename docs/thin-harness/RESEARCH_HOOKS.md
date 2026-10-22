# Optional research lifecycle hooks

ETALON can prepare bounded, sourced research context for Codex or Claude Code
without submitting science, resuming paused work, or triggering RSI. This bridge
uses `build_research_context`, the existing continuation and evidence readers,
and the host adapter's OS lock and atomic receipt writer. It does not replace
the host's private memory or intercept every host action.

## Entry points

```python
from etalon.research_hooks import handle_event

result = handle_event(
    "research-entry.json",
    {"hook_event_name": "SessionStart", "session_id": "my-session",
     "event_id": "delivery-1", "source": "startup"},
    host="codex", state_dir="host-delivery-trace",
    policy=None, max_bytes=16384,
)
```

The entry is the existing JSON entry file, or a dictionary containing `workspace`
and `checkpoint_ref`. `policy` uses the same explicit visibility and purpose
rules as the context reader. A host without lifecycle hooks may call this API
explicitly. No separate MCP hook server is required.

The command adapter reads one JSON event from stdin:

```text
python -m etalon.research_hooks --host codex --entry ENTRY.json --state-dir TRACE_DIR
```

Only the host-compatible JSON output is written to stdout. Invalid input,
unavailable context, identity conflicts and failed persistence produce a nonzero
exit and an error on stderr. A context-read failure is never emitted as a
successful context injection. A successfully persisted failure requires a new
delivery ID to retry; repeating its ID returns the same failure.

## Opt-in project exports

```text
python -m etalon.host export --host codex --target NEW_PROJECT --research-entry ENTRY.json
python -m etalon.host verify --target NEW_PROJECT
```

Use `--host claude` for Claude Code. The target must not already exist. Existing
exports without `--research-entry` retain their previous behavior and contain no
lifecycle hooks. No current project, user configuration, memory preference,
service or process is edited. The export binds absolute interpreter, installed
source, entry and receipt paths; re-export after moving environments.

The optional export contains a launcher and `.codex/hooks.json` or
`.claude/settings.json`. Claude uses direct executable/argument form. Codex uses
POSIX quoting plus a Windows fixed PowerShell invocation encoded as UTF-16LE;
event stdin is data and is never interpolated into shell commands. The native
host may still require review/trust of a project hook. Export verification is
not evidence that that host has discovered or run it.

Handlers have an explicit 30-second timeout. The CLI emits UTF-8 JSON bytes on
both stdout and stderr independently of Python's text-stream locale. Windows
receipt writes use extended filesystem paths when accessing the two full
SHA-256 identity components; public references and digests are not shortened.

### Codex native preflight

Before starting a model session, inspect the exported project's actual config
layers and hook metadata:

```text
python -m etalon.codex_hooks --target NEW_PROJECT
```

This bounded, read-only app-server check calls `config/read` and `hooks/list`.
It neither starts a chat nor writes trust settings. It reports the project
layer's disabled reason and each discovered project's hook hash, enabled flag
and trust status. It does not expose complete config, environment values or
commands from unrelated hooks. A missing project layer, a disabled layer, and a
discovered but untrusted hook are distinct outcomes. Config discovery alone
never establishes event delivery or model consumption.

For Codex, finish the normal project trust flow, then use `/hooks` to review and
trust the exact exported definitions. A project trust declaration is separate
from hook hash trust. In particular, explicit MCP command-line registration can
work while project hooks remain undiscovered; successful MCP calls do not verify
the hook integration. The Windows CLI tested during this upgrade also kept the
project layer disabled when project trust was supplied only through `-c`.
Use the preflight result rather than assuming a session override activated it.

After review, start a new chat so `SessionStart` can run. Observe native hook
receipts for that session, matched pre/post tool events and a stop acknowledgment.
Keep any failed/missing deliveries. Preflight does not automatically trust
definitions, use a trust-bypass flag or manufacture receipts. Changed hook
definitions require the host's review again.

## Event behavior

| Event | Behavior | Persistence |
| --- | --- | --- |
| SessionStart (startup/resume/compact/clear/fork) | Read the entry and return a bounded evidence package | Exact prepared context, selection policy, source watermarks, reference inventory and output hash/bytes |
| PreToolUse for ETALON MCP tools | Return current entry context, including existing/pending work and authority status | The same injection receipt plus a host-observed request fingerprint |
| PostToolUse | Record the observed return; explicit MCP errors remain failures | Tool identity, input/output fingerprints; no scientific admission |
| PostToolUseFailure (Claude) | Record host-observed failure | Failure observation, without pretending to know a scientific endpoint |
| PreCompact / Stop | Read and acknowledge the existing entry/checkpoint pointer | Checkpoint acknowledgment; no newly invented scientific checkpoint |

The tool matcher targets ETALON MCP names, including a single plugin prefix.
Unrelated tools and the optional `research_hook` tool itself are ignored. No
tool is blocked, permitted, rewritten, retried or dispatched by this bridge.
Arbitrary shell-based ETALON calls are not automatically observed. Codex uses
PostToolUse for returned errors; its export does not declare Claude's separate
PostToolUseFailure event.

The trace records only supplied host observations. A returned tool result is
not automatically a successful execution, a validated artifact or an admitted
scientific result. Producer-side receipts remain authoritative for those
claims, runtime costs and pending state. Original journals remain read-only.

## Delivery identity and recovery

Receipts are isolated by host, session and research entry. Host `event_id`, or
tool `tool_use_id`, gives a stable event identity (also separated by event type).
Repeating an identity reuses its saved output; changing its payload or handler
configuration is rejected. Locking and atomic rename prevent two concurrent
writers from independently committing the same event.

Some native lifecycle payloads have no unique delivery ID. The fallback uses
the event payload and current context fingerprints. Equal observations are
deduplicated, while a changed research snapshot can produce refreshed context.
Receipts explicitly say `content_only`: indistinguishable native events cannot
be counted separately. This is not an exactly-once host execution guarantee.

The complete output is saved before it is returned. A crash after saving but
before delivery can replay the same output. Its status is therefore
`prepared_for_host_not_consumption_verified`, never “the model read this”.
PreCompact/Stop receipts acknowledge the already committed checkpoint; normal
execution must persist its scientific progress at the producer, not wait for
session termination. No transcript is parsed or stored.

## Trust and scope

The context carries a data label and a fixed wrapper identifying source content
as research data, not instructions or authority to execute. Native hosts choose
the message role for hook context; the wrapper is not a security sandbox.
Purpose/visibility rules run before selection and serialization. Do not place
untrusted source instructions into policy or host configuration.

The bridge neither disables native memory nor claims selective control over
the host's internal memory implementation. It establishes a project research
entry and auditable context delivery. Skills retain responsibility for scientific
selection and judgment; deterministic code retains identity and persistence.

## Verification scope

`tests/test_research_hooks.py` covers event replay, conflicting IDs, session/host
isolation, snapshot refresh without host IDs, failure receipts, pending/paused
data retention, no scientific writes, existing Journal/context integration,
native JSON shapes, opt-in exports and quoted export paths. These are protocol
and SDK tests. A real native host session must be separately observed before
claiming lifecycle discovery or model consumption; producer admission, expensive
science and cross-project RSI benefit are outside this test scope.

Protocol references checked during implementation:

- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
