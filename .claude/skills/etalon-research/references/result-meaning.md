# Method definitions and actual results

Use this when producing a new method's output or adapting an existing result.
It is an output convention, not a prerequisite stage or an automatic judge.

`etalon.research.result_semantics` builds an ordinary JSON method/result profile:
`build_result_profile(profile_id=..., method=..., metrics=..., conditions=...)`.
Store the definition once with the method or as an existing Journal Artifact.
`profile_definitions(profile)` identifies metric/state meanings independently of
method code version, so a changed algorithm can retain its external endpoint.

`bind_scientific_result(profile, subjects=..., operation=..., conditions=...,
observations=..., assessment=...)` records the actual inputs/state/operation and
observations. Capture them from the variables the producer actually used; a
configuration label alone is not an execution observation. Keep a population or
exposure with rates, and specify units and unmeasured values. Domain-specific
extensions can describe atom selections, search coverage, assay endpoints or
reaction conditions. No universal QC or expensive extra calculation is required.

Assessment records a criterion applied to a particular state. Host interpretation
is separate: a passed criterion is not automatically a scientific success.
`summarize_result_binding` provides a read view; detailed output stays referenced.
`compare_result_context` detects declared differences, not causal equivalence or
scientific benefit. Existing evaluation contracts can replay explicit measures.

For a project note, retain the binding as `scientific_result` and its definition
identities as `definitions`, with actual evidence/counterevidence refs. Save through
`notes.save_progress`, retaining short useful knowledge, contrary facts, limits
and the next choice. Do not append run histories to SKILL.md or copy raw arrays
into the note. A read recipe may select fields without loading the entire result.

For task-specific skill/memory selection, `make_task_profile(definitions=...)`
and resource parts' `definitions` bind the meaning used. Changed or missing
definitions cannot silently activate old judgments; a genuinely independent
utility can explicitly declare a dimension irrelevant. Keep exact selected skill
versions, applicable resource parts and unknown scope on continuation. Existing
task worksets support partial reuse, adaptation and explicit template fallback.
