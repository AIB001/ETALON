---
name: etalon-data-design
description: Acquire bounded source evidence, prepare candidate libraries, review assay admission, or inspect and import PRISM design bundles with ETALON's data SDK.
---

# Use source evidence without changing its scientific role

Use the part that answers the current question. An existing snapshot or design
bundle can be inspected directly; acquisition, candidate import, assay admission,
geometry attachment and scientific execution are separate operations. Known SDK
or MCP calls need neither this skill nor a method record.

- For source discovery, bounded collection, local catalogs, library preparation,
  assay reviews or geometry attachment, read [data operations](references/data-operations.md).
  It maps all nine `etalon_data_*` tools to their SDK calls and side effects.
- For a producer-exported `prism-design-candidate-bundle/1`, read
  [design bundles](references/design-bundles.md): strict read, explicit pool or
  index-only import, verification and replay.
- For target/literature, analogue, selectivity, sourcing or structure questions,
  [MolQuarry sources](references/molquarry-sources.md) identifies the six existing
  methods and the ETALON adapter. Load only the relevant method.

Keep source record, chemical state, pose, preparation, execution and observation
identities distinct. Candidate registration adds no assay; Astra reasoning is a
hypothesis with provenance, not a measured endpoint. A scalar score belongs only
to its declared computational endpoint. The existing data interfaces do not
establish a complete Astra annotation-to-learning loop.

Retain incomplete coverage, censored measurements, conflicts, failed slots,
unknowns and original files. Stop or narrow the question when the needed identity,
role or coverage is absent. Do not convert missing values to zero or repeat a
possibly completed acquisition merely because its response was lost.

The source example `examples/data_design_methods.py --output NEW_DIRECTORY`
executes a tiny synthetic local catalog with `max_requests=0`, actual chemistry
preparation and candidate import, and reports explicit assay refusal. Optional
`--bundle PATH` separately checks and indexes an existing engineering bundle.
It does not score candidates. The example is delivered in the source checkout;
installed users can run the SDK snippets in the references directly.
