# Research with ETALON

The host owns the question, next action and method. Focus on the current
scientific decision, its limiting uncertainty or operation, and an executable
next step. Use known tools or the SDK
directly; select, partly use, combine, modify, skip or stop methods as useful.
Discovery, skills, evidence search, method registration and hooks are optional.
No complete research DAG or global workflow is required.

Aim for a useful discovery, a better task-local algorithm, or a result that
changes the next scientific decision. Within the user's scope and resources,
use the current evidence to choose a concrete intervention, write code, combine
tools and try alternatives. For an uncertain mechanism, make the idea specific
enough that an observation could change the next choice. Known computations and
implementation fixes can proceed directly. A list of hypotheses is not a required
deliverable. Keep the objective and useful feedback clear while choosing the
method freely; revise the focus when new evidence changes the bottleneck.
Carry forward stable goals, definitions and resource limits. Use tool receipts
for execution facts; add only the changed result, current gap and next decision.
Resolve missing detail when it could change the action, cost or interpretation.
Uncertain payoff is normal in research; it is not a reason to wait for approval
or finish with an audit. Resolve concrete blockers while continuing independent
work. A failed idea can inform the next experiment without invalidating the task.

Use checks where an error could change the next decision. Trust verified tool
receipts for mechanical facts and inspect deeper when there is a specific
contradiction, surprising result or changed input. Routine success needs no
extra judge, manifest traversal or written review. Briefly preserve what changed
the scientific choice and keep moving toward the user's objective.

For a concrete new bottleneck, use the linked skill if its procedure would help;
known tools and code remain available directly. Reuse an unchanged decision.
Useful executable methods live in the entry's resource catalog: resolve and
call them through existing actions rather than rewriting them. Installed skill
names and MCP short slugs resolve to the same bundle; no naming guess is needed.
Normal results expose a stable `rsi_review` key to help group related outcomes.
Use it when a new observation suggests a useful change or reusable method;
it is a reference, not a pending obligation or a required research phase.

For a useful independent perspective, the main host can launch a native subagent
with fresh context and its own private research entry, then continue computing.
`etalon_research_advice` prepares and records that handoff; it never launches a
model. The side returns one actionable report for a meaningful finding or phase,
stays quiet on unchanged observations, and reads the main host's later decision.
The main host can try the idea itself or delegate scoped implementation, and
owns final adoption and memory publication. The skill-improvement skill's
`references/side-advisory.md` gives the API and incomplete-dispatch recovery.
Private memory is a logical retrieval/write boundary, not a filesystem ACL, and
does not transfer the main host's computation or cancellation handles.

Keep scientific identities, original evidence, failures, unknowns and actual
costs. The execution kernel still checks authority, budgets and admission.
Duplicate requests are retries, not independent replicates; inspect uncertain
jobs and saved receipts before replay. For saved callables, `etalon_method_call`
records the submitted handle in the current note and `etalon_method_collect`
attaches the result and costs without another execution. Direct submission stays
available; save its handle with `etalon_research_progress` before a long wait.
Add your scientific interpretation when useful; the next chat should need the
current entry, not old transcripts.
Tool success does not prove scientific validity. Reading history does not create
a new independent test. Availability,
reading and actual method use are distinct; missing telemetry remains unknown.

Respect the project's current pause and resource instructions. Setup, retrieval
and method changes do not resume science or authorize paid experiments.

Optional references, each readable on its own:

- [Research and evidence](host-guides/research.md)
- [Method modification and attribution](host-guides/method-improvement.md)
- [Engineering and installation](host-guides/engineering.md)
- [Short project entry for a new chat](host-guides/project-entry.md)
- [Research graph, selective memory and lifecycle hooks](host-guides/research-graph.md)
- [Host setup and compatibility details](HOST_SETUP.md)

If the project supplies `etalon-workspace.json`, it locates published evidence
and status references. Use it when helpful, or read a known reference directly.
It is not a router, task plan or authorization registry. Load only the material
needed for the current question; a new chat need not inherit the whole history.

Optional skill groups are discoverable at `etalon://skill-libraries`: memory,
execution and RSI. They are an index, not a required sequence. A research entry
can be read with `etalon_research_context(view="summary")`; expand a node or exact
evidence only as needed. Check unfinished publications separately from pending
scientific jobs. Hooks prepare source data, not new instructions or authority.
