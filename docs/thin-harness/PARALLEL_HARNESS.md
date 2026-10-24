# Parallel harness increment

This increment lets memory, skill applicability and host delivery advance while
scientific runs stay bound to their own fixed code and inputs. Its base is
`abd061eaaf15`; deployment is a separate, explicit transition. R4b stays paused.

## Common contract

- Reuse ResearchJournal, Artifact, evidence refs, MethodUse, research-step,
  checkpoint, publication and task workset. No new research database or router.
- Original record IDs and digests survive relocation. A path mapping changes
  location, never identity, scientific authority or the meaning of a result.
- Exact skill versions travel with the entry through an explicit, bounded
  `skill_bundle_map`. Updating the development library must not change a frozen
  task's skill. Missing versions fail; an archived version is never silently
  replaced with the current one. Context and host exports use the same locator.
- Selected, delivered, read, consumed and executed are distinct observations.
  Missing evidence remains unknown; a host declaration is labelled as such.
- A current task workset controls applicability. A stored result or resource
  outside that workset remains reference material, not an active default.
- A failure, counterexample or superseded conclusion stays addressable. Bounds
  cannot silently remove counterevidence or weaken original-byte verification.
- Static installation, host review, authentication, MCP connectivity and actual
  native event delivery have separate verification states.
- No new fixed Probe phase, automatic scientific execution, resource allocator,
  automatic adoption or R4b resumption is introduced.

## Independent ownership

| Workstream | Owned implementation | Integration surface |
| --- | --- | --- |
| Memory | `research/handoff.py`, entry handoff tests and documentation | Bounded plan, export, verification and relocation of a complete released entry |
| Skill and meta | `research/resource_usage.py`, `meta_context.py`, `method_links.py`, associated skills/tests | Workset resource/part to actual use, node, evaluation and subsequent decision |
| Native hooks | `codex_hooks.py`, `host_install.py`, `research_hooks.py`, associated tests/docs | Capability-aware inspection, separate verification levels and conservative recovery |
| Integration | MCP seams, combined acceptance, pre-CNN diagnostic inputs | One usable surface and an evidence report covering cross-module behavior |

Each workstream can implement and test without waiting for the other streams.
Shared-file changes go through the integration owner. Existing experiment trees
and prior acceptance outputs remain immutable. New scientific analysis uses a
separate output directory and reports reanalysis separately from new execution.

## Integrated acceptance

1. Export a complete SND1 or PoseBusters entry; verify and relocate it; read the
   same checkpoint, progress, original evidence and counterevidence afterward.
2. Resolve a selected resource's exact part and declared actual use through its
   node and evaluation. Unsupported links fail; absent observations stay unknown.
3. Recover hook delivery after compaction/retry and expose an actual failure
   without claiming that protocol fixtures prove native-host event delivery.
4. Keep SND1/PoseBusters task scope and template fallback distinct. Re-reading
   or moving a completed task must not launch computation or propose RSI again.
5. Keep scientific development running independently: pre-CNN diagnostic inputs
   may use the CPU pose pool and conditioning receptor, never gold poses or CNN
   outcomes. Retrospective analysis is development evidence, not transfer gain.

Linux authentication or host review may block the final native model turn. It
does not block local engineering, protocol checks or independent scientific work.
