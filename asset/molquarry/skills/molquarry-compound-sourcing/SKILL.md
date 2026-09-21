---
name: molquarry-compound-sourcing
description: Screen an SDF or molecular shortlist with MolQuarry for exact chemical-library matches, supplier catalog evidence and synthesis assessment, returning a traceable Excel workbook. Use for purchasability, catalog-existence or make-versus-buy questions. Distinguish live offers, catalog listings and unvalidated synthesis heuristics.
---

# Compound sourcing with MolQuarry

Load the installed MolQuarry SDK/CLI or its MCP sourcing tool. Keep reports and documentation in English. Use the user's requested output directory; otherwise create a new run folder under the current working directory, never under the default home directory. For the STK17B case the requested location is `example/stk17b_shortlist/` in the project.

## Inspect identity before searching

Read every SDF record, retaining its original index, name, properties and parse errors. Count both records and unique full InChIKeys. Do not silently drop duplicate rows or invalid structures.

Use the actual SDF structure with its stereochemistry, isotopes, charge and fragments intact. Compare any supplied parent SMILES independently. Docking-generated 3D coordinates can assign stereocenters absent from the parent SMILES: record this disagreement and search both identities when appropriate. A supplier hit for the unspecified parent is not an exact hit for the SDF stereoisomer. Never use a docking score as evidence that a compound binds the target.

## Execute a bounded, resumable screen

Inspect `describe_source` for PubChem, ChEMBL, UniChem and Mcule. The public workflow is:

1. Batch exact full-InChIKey queries to PubChem and ChEMBL; associate returned records by identity, never by list position. Missing keys in a successful partial batch are distinct from a failed batch.
2. Check UniChem mappings against the returned full InChI. Keep the source inventory's release dates; cross-references can lag the original database.
3. For exact PubChem CIDs, read PUG-View source categories and retain actual **Chemical Vendors** depositor records with supplier, catalog ID, SID and URL. Research databases and legacy depositors are not current vendor listings.
4. Perform only the budgeted Mcule lookups. Respect anonymous limits (currently 10/minute and 100/day, shared with other work); do not cycle clients to bypass a limit. All remaining keys must say `not_queried_budget`.
5. If authorized local catalogs are supplied, compare exact structures and preserve snapshot date/provenance. A stale `in_stock` cell does not independently confirm current stock.
6. Compute RDKit SA scores and BRICS cut counts as **heuristics**. A low score, an existing PubChem ID, a patent mention or a fragment decomposition does not establish a feasible synthesis route. Confirmed synthesis requires a documented route or a supplier's exact make-on-demand result/quote; retain route identity, conditions and uncertainties when those are available.

```bash
molquarry source-sdf path/to/shortlist.sdf \
  --output-dir example/stk17b_shortlist --max-mcule-queries 20
# Resume exactly the same input/configuration, preserving query evidence.
molquarry source-sdf path/to/shortlist.sdf \
  --output-dir example/stk17b_shortlist --max-mcule-queries 20 --resume
```

The SDK entrypoint is `molquarry.workflows.sourcing.screen_sdf`; configuration is `SourcingConfig`. Read [the result contract](references/results.md) for statuses and validation. Public queries do not require credentials. Credentialed stock/pricing or REAL Space searches require a separately authorized account and successful real response; their unavailability must remain explicit.

## Validate and deliver

The STK17B shortlist regression contained 1,352 records but 1,350 unique SDF identities, and 86 records differed from their parent-SMILES identity. Keep duplicate input rows and query distinct parent variants separately. Use an aspirin positive control before interpreting thousands of no-match responses. PubChem batch responses can omit missing identities and reorder returned hits: match by returned full InChIKey, never by array position. Restart interrupted runs with the same input/config and `--resume`; saved query evidence prevents repeating completed requests.

Open the generated workbook rather than treating successful script exit as sufficient. Verify the row count against the original SDF, preserve duplicates and failures, and cross-check a known positive control and no-match behavior against actual API records. Review identity mismatches, source errors, limits, current stock and synthesis evidence separately.

Return links to `tables/sourcing.xlsx`, `run.json` and the run directory. Explain counts at the **input-record** and **unique-identity** levels. Use three separate answers:

- **Exists in queried libraries:** exact source hits, with identifiers and dates. No hit is not proof of novelty.
- **Can purchase:** distinguish confirmed offers from catalog-only leads and unsearched/failed sources. Do not convert missing prices into zero or unknown stock into “unavailable.”
- **Can synthesize:** distinguish supplier make-on-demand or validated routes from heuristic prioritization and unknown feasibility.

Retain raw query responses/errors, source provenance, source-specific coverage, original input hash, tool versions and file checksums. Resume only when the input, configuration and supplied local catalog hashes agree. Do not publish private input molecules or generated results merely because the software repository is being pushed.

If a source repeats a continuation or exceeds the page budget, report `partial_pagination`, not absence. Resume also verifies the archived SDF copy. When deliberately retrying source errors after an outage, use `--retry-errors`; add the global `--no-cache` option when stale transport responses are suspected. Do not repeatedly retry authentication or quota failures.

After a real case, update this skill only when the observed result reveals a concrete workflow or interpretation gap. Record remaining uncertainties in the result, not as invented negative findings.
