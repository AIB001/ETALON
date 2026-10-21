# R3b: executed joint revision and independent feedback

This batch implemented and executed graph/coordinate-policy separation, a real
fixed-version scorer consumer, a factorial development panel, a nonempty joint
Revision, a frozen new-task prediction and a released independent feedback
transition. It does not establish learned improvement policies or complete RSI.

## Public entries and distinct scientific roles

- `examples/pose_round3.py`: pinned PRISM pose contracts, one shared CampaignStore,
  reserve before dispatch, actual cost settlement including failures, no duplicate
  dispatch/charge on acknowledged replay. Venv executable symlinks are preserved.
- `examples/joint_revision_round3.py`: initialize, assemble, freeze, predictions,
  feedback. Exact task/candidate/mapping/pose identities remain separate. Missing
  and rejected slots remain in the eight-attempt denominator.
- `research/controller.py`: executable hand-authored diagnostic schedules,
  observation-driven state transitions, model challenges and stop conditions.
  Choosing a P/E workflow is recorded as `selected_workflow`, never as phi learning.
- `examples/audit_joint_round3.py`: actual stage costs, seed/transform allocation,
  duplicates, eligible poses and scorer cost subsets. E0-only counterfactual cost
  is unavailable; equal caps do not imply equal actual costs.
- `examples/archive_joint_feedback.py`: a consistent complete H snapshot/index,
  including all artifact identities, events and execution receipts before and
  after controller registration. Hypotheses are a separate field, not H itself.
- `examples/diagnostic_selection_gap_round3.py`: seen-feedback reanalysis of
  coverage versus score-selected pose errors, with explicit exposure provenance.

The producer remains PRISM's `separate-graph-pose-constraints/1`. Old diagnostic
v1 contracts remain unchanged. The new request/result hashes are
`776292f3f5e4fe9fd58813ee37615eb570312a03910da39e8550d46f05e64c0f` and
`2bfd4dc64cb671509390c44adf31b72c45429fa2c3955c2796642a664fc3fe3a`.

P0 uses four fixed-core seed slots per mapping. P1 uses the first shared seed
with identity, 180, +30 and -30 degree proper rotations about the source aromatic
ring normal. Two mappings give eight slots per chemical candidate and policy.
Only the first-seed identity is directly paired; the full intervention changes
both coordinate policy and seed/transform allocation. It is not a pure estimate
of releasing coordinates. Duplicate slots remain visible.

E0 maximizes all ligand N/O–receptor N/O pairs within 3.5 A. E1 minimizes Vina
1.2.7 score-only total with Meeko 0.7.1 preparation; it never optimizes/docks.
Neither is experimental affinity. Units/directions differ and are never subtracted
across E. The scientific endpoint is independently supplied experimental pair
order: correct 0, wrong 1, tie/abstain 0.5, all failed 1. Tie tolerance is 1e-6.
Scorer failure does not remove a geometrically eligible E0 pose. Failed or
unattempted target slots retain intended target chemistry and no invented pose.

## Actual evidence and bounded outcome

The formal Linux run used consumer `etalon-pose-722e27ccce93` and producer
`pose-diagnostic-f43a02a49df4`. Later consumer hardening in the working checkout
is not represented as part of that immutable run. The first smoke failed because
resolving a venv launcher selected base Python; its 0.4973598029464483 seconds
remain charged. Corrected public API/CLI operations and replay then succeeded.
The actual Linux consumer regression set passed eight tests.

Development decision:
`d59cda7171f776306b5e5f85001bf2abda0a67cc6d5b27dc25565580038afe65`.
Nonempty Revision:
`revision-e1f12fdd95f7df8139d8a484fe0cb92c65f36097e1970af251ec2d3c2b6ef2a3`.
It changes pose admission, preparation and evaluation, keeps the exact chemical
graph policy, and remains proposed/unpromoted.

Predictions file SHA256:
`16acd5b61bb271f348f329f64260bb538b4fa3bf6f983cc2fcee8179233db621`.
Development freeze event 229 precedes the first new-task reservation event 230.

| Workflow | Development phenol/catechol loss | Held-out ortho-substituent pair loss |
|---|---:|---:|
| P0/E0 | 1 | 1 |
| P0/E1 | 1 | 0 |
| P1/E0 | 1 | 1 |
| P1/E1, selected before new-task output | 0 | 0 |

The frozen workflow correctly chose 2-methylphenol over 2-methoxyphenol on the
retrospective same-target holdout. It did not outperform E1-only, copy-best or
static P1/E1 there. The preparation intervention's apparent necessity therefore
depends on the task. One pair cannot estimate broad transfer accuracy or calibrate
a causal mechanism posterior.

Auxiliary worst-mode structural coverage improved from 2.7249 to 1.4275 A for
2-methoxyphenol and from 1.9688 to 1.0956 A for 2-methylphenol. But the latter's
Vina-selected pose best-mode RMSD changed from 1.9688 to 1.9791 A. Oracle coverage
and selected-pose accuracy are different metrics. The released-feedback audit
also computes selected worst-mode error minus same-mode oracle coverage without
changing the frozen choices or reusing those labels as unseen validation.

