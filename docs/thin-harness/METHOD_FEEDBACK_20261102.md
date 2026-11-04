# Make method feedback useful to the next research decision

This increment improves the existing host-authored method path. It adds no research
stages, mandatory method reuse, observation policy, stopping rule, or new model call.
The host continues to choose the goal, method, adaptation and next action.

## Problems reproduced and changes

1. **A failed native program hid its useful exception.** `runtime.method.read_result`
   returned a generic native-failure summary, although the exact stderr was archived.
   Failed results now include `failure.kind`, exit code, missing output names, the
   archived stderr locator and a tail read of at most 2 KiB. A truncated tail is
   marked. UTF-8 decoding uses replacement for undecodable bytes; the original log
   remains authoritative. A log-reading problem stays separate from program failure.
   Actual execution cost remains charged. Collection does not retry computation.

2. **Large results hid small decisions.** `method_collect` replaced the complete
   output with a locator above 8 KiB, losing the summary and compact feedback as well
   as the large array. It now also returns `output_summaries`, using the same preview
   as `action_result(view="summary")`. Status, author summary, method identity,
   failure details and feedback up to 2 KiB remain visible. Larger feedback has an
   explicit `/feedback` read locator. Existing small output bodies and large-output
   locators remain compatible; original arrays, hashes and receipts are unchanged.

3. **Missing fold assignments could produce incomplete cross-fitting silently.**
   `NaN != NaN` left a row without its held-out prediction while other folds still
   fitted. Missing/non-comparable assignments now fail before any fit is invoked.
   The author still chooses features, split, model and objective. No scientific
   fold-selection policy is introduced.

4. **The sweep tests contained shadowed definitions.** Removed 37 duplicate test
   definitions and two duplicate helpers. Every removed definition had exactly the
   same AST as the later effective definition. This removes ambiguity, not adds test
   coverage. Across all 289 top-level test modules no duplicate top-level
   function/class names remained. Two old progress fixtures were updated to provide
   their real `workspace` attribute; the unreadable-CSV fixture now injects the same
   stat error without requiring Windows symbolic-link privileges.

## Consumer contract and example

```python
from etalon.research.saved_methods import collect_saved_method

result = collect_saved_method(invocation_path)
# Decide from compact author feedback; raw results remain accessible when useful.
for output in result["output_summaries"]:
    print(output["summary"])
    print(output["read"])
print(result["accounting_refs"])
```

The producer is the existing native `method.run` adapter. Consumers are the Python
saved-method API, MCP `etalon_method_collect`/`etalon_method_call`, and the existing
action summary view. New fields are additive. This does not change submitted
commands, request identity, result archival, accounting units or scientific admission.
Reading the feedback is optional; the environment does not decide whether to stop.

## Validation and its boundary

Local interpreter: the existing ETALON `.venv/Scripts/python.exe`, importing
`src/etalon` in this checkout. No dependency upgrades or remote deployment.

- The five new regression cases failed before the fixes and passed afterward.
- Method/runtime/saved-method/cross-fitting/action-view checks: **36 passed,
  4 skipped**. The skips retain their existing platform conditions.
- Native publication, feedback and MCP skill delivery checks: **15 passed**.
  Five feedback tests overlap the preceding group; these counts must not be added
  as distinct coverage.
- The sweep run exposed **200 passes and 18 failures**. Three fixture problems
  were fixed and independently rerun: **3 passed**. The remaining 15 failures
  share the existing POSIX-only legacy ledger lock. A before-change test snapshot
  reproduced the same error. They remain unresolved on Windows, not silently skipped.
- The native-program cases execute actual subprocesses and check retained logs,
  measured nonzero wall cost, failure behavior, unchanged arrays, and collection
  without a second launch. The test foreground controller substitutes for launching
  the Linux-oriented durable controller. This is not full cross-platform service
  acceptance or a scientific efficacy experiment.

In the actual 20,000-value fixture, the full action response was **133,255 bytes**;
collection including the decision and feedback was **3,605 bytes**. The full array
remained readable. Previously collection was already short but lacked these decision
facts; the gain is making them available without a compulsory full-result read.
These are serialized bytes, not measured model tokens or demonstrated research savings.

## What still needs work

The old campaign ledger and durable workflow controller still have Linux-specific
ownership/locking requirements. The newer native-attempt layer has broader process
support; that does not establish support for every upper layer. Platform capability
and deployment tests should be addressed as an execution task, not by weakening
concurrency protection or declaring the entire suite green.

The model's cumulative formation/adaptation/failure expenditure and scientific value
also remain distinct questions. Better feedback makes decisions possible; it does
not establish that retention, extra observation, or self-improvement pays for itself.
The Evolve comparisons did not establish that advantage.

Existing checkout edits were preserved. This increment was left uncommitted under
the local collaboration rule; no unrelated changes were staged or published.
Paused scientific batches and original execution snapshots were not resumed or altered.
