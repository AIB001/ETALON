# Sourcing result contract

`screen_sdf(quarry, input_sdf, output_dir=None, config=SourcingConfig(), resume=False)` writes one organized directory. The source input remains unchanged; `input/shortlist.sdf` is its exact copy. `evidence/queries/` is a request-keyed journal, including failed and negative lookups. `manifest.json` hashes the actual output files.

The workbook contains:

| Sheet | Meaning |
| --- | --- |
| Compounds | One row for each original SDF record, including invalid or duplicate records |
| IdentityVariants | Unique exact identities; parent variants remain separate from SDF identities |
| LibraryMatches | Source IDs, full identity matches, links and query IDs |
| VendorListings | Chemical vendor depositions/cross-references; current stock and price unverified |
| SynthesisAssessment | SA score (1 easier–10 harder), BRICS cuts and explicit route/MOD status |
| QueryCoverage | Request parameters, statuses, timestamps and query IDs |
| LocalCatalogs | Supplied snapshot hashes and parsed/unparsed counts |
| UniChemSources | Source inventory and reported update dates |
| Limitations | Query budgets, unavailable account-dependent services and evidence boundaries |

`exact_match` requires a matching full InChIKey or full InChI converted to that key. A connectivity-only, salt, protonation or stereochemical alternative must not be promoted to exact. A parent-SMILES match remains a separate column even when it is the intended design structure.

`no_exact_match_in_source` means a completed, correctly targeted lookup found no matching identity. HTTP 429/5xx, schema drift, missing authentication, budget limits and unrequested sources retain distinct statuses. A partial successful response does not erase failed later pages. `catalog_listed_stock_unverified` is a sourcing lead, not a purchasability guarantee.

No public endpoint in this workflow validates synthesis. The default `can_be_synthesized=not_established` and `route_status=no_validated_route_retrieved` are intentional scientific conclusions about available evidence. SA score and BRICS cuts help prioritize chemist review; they must not be labeled “synthetically feasible.”

Acceptance should reopen Excel and JSON, compare every original input record and identity, test source-specific positives and negatives, check formula-safe strings and confirm checksums. A fully successful anonymous screen can still return unknown stock, no vendor hits and no established synthesis route.