Metric correction: the first supplemental audit called that difference
`selection_regret_A`. That label is deprecated; v2 uses
`single_pose_vs_ensemble_coverage_gap_A`. The single-pose and ensemble objectives
differ, so this is not ranking regret. The original report is retained; no true
same-loss ranking regret is asserted from this calculation.

All 18 scientific execution receipts, including failed smoke, total
56.29114627279341 wall seconds against a 1200-second budget; pending reservation
is zero. Environment setup, tests and independent/postprocessing validation costs
remain separate. Scorer timing is a subset of action cost, never recharged.
No active-learning experimental-affinity labels were created.

Independent consumer audits verified 1460 prediction-delivery files, 51 frozen
prediction references, 112 measured readouts recomputed from raw measurements,
16 failed/missing readouts, and the unchanged decision/prediction artifacts.
The feedback delivery has 1046 verified files and a complete H snapshot with
377 research records, 116 artifact references, 414 events and all 18 cost records.
Feedback replay adds no events or cost. The original archived database remains
unchanged; feedback is appended to the live campaign and separately snapshotted.

## Final consumer compatibility supplement

The later consumer `etalon-pose-57ca69edd743` was separately verified on actual
Linux with the original isolated interpreter and PRISM `f43a02a49df4`. All nine
v4 frozen files match the executed snapshot. Six directed Linux regressions
passed; the local six-test suite also exercised the public producer CLI on an
explicitly synthetic fixture. These are separate checks, not twelve independent
scientific validations. The original scientific panel remains on `722e27ccce93`.

The first compatibility supplement, v3 (`etalon-pose-7c45e0933551`), failed on a
real historical receipt. The original and archived requests had equal `inputs`
dictionaries but different key order. Reconstructing Artifact parents in that
order changed derived identities and collided with the immutable admission ID.
The failed backup appended 17 partial derived records/events (414 to 431); it
was not idempotent. Its archive and logs remain preserved, and the original
campaign was unchanged. A source-kind change was an additional replay risk,
not the observed cause: the historical source kind was already
`existing_real_artifact`.

V4 verifies request/result identities, artifact bytes and stored evidence, then
returns the original admission before any derived writes. It preserves historical
provenance and sorts input names for first admission. Legacy controller steps
without `admission_record_id` are read through their recorded result artifact and
execution identities. On a fresh backup of the original H, actual FAILED
validation-00 and COMPLETED validation-01 replayed with zero new events, executions
or fees. Public feedback appended one compatibility record; its repeat added zero
events and returned the same output. Journal metric reanalysis appended ten
records. All 396 original metadata bodies remain unchanged; the new H contains
388 research records, 117 artifact references, 425 events and the same 18 receipts.

The complete supplement contains 2074 files. Total-control independent acceptance
verified every file hash/size, all H artifact references, the old SQLite metadata
and all nine frozen source hashes. All 1018 original campaign files, the frozen
decision/predictions and scientific budget remain unchanged. Four v2 metric groups
were recomputed from released feedback; `ranking_regret` remains null. The failed
v3 processing used 1.515130264684558 wall seconds; successful v4 processing used
2.786674229428172 wall seconds. These subprocess costs exclude transfer, packaging
and inspection, and remain separate from scientific execution costs.

Verification scope is narrower than rerunning the panel: new-decision request-list
binding and diagnostic-assessment writing have local test coverage, while Linux
checked historical-receipt compatibility and released-data reanalysis. No new
scientific dispatch or promotion occurred. Full source comparison against all
997 files in the actual scientific archive finds 989 identical files, eight
changed files and no deleted source files; six post-run additions and seven
preexisting packaging omissions are listed separately in the local final audit.
Cross-repository acceptance and evidence locations are maintained in
`integration/round3b/ACCEPTANCE.md` at the workspace root.

## Research state after feedback

The actual controller reaches `additional_tasks_required`, with `phi_learned=false`,
`expected_gain=null` and no promotion. It records the independent comparison and
remaining model challenges, not a generic success/completed research state.
1XEP, 3HT6, 3HT9 and both evaluated chemical pairs are now seen development
feedback for any future revision.

There was one development chemical pair and one retrospective same-target holdout,
not eight independent training samples per pose family. Multiple independent task
groups, query-conditioned losses and actual query costs are still missing for
calibrated empirical q selection or learning phi. Current hand-authored controller,
copy-best and static replay are executable controls; myopic/exact finite query
value comparisons remain conditional synthetic/empirical interfaces until those
data exist. They are not measured chemical-efficiency gains.

The next discriminating work is fixed-pose-pool scorer comparison and reviewed
receptor/state/solvation controls on new frozen task groups. New control capabilities
and their runtime quotes are not registered in this run. This is a concrete
execution/data gap, not a request for routine reapproval of already authorized
work. Preserve failed outcomes, the coverage/selection counterexample, source
versions and cost controls when extending the research archive.
