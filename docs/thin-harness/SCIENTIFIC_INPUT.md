# Scientific input / 1

`etalon-scientific-input/1` is an ordinary JSON Artifact. It connects scientific
evidence to an explicit domain adapter without adding a journal, scheduler or
mandatory research stage. The host can skip, replace or compose adapters.

## Layers and responsibilities

| Layer | Contents | Owner |
| --- | --- | --- |
| `sources` | Immutable file path, SHA256, byte count, precise locator, scientific scope and visibility | Data acquisition / existing Artifact |
| `observations` | What was measured or extracted; identity, endpoint, assay, units, missingness, uncertainty, source lineage | Domain extraction |
| `interpretations` | What the host assumes the evidence permits; limitations and supporting observation IDs | Host scientific judgment / existing claim |
| `bindings` | Exact tool parameter value, use, basis IDs and rationale | Domain adapter |
| Consumption receipt | Which parameters were applied, which were explicitly deferred, and why | Normal execution |

These layers are information roles, not four new workflow graphs. Evidence and
extracted reports belong to E, hypotheses and binding decisions to J, grounded
execution to X, and outcomes/experience to R. Any graph can reference the same
immutable Artifact. `ResearchJournal`, claim, research-step and checkpoint remain
the persistence/continuation interfaces.

Top-level required fields: `schema`, `task_id`, `scope`, `subject`, `sources`,
`observations`, `interpretations`, `bindings`. Every source/observation/interpretation
has a unique `id`, a `scope`, and a `role`: `conditioning`, `evaluation_only`, or
`context`. Observations and interpretations have `basis_ids`; interpretations
also declare `assumption`. Sources require absolute paths and precise `locator`s.

Each binding has a unique `id` and `target`, a JSON `value`, nonempty `basis_ids`,
`rationale`, and `use`: `constraint`, `objective`, `selection`, `evaluation`, or
`context`. Parameter names and semantics belong to the adapter. No implicit
mapping from a natural-language claim or a unit-bearing label into a tool setting.

## Visibility and identity

`validate_input` verifies original bytes and recursively checks lineage. Neither
an evaluation-only source nor a context-only source may feed an execution
constraint, objective or branch choice, including through an intermediate claim.
Missing references, cycles, duplicate targets and unsupported bindings fail.
`compile_input` accepts an explicit adapter target allowlist. Scientific execution
adapters pass a mapping of target -> required use, so relabeling a constraint as
an evaluation field cannot bypass visibility. The SND1 consumer uses this mapping. A
`consumption_receipt` requires every target to be consumed or explicitly deferred.

This checks declared lineage, not all behavior of arbitrary host code. The host
still owns source selection and scientific correctness; it must label already
seen answers honestly. In SND1, reference poses are excluded from optimization
but were seen in batch5, so this is retrospective validation.

Molecule graph, chemical state, pose, receptor frame, preparation/protocol and
execution must remain distinguishable. SND1 binds the canonical molecular graph,
exact template mapping and receptor file identity. Reported residue contacts are
grounded to model/chain/residue/insertion code/atom names before being applied.
Unsupported solvent, protonation, symmetry-mate or directional interactions are
explicit omissions, not invented observations.

## Activity and units

Activity observations require `molecule_id`, `target`, `endpoint`, `assay`,
`relation`, `unit`, `status`, and `value`. Quantified values must be positive and
finite. Unknown/unresolved values remain null, never zero. Keep original text,
source cells, normalization rule, construct, assay conditions and uncertainty.
IC50, Kd, Ki, viability, docking score and calculated energy are distinct endpoints.

The SND1 table provides an instructive import boundary: B4 contains ASCII `m`
formatted in the Symbol font, which displays mu; the normalized unit is uM.
Retain that glyph interpretation and the original workbook. The reported spread
is retained without inventing SD/SEM/CI semantics. Low signal-to-noise does not
justify a relative-affinity ranking. The present branch gate uses exact-template
MST support; Kd magnitude does not determine force constants.

## Memory and reading

Use existing `record_json`/Artifact for the input and individual observation
records. Use existing claims to distinguish extracted reports, hypotheses and
interpretations. research-step links input, MethodUse, output, receipt and cost.
Numerical weights and method assumptions belong to a versioned method/protocol.

Large coordinates stay in immutable files. Store a bounded summary containing
the full input reference, subject/frame identity, parameter-value hashes, binding
manifest and relevant scalar facts. A summary is complete only for its declared
view; it does not replace or silently truncate the original. Current generic
`read_material` is bounded to 32 KiB. Retrieve the smaller evidence needed for an
RSI decision, and follow the exact large reference when coordinates are required.

## RSI boundary

Reuse revision context -> opportunity -> method/workflow patch -> evaluation plan
-> normal MethodUse/receipt -> assessment -> MethodEvaluation -> disposition ->
experience. No fixed Probe. `assess_revision` checks attribution/readiness; the
domain evaluator supplies numerical results and the host supplies scientific
disposition. Evaluate convergence and geometry separately from the optimized
objective. Negative experience may change a later branch to skip/reuse.

MethodUse remains `host_reported` in the current journal; actual tool responses
and hashes are linked separately. Setting `tool_observed` without supported
telemetry is rejected. No new default promotion or workflow optimizer is implied.
