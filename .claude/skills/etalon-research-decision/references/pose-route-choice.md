# Let pose and full-route evidence change the next choice

Use this optional helper when the choice depends on both a specified molecular
state in a receptor and a complete route to that exact state. State the actual
objective before declaring required facts. A local geometric endpoint can support
choosing a reference or follow-up calculation; it is not a binding-affinity goal.

`etalon.research.reaction_candidate.choose_pose_route` takes pose options, full
route bindings and task-defined Boolean/unknown facts with evidence and scope.
It returns the compatible pairs supported for the stated objective, unresolved
requirements, and caller-proposed next actions. Keep ties and unknowns. It does
not make a weighted total score, execute the actions, or provide a mandatory gate.
A requirement that is false excludes an option from this particular shortlist,
not from all future research. Change the objective when the science calls for it.

Bind complete routes through `bind_pose_route`. Canonical identity preserves graph,
stereochemistry, charge, salt components and tautomer. Different coordinates of
the same state can share a route. A different state needs its own compatible route;
do not silently reuse the original product's route, yield or pose evidence.
`compile_reaction_candidate` replaces an exact internal edge while preserving the
boundary product and each original precursor subtree. It is not a new-molecule
generator. No graph edit is required just to demonstrate a joint decision.

Use `route_dependency_manifest` and `pose_route_dependency_keys` to describe the
dependencies separately. They do not launch an executor or establish cache
completeness. Supply the actual original method/runtime/model identities and the
full pose request, including coordinates, receptor/preparation and protocol.

- Pose-coordinate changes affect the pose readout; they need not rerun synthesis.
- Route graph/template changes affect native graph evaluation; unchanged exact
  precursor subtrees and compatible edge computations remain reusable.
- Process/source changes affect the process assessment. A graph-only filter that
  never consumed conditions cannot confirm a new solvent, drying step or yield.
- Inventory updates affect sourcing. A fixed stock database miss is not proof of
  present supplier unavailability. Compile-time leaf flags and completed native
  stock observations are different layers; retain discrepancies and provenance.

The executable `examples/pose_route_selection.py` consumes pinned prior QOV
originals, verifies actual SDF states, original native contracts and route graph
inputs, then compares the scoped joint choice with evidence omitted. The omitted
evidence checks are retrospective sensitivity checks, not independent experiments
or a fair end-to-end algorithm benchmark. Case history stays in the result files.

Select an unresolved route-sourcing or exact-target condition question when that
is the useful next action. Preserve unknown full yield, current procurement,
chemical selectivity and affinity separately. After acting, use the normal result
and feedback path; do not repeat a completed MD or route search merely to show
that reuse works. Broader adoption requires actual quality and total-cost evidence.
