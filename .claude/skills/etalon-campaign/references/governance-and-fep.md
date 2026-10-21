# Handoff, admission, council and relative-network questions

Select a check for the concrete question. These are existing tools in
`etalon.mcp.governance`, not a required sequence for pure diagnostics, screening
or research. Their JSON-string arguments are JSON serialized once. No call here
runs MD/FEP or spends campaign simulation resources; network design consumes
local CPU and authorize_spend issues a signed token. Tool cost classes are not
measurements of elapsed time or a replacement for the execution ledger.

| MCP call and input | Interpret the result and decide |
| --- | --- |
| `etalon_check_handoff(records_json, receptor_path="", toolchain_seeded=False, waived="")`; records are actual md_system_input/v1 rows | Per-parent blocking, could_not_be_checked, claim qualifiers and waiver attribution. Zero blocking with unchecked items is not certified cleanliness. Receptor path is absolute and hashes the actual intended receptor. No file/row is repaired. |
| `etalon_check_stability(ligand_rmsd_nm, nanoseconds=0.0, replicas=1, contacts_json="", scored_contacts="")`; RMSD is per-frame JSON array in nm; contacts map residue to frame fraction, scored_contacts is CSV | Distinguishes leaving the site, unsettled motion and loss of scored contacts. Keep blocks_the_result separate from qualifiers and unevaluable checks; a single trajectory does not prove a correct pose or converged affinity. Reads supplied arrays, not trajectory files. |
| `etalon_rule_admissible(measurements_json, waived="")`; rows contain parent_id, cheap_value, expensive_value, observations=[{code,fired,evaluable,detail}] | Returns admitted/withheld measurements and bias notes. This is a ruling, not an automatic model fit or database import. Failed runs never become low-affinity labels. |
| `etalon_authorize_spend(records_json, receptor_path="", toolchain_seeded=False, waived="", lifetime_hours=24.0)` | Returns tokens for exact accepted row/receptor fingerprints, plus refusals. It does not reserve resources or launch a run. Default signing key is process-local unless explicitly configured; tokens can become stale after restart/expiry/input change. Do not change shared authentication to make a token work. |
| `etalon_council_reliability(votes_json, truth_json, labels)`; seat-to-votes arrays align to a boolean truth array and explicit label provenance | Measures a supplied council against actual positive and negative examples, correlation and consensus error; it does not call advisors. Single-class labels cannot establish reliability. No council can create deterministic evidence by agreement. |
| `etalon_council_adjudicate(code, ballots_json, reliability_qualified=False, parent_id="")`; at least two unique seats, vote refuse/clear/abstain and reasons | Unqualified stays unchecked. Qualified unanimous refusal may fire a fault; all-clear stays unchecked; dissent remains split. Preserve minority reasons. A passed boolean is caller provenance, not newly measured council qualification. |
| `etalon_recommend_waiver(code, reason, expires, recommended_by)` | Returns a recommendation with explicit human-grant boundary. It cannot grant or spend. Keep model attribution honest and at least 24 characters of specific reason. Use existing user grants only when actually applicable. |

Existing granted waivers use JSON array entries `{code, reason, granted_by,
expires}`. Bare fault-code lists, expired entries and ineligible causes cannot
silently release checks. Do not fabricate a grantor. If a genuinely new waiver
is required, report the named fault and exact reason for human input; unrelated
already authorized work continues. Waiver syntax validation cannot prove that
the named person agreed.

## A direct SDK call with a deliberately unresolved outcome

```python
from etalon.council.ballot import Ballot, Vote, outcome_for
ballots = (
    Ballot("fixture-a", "F_PROTONATION_UNDECIDED", Vote.CLEAR, "synthetic clear opinion"),
    Ballot("fixture-b", "F_PROTONATION_UNDECIDED", Vote.CLEAR, "synthetic clear opinion"),
)
result = outcome_for(ballots, qualified=True)
assert result.value == "cleared_but_still_unchecked"
```

This function computes only a bounded council outcome from synthetic ballots.
It does not grant a waiver or make a measurement evaluable. Known SDK users can
call `faults.preflight.check_record`, `faults.stability.check_stability`,
`learn.admissible.admissible`, `authority.grant.authorize`, and
`council.reliability.rule` directly with their typed inputs.

## Relative network design is not relative free-energy execution

`etalon_design_fep_network(molecules_json, references="", min_core_fraction=.5,
cycle_edges=0)` takes `{id: smiles}` and CSV IDs with real measured references.
The SDK equivalent is `etalon.fep.network.design(molecules, references=(),
min_core_fraction=.5, cycle_edges=0)`; it returns a Network with `as_dict()` and
`render()`. It uses installed RDKit graph/mapping logic and local CPU, not a
simulation executor. Unparseable molecules are reported as unreachable while
valid pairs can still be designed; missing dependencies prevent the call. Stop
claims for unsupported members without discarding the partial result. Do not
install/upgrade a shared environment as a side effect of reading a skill.

Inspect usable edges, connected components, unreachable molecules, anchor
references and cycles. A component with no measured anchor supports relative
differences only. A tree has no cycle-closure check; an extra edge is a proposed
check with execution cost, not an observed error estimate. Reframe unsupported
chemotypes, obtain an appropriate reference or stop rather than force degenerate
mappings. Physical feasibility, preparation, charge/state, force field and
convergence still require their own evidence. The registered ETALON PRISM path
does not thereby become a complete FEP-network execution implementation.
