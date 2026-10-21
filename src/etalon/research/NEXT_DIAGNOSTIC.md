# Next diagnostic after released structure validation

Historical planning record: the capability and new-task work below has since
been implemented and independently evaluated in [R3B.md](R3B.md). Its original
unsupported/pending statements describe the planning cutoff, not current status.

This is a development proposal, not a promoted revision. Frozen decision
`0201c9bbf0637ff88522704ed44ca91deb977f4fe3fa694f2f0e1323799473cb`
retains its empty delta. Its independent validation is an appended
`etalon-decision-validation/1` record, not a phi validation or transfer result.
1XEP is now seen development feedback. No further revision may call it unseen.

## Question and executable boundary

Test whether fixing every phenol heavy-atom coordinate excludes relevant
catechol modes. Preserve chemical graph, charges, atom mapping, receptor and
coordinate frame. C2/C6 remain two mappings of one chemistry.

PRISM's `docs/STRUCTURAL_MODE_INTERVENTIONS.md` and actual preflight show that
smaller masks, increased tolerance and `fixed_coordinates=false` cannot implement
this test. The v1 executor fixes the graph core coordinates unconditionally.
The two schema-valid fresh-ID mapping request drafts remain executable through
`examples/diagnostic_round3.py`; use their archived results as baseline rather
than regenerate. Optional regeneration has a 120-wall-second reservation ceiling,
not a measured runtime estimate. No execution is requested by this proposal.

Candidate joint configuration delta (proposed semantics, no installed wire fields):

| Component | Baseline | Candidate |
|---|---|---|
| Design acceptance | Graph-preserving growth with absolute source-core position check | Same exact mapped graph/edit provenance; separate reviewed pose-constraint artifact |
| Preparation | C2/C6, four original conditional seeds per mapping, all seven heavy atoms fixed | C2/C6, four declared proper rigid transforms of each already frozen starting pose |
| Pose acceptance | Old absolute core displacement threshold | Reconstruction against declared transform and displacement bounds; old core displacement remains reported |
| Diagnostic readout | N/O pair count within 3.5 A, complete clashes | Same numeric definition, separately bound to new pose policy |
| Independent objective | No optimization objective evaluated for initial decision | Fixed structural coverage loss below; never proximity or affinity |

Initial finite transform proposal: identity, +15 degrees, -15 degrees and
180 degrees around the mapped aromatic ring normal through its centroid.
Freeze signed normal construction, ordered transforms, reference coordinates,
units and proper rotation matrices before dispatch. These are engineering
development probes, not a global pose search or guaranteed valid binding poses.
Reject clashes/invalid reconstruction explicitly; retain all eight attempts,
duplicates and failures. No receptor minimization, docking or MD is implied.

This candidate needs PRISM's proposed `separate-graph-pose-constraints/1` schema,
sampler and verifier. It is currently unsupported. It cannot be represented as
an executable v1 request or an applied Revision. Producer capability and measured
cost quote must exist before ETALON reserves and dispatches it. Compatibility:
keep v1 schema and evidence immutable; select a new capability/method explicitly.

## Fixed proposed J and resource comparison

For a structural validation task with prespecified experimental modes m, define
`d(m,p)` as heavy-atom RMSD after a single proper receptor CA alignment and exact
graph symmetries, without free ligand fitting. Fix
`J(P) = max_m min(6 A, min_{p in accepted P} d(m,p))`.
An empty accepted set has J = 6 A. Lower is better. This objective measures worst
reference-mode coverage, not the accuracy of an independently ranked best pose.
Report each mode's unclipped minimum, all slot outcomes and acceptance fraction;
do not tune the 6 A cap or mode set after seeing the next labels. It is explicitly
a proposed future protocol, not a retroactive score for the frozen decision.

Compare fixed-core and finite-transform policies on identical task inputs,
eight attempted slots, the same hard wall-time cap, single thread and identical
measurement definitions. Keep capped failures in the denominator. Baseline
artifact reuse has zero new producer execution, while its original cost remains
visible for the matched-resource scientific comparison. Include a static
predeclared transform policy and the existing fixed mapping policy as controls;
adaptive q must earn any advantage at matched total diagnostic/preparation cost.

## Next validation task and calibration gate

Proposed task ID: `t4-mode-generalization-v1`, status `awaiting_coordinator_input_freeze`.
It must bind a different ligand/chemical task with compatible structural modes,
or a genuinely unexposed independent endpoint. A possible candidate for
coordinator screening is 2-methoxyphenol; availability of suitable independent
data has NOT been established here. Do not fetch labels or designate this a
real frozen task without receptor/graph/state/frame/input hashes, mode definition,
access history and evaluator ownership. This missing binding is explicit; a new
name for the already seen 1XEP case does not create independence.

Before the next run, coordinator must freeze that input and evaluator protocol.
The consumer public interface is ready for the receipt; it does not fabricate
a new independent task or require routine reapproval of existing resources.

`calibration.bind_outcome` binds actual admitted observations, condition identity,
units and measurement method to declared finite probe bins. Current real data
give counts 0 and 2 under the two conditions; threshold 1 is a POST HOC development
projection, not a validated chemical selection rule. `diagnostic_calibration_round3.py`
persists those bindings and reports one independent task, no fitted model,
no mechanism posterior and no expected gain. Eight correlated slots are not
eight training examples.

`calibration.empirical_model` can feed the existing exact q solver only after
complete paired development-task outcomes and measured candidate losses under
fixed J are supplied. It enforces disjoint held-out tasks and one row per task
and declared independence group. Row evidence and independence declarations
still require scientific review; the API cannot infer independence or causal
labels from files. Equal task weights define an empirical predictive model,
not HD/HP/HE probabilities. Two rows merely satisfy the software minimum, not
statistical adequacy. Evaluate calibration and q versus myopic/static controls
on separate frozen tasks before claiming benefit or phi transfer.

Until these inputs and the new capability exist, the justified next action is
capability implementation plus a bounded smoke test under PRISM ownership,
followed by task/model/protocol freeze. No chemical efficacy or joint-revision
benefit has been demonstrated by the XOR fixtures or this structural case.
