# Reviewed public data to a native screening result

`examples/quarry_cascade_research.py` is an optional example using the existing
component interfaces. It introduces no router, executor, scientific database,
automatic label admission, or default research policy.

The interfaces form this chain:

1. `data.service.plan_data/run_data` queries MolQuarry and seals normalized
   source records with response hashes, provenance and a bounded HTTP receipt.
   This API does not preserve the raw HTTP response envelope.
2. The host reviews source identity, exact chemical state, target context and
   the role of each candidate. A structural control is not an affinity label.
3. `data.library.prepare_library` uses the pinned MolCascade standardizer and
   retains every source-to-parent mapping. Its default tautomer policy preserves
   the supplied graph. This conversion belongs to ETALON integration.
4. `boundary.screen.Screen.plan` compiles a native cascade. `run` executes it;
   `read`, `metrics`, `recall`, `export_shortlist` and `handoff` inspect the native
   artifacts and contracts. MolCascade owns stage execution, cache and resume.
5. `research.components.inspect_component` produces verified read receipts for
   explicit later publication. Reading those receipts does not execute science
   or charge the original calculation again.

MolQuarry supplies source identity and measurement context. MolCascade supplies
computational screening evidence for declared inputs and policy. ETALON and its
host retain scientific admission, research branches, comparisons and RSI
evaluation. Similarity and basic SDF operations may overlap without requiring
another abstraction layer.

Use the existing catalogs for discovery. `research.components.capabilities()`
declares the host interfaces and six MolQuarry instruction skills. MolCascade's
empty host `skills` list does not mean it has no capabilities:
`molcascade.cascade.catalog.catalogue_json` exposes its native criteria and
registered adapters. Adapter registration, environment preflight, real execution
and scientific benefit are four separate levels of acceptance.

The example has explicit `acquire`, `review`, `prepare`, `run`, `resume` and
`inspect` commands. For the SND1 example it uses two explicitly named, already
seen crystal ligands, public PubChem properties, RCSB entry/polymer context and
an existing pinned 7KNW chain-A receptor. A host-authored review compares full
InChIKeys and exact canonical graphs; it does not infer a pH-dependent state.
The review function is an example-specific review policy, not a replacement for
scientific judgement on another dataset.

```sh
python examples/quarry_cascade_research.py acquire --root NEW_ROOT --panel PARENT_PANEL
python examples/quarry_cascade_research.py review --root NEW_ROOT
python examples/quarry_cascade_research.py prepare --root NEW_ROOT --gnina ABSOLUTE_GNINA
python examples/quarry_cascade_research.py run --root NEW_ROOT
python examples/quarry_cascade_research.py inspect --root NEW_ROOT
python examples/quarry_cascade_research.py resume --root NEW_ROOT
```

Set the scientific interpreter and pinned component environment explicitly.
`prepare` keeps default native pose repair/checking, uses CPU empirical GNINA
(`cnn_scoring=none`, `rank_by=affinity`, two threads, exhaustiveness 1), and adds
the existing handoff criterion. Its permissive score gate is for engineering
acceptance and is not a calibrated affinity decision. The host explicitly
permits the already-authorized GNINA copyleft backend for this example.

Always set `LibraryConfig.id_column="id"` for the prepared CSV. The parent hash
identifies a chemical state, whereas native recall and exported source identity
also need the original identifier mapping. The 2026-10-25 acceptance retained a
first successful 12-stage run whose missing mapping caused real `recall` failure,
then repaired the example in a new workspace and recorded the extra cost.
Neither result was overwritten or treated as independent scientific validation.

The repaired real run completed 12 stages, retained 2/2 identifiers, produced
two receptor-bound handoff records and exported a two-row identified shortlist
with docking sidecar. A repeated native resume produced the same result with
zero child CPU time and no scientific artifact/cache byte or mtime changes;
run/event status files appended resume information. This verifies exact reuse
for that run, not independent reproduction. Public-source and native-execution
receipts are separate from the seven synthetic identity unit checks.

Handoff remains a contract, not a simulation acceptance. These two records carry
`DOCKED_POSE`, the exact receptor digest, explicit neutral-source state and
`POLAR_ONLY` hydrogens. They still need the receiving simulation's full preparation
and validation. The receptor has no explicit hydrogens; native preflight records
that GNINA chooses its receptor hydrogen perception. No MD was dispatched.
Native pose repair can change coordinates after scoring; its method identity
and QC metadata must accompany the engine score rather than presenting it as a
new score of the repaired geometry.

The evidence scope is two seen controls at one known pocket. Retention is not
biological active recall; docking scores are not experimental affinities. This
acceptance evaluates neither scientific ranking benefit nor transfer nor RSI
improvement. R4b remains paused. The run's `memory-materials.json` contains
existing-reader receipts and explicit limitations for host-selected publication;
it does not itself change task authority, adopt a method, or start a new branch.
