# Independent side advice with a main-host decision

Use `etalon_research_advice(entry, action, topic, review_key, payload={})` or
`python -m etalon research advice --entry ENTRY --action ACTION --topic TOPIC
--review-key KEY --payload PAYLOAD.json`. The CLI reads the payload from a JSON
file. Actions are `prepare`, `bind`, `report`, `collect` and `decide`; payloads
must be objects within 8 KiB, with larger detail in referenced originals.

Keep `topic` stable for the decision being improved and reuse `review_key` across
retries and new chats. A normal result's `rsi_review` key can identify the
observation. Only material new evidence or scope justifies a new key; finish or
explicitly defer an active review before replacing it.

## Launch and continue

On the main entry, call `prepare` with:

```json
{
  "question": "Can the next stage reuse this intermediate while preserving the endpoint?",
  "brief": {
    "result_refs": ["exact selected result reference"],
    "known_costs": "measured cost and unknown parts"
  },
  "execution_scope": {
    "allowed_work": "Read selected results and relevant method skills; propose a change.",
    "allowed_paths": ["the relevant task output directory"]
  }
}
```

The example is illustrative. Include selected endpoint, conditions, contrary
evidence and cost facts, without copying the main transcript or hypotheses.
`prepare` returns `advisor_entry`, `dispatch` and `launch_required`; it does not
launch an agent. When required, the main host launches a native subagent with
fresh context, that entry and brief. Let it choose relevant skills and originals.
Then call `bind` on the main entry with
`{"handle":{"host":"HOST","id":"AGENT_ID"}}` using the actual returned handle.
The main host can continue its authorized work while the advisor reasons.

The private entry excludes inherited main authorization, budget and pending
handles. `execution_scope` is the explicit delegated boundary: it may permit a
small experiment within existing authority, or just reading and proposals. Do
not transfer the main run's completion/cancel ownership. The entries share
originals and a journal; private drafts stay private. Isolation is logical, not
an ACL.

After an interrupted prepare/launch/bind, reuse the ticket and inspect the native
host before relaunching: `launch_required=false` does not prove an agent exists
or finished. Recover and bind an existing agent where possible. If native
inspection establishes no usable dispatch remains, the main host may launch a
replacement and `bind` with `{"handle":NEW,"replaces":OLD,"reason":"inspection result"}`
when replacing a bound handle. Preserve the failed attempt and costs; never
change the review key merely to trigger another launch.

## Return one useful report

The advisor calls `report` only on `advisor_entry`, with the same topic and key.
Required fields are `outcome: "suggestion" | "no_change"` and `summary`. Optional
`proposed_change`, `conditions`, `comparison`, `costs` and `next_action` make the
finding actionable; `evidence` and `counterevidence` are arrays of actual original
file paths. Keep ideas, attempts and measured effects distinct.

Group findings into one final report per key and leave drafts in private memory.
A substantive report returns `notify_main=true`: notify once with its entry and
short actionable summary. A no-change report or replay returns false; send no
separate progress or repeated no-change messages. Retaining a method or improving
the next scientific question can be useful advice without an algorithm change.

## Decide, evaluate and retain what was learned

The main host calls `collect` at a useful decision boundary or after notification.
It imports only the final report and delivers it once. An unhandled report still
has `needs_decision`; `deliver=false` on replay does not mean it was handled.

The main host records `decide` with a stable `decision_id`, `outcome`, `reason`
and relevant `evaluation` or `handles`. Outcomes are `try`, `adopt`, `retain`,
`defer`, `reject`, `no_change` and `rollback`. `try` keeps the ticket active. The
main host can try and evaluate the suggestion itself or delegate that work to a
scoped implementation subagent. Preserve actual handles, originals and costs;
the report alone grants no execution authority.

After evaluating normal results, close the try with a new `decision_id`, its
conclusion and reconsideration conditions. Main owns adoption; a disposition
alone proves no benefit. Existing evidence can suffice, and untested suggestions
stay untested. Closing the review does not finish the scientific task.

`decide` stores released `advice_feedback` privately with status `evaluating` or
`closed`, readable after main moves on without waking a model; the same decision
retry repairs interrupted delivery. A new review receives prior compact feedback in
`previous_feedback`, with full records in main history, so it need not propose a
failed idea again. Main keeps the latest ticket per topic; the journal preserves
history. Retain useful code with `etalon_method_publish` and conditional lessons,
failures and next actions with `etalon_research_progress`; update skills only for
reusable guidance that changed.

Recover saved reports, decisions and calculations before doing more work. Repair
publication from saved outputs; a new chat or unchanged status is no reason to
regenerate a proposal or repeat computation.

If resource accumulation makes a choice difficult, the main host may instead
delegate consolidation of the newly relevant set. Distinguish duplicate runs,
parameter configurations, algorithm versions and changed usage guidance; clarify
parts and conditions rather than creating a skill per result. Return a small
proposed grouping or applicability update, retaining distinct negative cases.
Use the ordinary resources query and scoped use feedback; do not sweep every
project or rewrite history on a timer. Main adopts useful changes when needed.
