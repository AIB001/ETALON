---
name: molquarry-selectivity-evidence
description: Retrieve measured on-target, off-target and counter-screen evidence for specific compounds with MolQuarry and ChEMBL. Use to assess a shortlist's known selectivity or identify missing panels; distinguish assay-comparable selectivity ratios from heterogeneous measurements and untested targets.
---

# Measured selectivity and counter-screen evidence

Accept exact compound identities, the intended target/species and any requested off-target panel. If only a compound name is supplied, resolve it and report unresolved stereochemistry or parent/salt differences. This workflow retrieves existing measurements; it does not predict unmeasured targets or prove selectivity from missing records.

## Collect the compound's measurements

Inspect `describe_source("chembl")`. Resolve full InChIKeys with `molecules_by_inchikey`, then inspect `molecule` details and hierarchy. Query by **molecule**, without an initial target or potency filter that would hide weak/inactive results:

```json
{"source":"chembl","operation":"activities","parameters":{"molecule_chembl_id":"CHEMBL25","limit":100}}
```

Page with the returned `next_parameters` unchanged, recording the budget and any unfinished continuation. For each target ID use `target`; for each assay/document ID use `batch_details` with `entity="assay"` or `"document"` (up to 50 IDs per request, still paginated). Inspect assay confidence, target components, species, mutant/construct and experimental description. A compound tested in a cellular assay is not automatically measured against its annotated protein target. Parent-molecule measurements remain separately labeled when identity differs.

For a target-first panel gap, map intended UniProt accessions using `targets_by_uniprot`; do not equate every returned complex or family with the single protein. The generic `bindingdb.by_uniprot` query can supplement target-specific measurements after checking its current schema. Reconcile hits by chemical identity, not compound-name substrings. Use `europepmc.article` or `search` for primary follow-up and the `molquarry-assay-literature` skill when experimental context determines interpretation.

## Compare only supported pairs

Keep `standard_type`, `standard_relation`, `standard_value`, units, activity comments, validity flags and duplicate flags. Separate binding, biochemical function, cell response and counterscreen observations. Do not average Ki, Kd, IC50 and EC50 together or use a pChEMBL value alone to declare assays interchangeable.

For the same positive concentration endpoint under comparable experimental conditions, define fold preference as `off_target_value / on_target_value` after explicit unit conversion. Larger values favor the intended target. Record the two measurement IDs and why conditions are comparable. An off-target IC50 `>10000 nM` and on-target IC50 `=10 nM` imply a lower bound `>1000`, not an exact ratio. Incomparable contexts, mixed inequality directions, missing denominators or conflicting measurements remain unresolved rather than silently producing a number. Same-paper panel measurements are useful candidates for comparison, but still require assay review.

## Deliver and hand off

Return an evidence matrix with compound identity, target/species, assay/document IDs, endpoint/relation/units, context, reported inactivity thresholds and a coverage status: `measured`, `not_found_in_queried_sources`, `not_queried_budget` or `query_failed`. List partial pagination separately. Separate observed selectivity from missing evidence and proposed experiments. Keep raw responses and query provenance in the project run directory.

In ETALON, attach this matrix to candidate review before prioritization. Docking against a counter-target can prioritize a hypothesis, but cannot convert an unmeasured matrix cell into experimental selectivity.

## Basis

[ChEMBL's assay and activity documentation](https://chembl.gitbook.io/chembl-interface-documentation/frequently-asked-questions/chembl-data-questions) explains target-mapping confidence, activity validity and duplicate annotations; its [web-service documentation](https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services) describes retrieval and filters.
