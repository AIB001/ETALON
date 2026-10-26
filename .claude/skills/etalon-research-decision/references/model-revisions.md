# Optional testable research models

Use `etalon.research.research_model` to describe a small, explicit research scope.
`build_research_model` verifies the referenced originals and returns an ordinary
Artifact body. Save it through existing `record_json`; no second store, agent
router or mandatory stage is introduced. Each rule has a stable ID, applicability
and unknowns, an optional checker/predictor reference, dependencies and an explicit
verdict mapping. Referencing a checker does not execute it.

`recorded_rule_assessment` binds a case, observation JSON pointer and completed
normal-use research node. `review_model_revision` compares explicit rule mappings
and those recorded observations. `read_model_revision` rechecks the stored review.
See `examples/research_model_review.py` for the explicit request shape and bounded
existing-report workflow. Do not construct TRUE/FALSE verdicts from narrative
confidence, a missing field or unknown applicability.

Distinguish unchanged/rename, split/merge, generalize/specialize, withdraw and add.
Keep negative witnesses and unresolved issue identity through every revision.
FALSE becoming NA or UNKNOWN does not resolve the original counterexample.
Reinterpreting identical originals can improve a representation without producing
independent scientific evidence. A review neither adopts a method nor reopens a
terminal RSI opportunity. Those decisions still use existing evaluation,
experience and opportunity interfaces with their original conditions.

For pose analysis, `pose_verifier.verify_pose_endpoints` is an optional versioned
protocol. It uses a frozen installed check contract, exact input pins and per-slot
qualification Artifacts. `result_integrity.validate_endpoint_verifier` rechecks
the report. Physical checks can be available while reference-dependent RMSD and
joint success remain unknown. Joint success still requires all required checks
and qualified RMSD at the frozen threshold. Never relabel an old unknown as a pass.
