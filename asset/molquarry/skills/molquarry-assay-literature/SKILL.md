---
name: molquarry-assay-literature
description: Trace compound activity claims to primary assays and accessible literature with MolQuarry ChEMBL and Europe PMC, preserving passage/table locators and coverage gaps. Use to review assay comparability, resolve potency conflicts, inspect counter-screen evidence or follow up a reported compound label.
---

# Primary assay and literature review

Begin with a specific claim, molecule/assay/document identifier or target plus aliases. Define species, endpoint and literature cutoff when relevant. A database label or review summary is a search lead, not a substitute for the underlying experiment.

## Resolve database records to papers

Inspect `describe_source("chembl")` and `describe_source("europepmc")`. Retrieve ChEMBL `assay` and `document` records using `chembl_id`; retrieve associated `activities` with `assay_chembl_id` or `document_chembl_id` so weak/inactive or conflicting measurements remain visible. Preserve the original activity IDs and censoring.

Fetch a known PMID with `europepmc.article` using `pmid` as a string. For unresolved claims, search target aliases, exact compound labels and experimental vocabulary. Example `query_database` arguments:

```json
{"source":"europepmc","operation":"search","parameters":{"query":"TITLE_ABS:EGFR AND TITLE_ABS:selectivity","limit":20}}
```

If a cutoff is requested, append an explicit `FIRST_PDATE:[1900-01-01 TO YYYY-MM-DD]` clause and record that this constrains first publication date, not database retrieval date. Follow cursor `next_parameters` unchanged. Retrieve accessible full text using the actual returned PMCID:

```json
{"source":"europepmc","operation":"fulltext","parameters":{"pmcid":"PMC8818087","contains":"C26","limit":20}}
```

`contains` is a local substring filter after the full XML fetch, not a remote semantic search. It can miss compound synonyms and neighboring experimental context; read surrounding unfiltered blocks when needed. Retain `block_id`, `locator`, `section`, table cells and links with PMID/DOI. Supplementary files are returned as links, not downloaded automatically. A PMCID does not guarantee full-text access. Report unavailable SI or tables instead of filling gaps from memory.

## Review the claim

For each measurement record chemical identity or unresolved local label, target/species/construct, binding versus functional/cellular endpoint, protocol, substrate/cofactor concentration, exposure time, readout, units, relation, replicates and controls where reported. Preserve contradictory values and distinguish repeated citations from independent experiments. Inspect correction/retraction metadata when supplied; a missing metadata flag does not prove that the paper has no subsequent correction.

Separate a reported observation, the authors' mechanistic interpretation and the agent's inference. A rescue experiment, direct-binding result and phenotype support different conclusions. Docking or AF3 predictions remain computational evidence. Do not infer inhibition versus agonism merely from a potency value.

## Deliver

When the host needs knowledge for a next choice, synthesize what the evidence
implies rather than returning every row. Existing `Curation` accepts optional
`knowledge` items: `statement`, `conditions`, `implications`, `limitations`,
`references` (the existing EvidenceReference format), and `basis` equal to
`reported_observation`, `authors_interpretation` or `agent_inference`.
Keep conditions and important contrary results in the synthesis. The agent writes
the interpretation; the SDK checks source links, not its scientific correctness.

`build_target_bundle(..., view="knowledge")` saves the full reviewed material
and returns a compact knowledge view plus its detail path, without preparing
structures or making additional network queries. SDK users can call
`integrate_review` and `knowledge_context`. Read the details when the current
choice needs them. Existing adequate knowledge can be used directly; this
synthesis is optional and does not process all internal experiment memory.

When the task needs assay comparison or source reconciliation, deliver an evidence table with exact claim, molecule identity status, assay context, measurement relation/value/units, source IDs, primary passage/table locator, evidence type and unresolved questions. Keep query strings, cutoff, page budgets, full-text/SI availability and raw response provenance in the saved material. A conflict may require a follow-up experiment; it need not be forced into a single consensus potency.

Use this result to label reference actives/inactives and choose assay-compatible evaluation sets before screening. An unmeasured analogue is not an experimental inactive, and test-set compounds already used to tune the protocol do not provide independent validation.

The [Europe PMC REST service](https://europepmc.org/RestfulWebService) documents search, publication records and open-access full-text retrieval. This skill does not provide publisher-wide SI retrieval or an exhaustive systematic review.
