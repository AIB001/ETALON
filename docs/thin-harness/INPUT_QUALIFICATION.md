# Optional scientific input qualification

`etalon.research.input_qualification` reads frozen molecular inputs into a
source-bound `etalon-input-qualification/1` report. It uses the existing
`etalon-scientific-input/1` envelope, ordinary `Artifact`, evidence workspace and
`ResearchJournal`; it creates no journal, endpoint, execution service or gate.
Existing `compile_input` callers continue unchanged. Consumers opt in explicitly.

```python
from etalon.research.input_qualification import (
    build_input_qualification, write_input_qualification, read_input_qualification,
)

report = build_input_qualification(
    bundle, stages=stages, endpoint=endpoint, workspace=workspace,
    purpose="research",  # research | conditioning | evaluation
)
artifact = write_input_qualification(
    output_path, bundle, stages=stages, endpoint=endpoint, workspace=workspace,
)
existing_journal.record(artifact)  # optional; caller owns the existing journal
checked = read_input_qualification(artifact, workspace=workspace)
```

Every bundle source retains `id`, `path`, SHA256, byte count, scientific `scope`,
`locator` and `role`. It adds an exact `evidence_ref` pointing to a released
Artifact. For a manifest or diagnostic containing the file pin, add
`identity_pointer`, a list of JSON keys and array indices selecting the exact
`{sha256, bytes}` object. For example, `["stages", 2]` selects a pinned preparation
input in a historical diagnostic. The anchor's bytes and record digest are checked
before comparing that pin with the local source file. Absolute local source paths
may relocate identical bytes; historical locators and refs remain intact.

Stages are ordered producer/consumer boundaries. Each has `name`, `source_id`
(the bundle source id), explicit `origin` (`source`, `generated`, `native_output`)
and a zero-based `record_index` (default 0). This origin is a caller declaration,
retained in the report; it is not authentication of experimental provenance.
The present adapter handles UTF-8 V2000 SDF, up to 8 MiB per file and 64 selected
stages. Unsupported formats fail explicitly. Records are selected explicitly;
reading record 0 does not claim analysis of the remaining records.

`endpoint` names the scientific question and its reference/prediction stage:

```json
{"name": "proposed_identity_comparison", "policy": "strict_full_identity/1",
 "reference": "reference", "prediction": "output"}
```

The policy describes this qualification's graph/state and stereo comparison. It
does not replace an existing verifier's canonical-string rules. A positive
`identity_comparable` only states identity comparability under this adapter and
the declared provenance. Every result keeps `endpoint_evaluated=false` and
`endpoint_value=null`; no RMSD, ranking success or affinity is computed. A
reference with missing/unknown applicable stereo remains
`unresolved_reference_comparability`, including when generated coordinates yield
a resolved prediction. A generated stage cannot supply the missing source
reference configuration. No H or stereo is removed from original bytes.

The report keeps separate:

- Graph/chemical-state identity after stereo/map neutralization, preserving
  isotopes, formal charge, radicals and total H. This signature is only a graph
  correspondence check, not a complete stereochemical identity claim. Charged
  hydrogen is currently rejected explicitly, since default H removal can erase
  its formal charge; unsupported chemistry never becomes an identity match.
- Potential stereo applicability versus nonstereogenic features; information
  states `unspecified`, `explicit_unknown`, `resolved`; raw field assertions,
  source depiction/coordinate perception and generated coordinate perception.
- Existing-coordinate perception without added H, raw molfile fields, atom
  indices, parser/version/path and default `RemoveHs` retained-H reasons.
- Exact mapped producer/consumer transitions. Resolved R/S or E/Z changes are
  separate from unknown-to-resolved representation events. Neighbor-order chiral
  tags alone never establish inversion. All found symmetry mappings are checked;
  conflicting or truncated mapping sets remain ambiguous.
- Exact evidence refs, their transitive roles and byte-verification results.

Evaluation-only/context sources, observations and interpretations cannot be
returned as a conditioning report, even when no execution binding uses them.
Evaluation-only/context sources cannot be relabelled conditioning. This applies
transitively to Artifact refs/parents and to an explicitly selected JSON
container's roles, visibility and refs, including linked JSON Artifact payloads. Sealed or
unresolved lineage fails closed. A foreign payload-only ref without a loaded
declared dependency fails explicitly; the adapter does not widen source scope
or scan unrelated sources to make it pass. The output
Artifact keeps every input ref and receives the restrictive existing
`independent_validation` role when any embedded content is non-conditioning; this role
protects visibility, **not a claim of newly independent validation**. Fixture
lineage remains `source_kind=fixture`, including through wrappers declared real.
`read_input_qualification` checks artifact
bindings/role and recomputes claims against pinned sources; edited status claims
or a changed parser require a new report rather than silent historical rewriting.

The implementation borrows the raw-field, stereo-neutral-copy and mapping
conventions of `examples/diagnose_pose_stereochemistry.py`, while moving the
reusable qualification interface into the package. Its graph comparison handles
isotope-labelled H explicitly; the old diagnostic's narrower isotope mapping
boundary and all old interface behavior stay unchanged.

`examples/qualify_archived_imines.py` demonstrates read-only qualification of
the actual archived `1OWE_675` and `1UOU_CMU` top-1 SDF records. It binds their pins
through the existing diagnostic Artifacts in the original frozen journal, copies
only the two anchor files into a local relocation directory, and writes derived
ordinary Artifacts. It creates no journal and calls no native tool or historical
verifier. The old unknown endpoints are retained. Local RDKit reanalysis is
reported with its actual version; it is not replication of the historical Linux
runtime or independent scientific validation.

`etalon-input-qualification/1` is an Artifact **payload** schema. It is not a new
typed research record and need not be added to evidence content projection or
global schema dispatch. Its ordinary Artifact bindings carry the source refs;
the dedicated reader validates the payload when an interested consumer asks.
