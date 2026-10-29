# Learn from normal generation and choose the next operation

Keep the model, pocket, chemical-state preparation and conditions visible when
comparing runs. `etalon_generation_feedback(manifests, criteria)` reads native
PRISM manifests without generating molecules. `criteria` declares the decision's
property windows, for example `{"heavy_atoms":{"min":20,"max":40}}`; choose
limits for the actual task, not this example. Unknown properties remain unknown;
failure counts overlap and should not be summed as unique rejected molecules.

This returns requested/generated/candidate counts, native QC statuses, passing,
failing and unknown windows, and failure reasons by run. Nominal intervals are
descriptive; correlated generated structures are not independent trials. Window
yield does not establish affinity, synthesizability or superiority to a library.

Interpret the limiting difference: delivery failure, irrelevant size/chemistry,
bad geometry, weak useful diversity, repeated structures, or a selection rule
that may miss a valuable alternative. Change a model parameter, adapter, method,
action representation or workflow when useful. A model may not expose a desired
knob; use a different method or explicitly extend it rather than inventing one.
Keep useful exploratory exceptions separate from a claim that the original
criteria were met. Another generation batch is optional, not an automatic repair.

The same method can run continuously without model turns:
`[etalon_python, "-m", "etalon.generate.feedback"]` accepts the existing
`method.run` request/result contract, inputs `{"manifests":[...],"criteria":{...}}`,
and output `{"feedback":"feedback.json"}`. Register its actual inputs/source
through the existing method contract; inspect compact output and open detail only
when it changes a decision.

`etalon_generation_handoff(manifests, destination, operation, candidates=None,
locations=None)` prepares an SDF and candidate map. `operation` is an explicit
host choice: `inspect_pose`, `redock` or `properties`. Candidate keys are
`<manifest sha256>/<model>/<native candidate_id>`; names alone collide across
chunks. The default selects all delivered candidates, not all generated attempts.
Use `locations={"/old/run/root":"/current/run/root"}` for an explicit relocation.
Each occurrence retains model/run membership even when two models generated the
same chemical identity. `include_scaffolds=True` adds observed Murcko groups;
annotation failures stay unknown without dropping the molecule. It does not cap
the groups. Preserve this many-to-one map when later parent registration deduplicates.
The returned `screen_input` contains the native SDF reader/library settings.
Merge that fragment into the chosen cascade and pass `outputs.sdf.path` to the
ordinary plan/submit call. SDF occurrence/partition tags then enter the existing
raw-source and parent-source contracts; do not rebuild a CSV that keeps only the
first model's origin. A shared chemical parent may belong to several source groups.
For within-family optimization, choose a family-aware sampling policy explicitly;
commercial catalogue diversity selection need not apply to generated analog series.

The SDF preserves original coordinates. Inspect/score consumers must preserve
them; redocking explicitly allows replacement. The map points to original quality
and pocket metadata instead of copying it. Unknown stereochemistry, protonation,
missing outputs and native warnings do not become successes. Repeating a completed
unchanged handoff reuses checked output without parsing molecules again.

Pass the prepared SDF to the selected consumer. For MolCascade, use a compatible
host-selected configuration and the ordinary screen plan/submit interface. Do not
insert ETKDG when the question concerns the native generated pose. MD remains a
separate scientific choice within the task's authorization.

Save an ordinary progress note linking the executable method, tuned configuration,
usage skill and conditional finding. Update a skill when the way to choose or tune
has improved; keep per-run counts and target-specific lessons in memory.
