# Explicit capability evidence

`build_capability_profile` in `etalon.research.capability_profile` reads an explicit
bounded list of research decisions, updates, case declarations and evaluation
contracts. It replays the existing evaluator; it does not run science, update
defaults, assert a globally latest state or create another registry.

Each study names `decision_ref`, nullable `update_ref` and
`evaluation_contract_ref`, `case_ref`, `case_id`, `family_id`, and `repeat_id`.
The case original must contain the same case/family labels and be linked from
the decision. The update must name that exact decision. The evaluation contract
must be linked from the decision or update and belong to the same task.

Groups separate exact problem structure/scope, model, skill, method and verifier
references, evaluation subjects/comparators, and rule digest. Missing version
identity stays unknown. Original case labels are verified declarations, not a
sequence-similarity analysis or an independent-sampling certificate.

`distinct_result_bundles` counts distinct verified result byte bundles. Renamed
Artifact records, extra context inputs and changed evaluation rules do not add
result bytes. All assessments remain in `rows` and their exact version/rule
groups, including contrary evaluations. `duplicate_observations` points to reused
bundles; status counts count assessments, not independent outcomes. Result JSON's
own bytes are retained even if it references shared inputs. An altered JSON
wrapper is different bytes but is not proof of another scientific observation:
`unique_observations` and `independent_case_count` remain null. The existing
bounded reader verifies complete original bytes. Repeated runs in one declared case
remain one case cluster. Metric outcomes, named quality/cost families, prediction
judgments, missing evidence and counterrefs remain separate. No average of unlike
endpoints, aggregate success probability, confidence interval or automatic
promotion is manufactured. Prediction judgments remain host judgments.

The view supports at most 32 explicit studies and 512 KiB output. Large input
contracts continue through the existing verified evaluator; decision/update/case
metadata must be complete within the structured-reader bound. Unknown lifecycle
cost is null, not zero. Read failures are explicit rather than silently dropping
an inconvenient case. Consumers can archive the view as an ordinary Artifact,
but its numbers should be reproduced from the linked original inputs.
