# Reuse the six MolQuarry methods

The maintained content source is `asset/molquarry/skills/`; do not copy or rewrite
its scientific workflows merely to call them through ETALON. All six are methods
that may be read independently, partially used, combined or skipped.

| Existing skill | Relevant question |
| --- | --- |
| `molquarry-analogue-search` | Structure/analogue and identity-based follow-up searches |
| `molquarry-assay-literature` | Assay/publication context, endpoints and evidence interpretation |
| `molquarry-compound-sourcing` | Vendor/catalog evidence and bounded sourcing, not proven stock or synthesis |
| `molquarry-selectivity-evidence` | Target/selectivity context and counter-evidence |
| `molquarry-structure-templates` | Experimental structure, construct and ligand/template provenance |
| `molquarry-target-modulators` | Target-to-compound collection and reviewed dossier delivery |

ETALON preserves the legacy resource index `etalon://skills/molquarry` and item
URIs `etalon://skills/molquarry/<name-without-molquarry-prefix>` (for example
`etalon://skills/molquarry/assay-literature`); full bundles include the original
references and agent metadata. Those URIs remain valid alongside main-library
discovery/export. Check the returned bundle version when recording use; the
entrypoint alone does not identify changed references.

Original MolQuarry text names its own CLI/SDK/MCP tools. In an ETALON-only MCP
session, map `list_sources/describe_source` to `etalon_data_sources`, acquisition
to `etalon_data_plan/run`, and snapshot verification to `etalon_data_status`.
For `collect_target_evidence`, use a `kind="collect"` request; for bundle building,
use `kind="bundle"` with a collected snapshot and curation path. A source-specific
operation that is not exposed through ETALON can use the installed MolQuarry SDK
under the same task's permissions; do not pretend its tool exists in ETALON.

Source coverage and a successful export are not scientific validation. Keep raw
evidence, retrieval failures, curation assertions, review limits, identity changes
and overlapping experiments. Download/collection/purchasing/model submission in
an optional method is not automatic authorization to perform those actions.
For ETALON candidate or assay admission, use the separate actual SDK/tool contracts
in [data operations](data-operations.md); collected rows are not automatically
training labels. No live collection is needed to discover/read/export these skills.
