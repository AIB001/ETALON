# Evidence-driven changes during ordinary research

`etalon.research.improvement` connects existing research memory to optional
method/workflow revision. The host initiates it for a concrete problem; it has
no scheduler, mandatory probe rollout, implicit model call or resource allocator.

New JSON contracts are revision-context, revision-opportunity, method-patch,
revision-assessment and method-experience, all `/1` ordinary Journal Artifacts.
They reuse `continuation.record_content`, evidence query/read and checkpoint
reference traversal. Existing MethodVersion/Use/Evaluation/Revision and decision
steps retain their semantics and independent-validation requirements.

The public example `examples/research_normal_improvement.py` accepts one released
research entry and original native execution trace. It preserves that trace as
problem evidence, binds a new reader method and optional graph edits, records a
waiting assessment, then reads the existing result as part of normal research
continuation. It compares the read with the original published result without
rerunning the producer. The comparison is a scoped engineering repair; it is
not an independent model/workflow/scientific-benefit benchmark.

`build_revision_context` is read-only and reports missing/bounded evidence.
`register_opportunity` uses semantic evidence/target/condition identity and the
existing host lock primitive. Its request ID remains stable across duplicate
events and chats; interrupted generation is `resume_pending`. External side
effects still require the executor's own idempotency/recovery contract.

`build_method_patch` requires a complete candidate version with the exact base
as a parent. Optional graph edits use explicit before/after values and validate
the complete resulting graph. The host publishes it and applies the candidate
explicitly; reading a patch does not change defaults or run a workflow.

`assess_revision` binds an evaluation plan to normal-use node receipts, method
attribution and exact conditions. It returns awaiting_evidence or readiness for
evaluation, never an inferred improvement score. Host judgments, correlated
historical comparisons and costs must remain explicit. A method can be used
locally before benefits are known without calling it generally adopted.

Experience is append-only; successors link old entries. Retrieval matches exact
required condition values, preserves negative/unresolved results and provides
coverage and pagination. It does not automatically select the latest or promote
a method. More sophisticated retrieval/experience consolidation is outside this
increment. No new evidence role or global memory database is introduced.

Installed-host usage is in `host-guides/normal-use-improvement.md`. Known APIs and
component-native skills remain usable directly. R4b authority is unchanged.
