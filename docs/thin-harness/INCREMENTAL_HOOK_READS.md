# Verified incremental hook reads

The existing research-context and hook entry points are reused. A request-local
read scope shares decoding and projection of a frozen Journal; the hook can also
retain a disposable source cache between events. Neither cache is evidence, a
scientific checkpoint, or permission to execute.

## What is reused and what is checked

| Boundary | Behavior |
| --- | --- |
| Frozen Journal | Decode once per request. Across events, reuse only an exact source declaration, allowlist, base and source-specific relocation key. |
| Original source and projections | Rehash the source and all projected original files before cross-event reuse and again before the outer read returns. Reject nonempty WAL. No mtime-based trust. |
| Live Journal, data snapshot, manifest source | Keep the existing fresh source-reader path. Data snapshots are deliberately excluded because member paths can move independently of identical snapshot JSON. |
| Cross-source visibility | Run the ordinary dependency scheduling and transitive suppression on each requested scope. Reusing a source does not reuse another task's permissions. |
| Counterevidence | Build the reverse-counter index over the entire authorized universe, including other tasks/sources; do not restrict it to forward checkpoint dependencies. Preserve direction, basis and incomplete-coverage receipts. |
| Entry, workspace, authority | Track bytes actually observed and check again at the outer read boundary. Changed authority cannot leave a previously assembled context eligible for injection. |
| Skill/resource versions | Existing exact resource and bundle validation still runs. Selection is not evidence of actual use or scientific benefit. |

Source caches are limited to 64 frozen source views and a 32 MiB decoded cache
budget, shared with request-local collection reuse. A full cache declines new
admissions and continues the ordinary bounded source reader; it never drops
evidence to stay inside the cache. This bounds acceleration memory, not arbitrary
archive size or guaranteed wall time. Existing source, node, traversal and output
budgets still apply, and incomplete coverage remains explicit.

The private `source-read-cache` file is checksum-bound by the delivery cursor and
invalidated by the actual validation implementation hash. It stores intermediate
source results before cross-source visibility suppression. Do not export it,
present it as evidence, or load it into the model. A missing, damaged or obsolete
cache rebuilds from originals. A cache-write failure does not block a valid
delivery; receipt/cursor failures remain errors.

## Delivery semantics

1. `SessionStart` prepares a fresh baseline for the new/resumed chat.
2. The next matching `PreToolUse` rechecks sources and assembles current controls.
   Same task/workset produces a top-level delta or an unchanged receipt when that
   is smaller. Task/workset replacement produces a baseline with enabled and
   deactivated versions.
3. `PostToolUse` records the tool outcome. It does not itself claim that new
   research context was consumed. A real publication is observed at the next
   supported injection event.
4. `PreCompact` invalidates the old delivery basis. `Stop` checks the published
   checkpoint and unfinished publication metadata. Neither creates scientific
   progress or triggers RSI.
5. Failed final verification emits no assembled context or checkpoint ack.
   A recovery read must succeed before it can become a usable baseline.

The receipt separates source cache statistics (`read_stats`), prepared delivery,
and observed model consumption. Reused sources still incur original-byte checks;
zero fresh decodes does not mean zero I/O. A patch requires its exact prior context
hash, and the current controls include a full-entry recovery route.

Stable event IDs replay the exact saved receipt without rereading or rewinding a
newer cursor. A retry after a failed read needs a new host event ID; the original
failure remains. Content-only identities cannot distinguish two identical
compactions from a retry. Every such `PreCompact` observation therefore invalidates
the currently prepared baseline, even when its receipt is a duplicate. The old
receipt stays unchanged, and the next injection prepares a self-contained baseline.
This is conservative invalidation, not a count of distinct native compactions.

Tool receipts retain explicit failure signals and hashes, not raw responses or
error text. Direct MCP `isError`, a JSON-encoded MCP result, JSON-RPC errors and
Claude's `PostToolUseFailure` are observable. A successful observation receipt can
describe a failed tool; it does not make that tool successful or authorize retry.
Plain error-like text and application-domain error fields do not establish a
protocol failure. Supplied events still require native host trace correlation
before they count as native delivery evidence.

The `/3` delivery format references changed controls through
`context_delta.set_from_current_controls`, then applies `set`, then `remove`.
This keeps controls self-contained without duplicating them in the patch.
After publication, tail step bodies may exceed the summary budget; their exact
references remain in checkpoint progress and the reference index, with an
explicit `tail_details_omitted` count. Required counter and control metadata is
never removed to fit; insufficient room for it still fails closed.

## Using native Codex hooks

Generate an opt-in project bundle with the existing interface:

```python
from etalon.host import export, verify
export(target_directory, host="codex", research_entry=current_entry)
verify(target_directory)
```

The bundle contains `.codex/hooks.json` and `.etalon/research-hook.py`, bound to
the selected Python/runtime and entry. Launch Codex in that project and use its
normal project trust and `/hooks` review for the exact definitions that need
review. Check native discovery with `etalon.codex_hooks.inspect(target_directory)`.
Keep the wrapper's installed runtime alive; an MCP server on another host does
not install the hook script on the Codex execution host.

Codex's trust applies to the current hook definition hash. Installing a bundle or
configuring MCP alone does not activate new hooks. Existing trusted definitions
do not need another review; changed project paths/commands must be inspected.
See the official [Hooks documentation](https://learn.chatgpt.com/docs/hooks).

ETALON's exports retain a 30-second handler timeout. Benchmark the fixed package
against its real entry before native acceptance; extending a timeout alone is
not a solution to repeated whole-archive work. Native event receipts, actual tool
calls and the new chat's reading must be checked separately from direct handler
tests. A CLI/app-server chat is not proof that a chat is visible in the desktop
sidebar. Resource allocation and R4b are outside this change.
