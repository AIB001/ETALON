# MolQuarry

MolQuarry gives agents a consistent way to discover CADD data sources, query scientific evidence, download official files, and search authorized local catalogs. Its target workflow produces organized **PDB structures and alignments, an Excel compound workbook, RDKit-prepared 3D SDF files, source evidence and AF3 job inputs**.

**Version 0.5.0 covers 51 sources in 12 categories:** 41 remote query/file-discovery adapters and 10 official access guides with local import/search. There are **110 query/discovery operations and 28 download operations**, plus `resources` on each source. These counts describe implemented operations, not complete coverage or public API availability at every website.

Six bundled skills turn these adapters into focused search workflows:

| Skill | Search and review task |
| --- | --- |
| [Target modulators](skills/molquarry-target-modulators/SKILL.md) | Target-to-ligand, inhibitor and agonist evidence dossiers |
| [Compound sourcing](skills/molquarry-compound-sourcing/SKILL.md) | Exact identities, catalog evidence and make-versus-buy review |
| [Analogue search](skills/molquarry-analogue-search/SKILL.md) | Bounded fingerprint similarity and supplied-core substructure expansion |
| [Selectivity evidence](skills/molquarry-selectivity-evidence/SKILL.md) | Measured off-target/counter-screen profiles and comparable selectivity ratios |
| [Structure templates](skills/molquarry-structure-templates/SKILL.md) | Experimental receptor/ligand templates and redocking reference selection |
| [Assay literature](skills/molquarry-assay-literature/SKILL.md) | Primary assay context, conflicting potency claims and traceable passages |

The new workflows use existing source queries plus PubChem `substructure`; they are agent-guided searches, not automatic conclusions about activity, selectivity or patent novelty. The MTDH/SND1 example (`examples/mtdh_snd1/README.md`, local workspace) contains an executed public-source search and deliverables. Original 50-source verification and the Gleevec FDA approval case remain in verification (`docs/verification.md`, local workspace); the current workflow review is in workflow review (`docs/workflow-review.md`, local workspace).

The Git repository contains runtime code, tests, scripts, skills and this README. Extended `docs/`, `examples/`, `example/`, build artifacts and generated results remain local and are intentionally excluded from Git. Live verification commands write their own evidence locally.

## Install

Python 3.11 or later is required:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[mcp,deliverables,dev]'
molquarry sources --implemented
molquarry describe europepmc
molquarry query chembl search_assays --params '{"query":"MTDH","limit":5}'
```

Windows PowerShell: `.venv\Scripts\Activate.ps1`. Core SDK/CLI installation is `pip install -e .` and only requires httpx and Pydantic. The `deliverables` extra adds RDKit, Gemmi, Biopython, NumPy and openpyxl for structures, alignment, chemistry and Excel. Source queries do not require this extra. The separate `verify` extra adds ORD/Parquet test parsers.

## Target inhibitor or agonist workflow

```bash
# Collection creates a new directory and never overwrites a previous run.
molquarry collect MTDH SND1 --mode inhibitor --taxon 9606 --until 2026-09-19 \
  --output-dir .molquarry/mtdh-snd1-search
# Agonist searches use activation/agonism literature vocabulary.
molquarry collect ADRB2 --mode agonist --taxon 9606 \
  --output-dir .molquarry/adrb2-agonist-search

# An agent reviews evidence and writes curation.json using the skill's schema.
molquarry review-inhibitors .molquarry/mtdh-snd1-search/dossier.json curation.json \
  --output .molquarry/mtdh-snd1-search/reviewed.json
molquarry bundle .molquarry/mtdh-snd1-search/dossier.json curation.json
```

`inhibitors` remains an alias for the collection command. For aliases, paper follow-ups and limits, use `--config search.json`; see the executed case configuration (`examples/mtdh_snd1/search.json`, local workspace). Collection does not classify every bioactivity row as an inhibitor or agonist. Explicit curation separates functional modulation, binding, cellular phenotypes, counter-screen results and computational predictions.

Unless an output path is supplied, `bundle` creates `MolQuarry_<targets>_<mode>_<timestamp>/` **under the current working directory**, not the user's home directory. The generated layout is:

```text
MolQuarry_<targets>_<mode>_<timestamp>/
├── README.md
├── run.json
├── manifest.json
├── tables/
│   ├── compounds.xlsx
│   └── structures.csv
├── structures/experimental/
│   ├── source_mmcif/            # Original structures and download manifests
│   ├── pdb/                     # PDB exports, retaining original models
│   ├── aligned/<accession>/     # Separate groups for non-overlapping domains
│   └── index.json               # Resolution, methods, coverage and alignment metrics
├── compounds/
│   ├── source_3d/               # Available downloaded 3D structures
│   ├── sdf/                     # RDKit-prepared poses or labeled generated conformers
│   ├── raw_queries/
│   └── index.json
├── modeling/af3/jobs/           # Official Server/local inputs and submission state
└── evidence/
    ├── collection/             # Source responses, pagination and checksums
    ├── curation.json
    └── reviewed.json           # Reviewed evidence and unresolved conflicts
```

The workbook contains Candidates, Measurements, Purchasing, ChemicalProperties, DatabaseHits, Structures, Alignments, Papers, Coverage, Conflicts, SDFIndex and AF3 sheets. Candidate rows include PMID, source/RDKit SMILES, InChIKey, formula, molecular weight, logP, TPSA, donors/acceptors, rotatable bonds, identity status and purchasing status when the chemical identity is available. Unknown structures are left unresolved, not guessed from local labels such as “C19.”

Structures are downloaded as original mmCIF and exported to PDB when legacy PDB can represent them. All discovered entry IDs and available resolutions remain in the index. NMR structures have no crystallographic resolution. Alignment validates SIFTS-linked entity sequences against UniProt, handles construct insertions/deletions, excludes ambiguous residue correspondences and performs a rigid C-alpha fit; disjoint domains get separate reference groups. Conversion failures, absent coordinate mappings, selected limits and high RMSD are reported.

Ligand processing assigns source chemistry, sanitizes with RDKit and adds hydrogens. Experimental heavy-atom coordinates stay fixed during minimization. Where no experimental pose is available, a public computed conformer or a reproducible ETKDG conformer may be used and is labeled accordingly. Neither is presented as an experimental binding pose.

Public purchasing checks use exact Mcule identity lookup. Catalog presence does not establish stock, price, packaging or lead time; these fields remain unverified until an authorized supplier service or quote supplies them. Credentials and live sourcing extensions are separate from the public case.

## All-ligand inventory and shortlist sourcing

Load the [target skill](skills/molquarry-target-modulators/SKILL.md) for ligand discovery and the [compound sourcing skill](skills/molquarry-compound-sourcing/SKILL.md) for an SDF make-versus-buy screen. These are repository skills; they do not install themselves into a home directory.

```bash
molquarry collect STK17B --mode ligand --taxon 9606 --max-pages 100 --max-details 5000 \
  --output-dir example/stk17b/collection
molquarry ligand-table example/stk17b/collection/dossier.json --output-dir example/stk17b/inventory
molquarry inventory-3d example/stk17b/inventory/inventory.json --output-dir example/stk17b/computed_3d
molquarry source-sdf path/to/shortlist.sdf --output-dir example/shortlist --max-mcule-queries 20
# Resume the same input/configuration without repeating saved queries.
molquarry source-sdf path/to/shortlist.sdf --output-dir example/shortlist --max-mcule-queries 20 --resume
```

`ligand-table` retains every collected ChEMBL/BindingDB measurement, source PMID/DOI, identity and assay context. It separates inactive, censored, quantitative-potency and other readouts. A database measurement is not automatically a validated ligand. `--sourcing path/to/results.json` joins public vendor evidence into a new inventory; `--highlights highlights.json` adds explicitly reviewed literature leads. `inventory-3d` generates labeled computed conformers with per-compound identity, force-field and failure checks; these are separate from experimental binding poses.

`source-sdf` produces `tables/sourcing.xlsx`, `results.json`, `run.json`, a copied `input/shortlist.sdf`, resumable `evidence/queries/`, and a SHA256 `manifest.json`. Its nine sheets preserve every input row, exact and parent identities, library matches, vendor listings, source coverage and synthesis heuristics. PubChem/ChEMBL use batched exact InChIKey queries; UniChem cross-references are checked against the returned full InChI. `--local-catalog catalog.csv` adds an authorized CSV/TSV/SDF snapshot. Mcule lookups have an explicit per-run cap; its anonymous quota is shared with other work. Credentials are required for additional live supplier services. Catalog listings do not confirm stock or price, and SA/BRICS heuristics do not establish a synthesis route.

The executed STK17B cases live locally in `example/stk17b_ligands/` and `example/stk17b_shortlist/`. Their data, molecules, caches and reports are intentionally absent from Git. They test real public sources, not only mocked endpoints. The target case also records inaccessible full texts, unresolved literature identities and unsuccessful 3D preparation.

## AlphaFold 3

The bundle prepares official **AlphaFold Server JSON** and **local AlphaFold 3 JSON**, with one job per target and a complex job for multiple targets. This is recorded as `prepared_not_submitted`.

The official Server documentation describes authenticated browser submission and JSON uploads. MolQuarry does not implement an undocumented username/password REST endpoint. An account password alone does not launch a job, and passwords are not saved in project files. An agent with an authorized browser can upload the prepared Server JSON, download the real result ZIP into the workspace, then import it:

```bash
molquarry af3-import path/to/actual_af3_results.zip \
  --output-dir path/to/run/modeling/af3/results
```

For an installed local AF3 environment, model parameters and databases:

```bash
molquarry af3-run path/to/target_local.json --output-dir path/to/run/modeling/af3/local-run \
  --python-executable /path/to/af3/python --run-script /path/to/alphafold3/run_alphafold.py \
  --model-dir /path/to/model_parameters --database-dir /path/to/databases
```

The importer retains actual CIF, PAE/pLDDT and confidence JSON, creates PDB exports, and reports pTM/ipTM/ranking scores when supplied. No AF3 inference was performed for the public MTDH/SND1 case; no server account or local AF3 installation was supplied. See AF3 integration (`docs/af3.md`, local workspace) for supported boundaries and official references.

## Source implementation checklist

All sources expose `resources` and the shared local CSV/TSV/SDF import/search tools. The table omits repetitive `resources`. Inspect exact parameters with `molquarry describe SOURCE`. Verification statuses are sample-specific; authenticated adapters have not been live-tested in the anonymous scope.

| # | Source / ID | Implementation | Query or discovery operations | Downloads | Public verification |
| --- | --- | --- | --- | --- | --- |
| 1 | [Aladdin](https://www.aladdinsci.com/) / `aladdin` | [x] Local import; [ ] Remote API | Official access guide and local search | — | blocked |
| 2 | [AlphaFold DB](https://alphafold.ebi.ac.uk/api-docs) / `alphafold` | [x] Public API | `prediction` | `structure` | semantic_passed |
| 3 | [BindingDB](https://www.bindingdb.org/rwd/bind/BindingDBRESTfulAPI.jsp) / `bindingdb` | [x] Public API | `by_uniprot`, `files` | `artifact` | semantic_passed |
| 4 | [BioLiP2 / BioLiP3](https://zhanggroup.org/BioLiP/download.html) / `biolip` | [x] File discovery | `files` | `artifact` | semantic_passed |
| 5 | [BLD Pharmatech](https://www.bldpharm.com/) / `bld` | [x] Local import; [ ] Remote API | Official access guide and local search | — | website_only |
| 6 | [ChEBI](https://www.ebi.ac.uk/chebi/backend/api/docs/) / `chebi` | [x] Public API | `compound`, `search`, `parents`, `files` | `molfile`, `artifact` | semantic_passed |
| 7 | [ChEMBL](https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services) / `chembl` | [x] Public API | `molecule`, `target`, `assay`, `document`, `search_molecules`, `targets_by_uniprot`, `activities`, `search_assays`, `molecules_by_inchikey`, `batch_details`, `files` | `sdf`, `artifact` | semantic_passed |
| 8 | [ChemDiv](https://www.chemdiv.com/catalog/screening-libraries/) / `chemdiv` | [x] Local import; [ ] Remote API | Official access guide and local search | — | website_only |
| 9 | [Chemspace](https://api.chem-space.com/docs/) / `chemspace` | [x] Auth adapter | `search` | — | deferred_credentials |
| 10 | [ClinicalTrials.gov](https://clinicaltrials.gov/data-api/api) / `clinicaltrials` | [x] Public API | `study`, `search` | — | semantic_passed |
| 11 | [ClinPGx](https://api.clinpgx.org/swagger/) / `clinpgx` | [x] Public API | `chemical`, `gene`, `entry` | — | semantic_passed |
| 12 | [COCONUT](https://coconut.naturalproducts.net/api-documentation) / `coconut` | [x] Public API | `search`, `files` | `artifact` | semantic_passed |
| 13 | [CompTox / ToxCast / ToxValDB](https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis) / `comptox` | [x] Auth adapter | `chemical`, `toxval` | — | deferred_credentials |
| 14 | [DepMap / PRISM](https://depmap.org/portal/data_page/) / `depmap` | [x] File discovery | `release` | `file` | semantic_passed |
| 15 | [DrugBank](https://go.drugbank.com/releases/latest) / `drugbank` | [x] Local import; [ ] Remote API | Official access guide and local search | — | blocked |
| 16 | [DrugCentral](https://drugcentral.org/download) / `drugcentral` | [x] File discovery | `files` | `artifact` | semantic_passed |
| 17 | [Drugs@FDA / openFDA](https://open.fda.gov/apis/drug/drugsfda/) / `drugsfda` | [x] Public API | `search` | `partition` | semantic_passed |
| 18 | [eMolecules](https://www.emolecules.com/data-downloads) / `emolecules` | [x] Local import; [ ] Remote API | Official access guide and local search | — | website_only |
| 19 | [Enamine REAL](https://real.enamine.net/api/static/API_usage.html) / `enamine` | [x] Auth adapter | `exact` | — | deferred_credentials |
| 20 | [EPO OPS](https://www.epo.org/en/searching-for-patents/data/web-services/ops) / `epo` | [x] Auth adapter | `search`, `family`, `legal` | — | deferred_credentials |
| 21 | [Europe PMC / PubMed literature](https://europepmc.org/RestfulWebService) / `europepmc` | [x] Public API | `search`, `article`, `fulltext` | — | semantic_passed (new case) |
| 22 | [Google Patents Public Datasets](https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query) / `google_patents` | [x] Configured adapter | `query_plan`, `search`, `job_results` | — | deferred_credentials |
| 23 | [GPCRdb](https://docs.gpcrdb.org/web_services.html) / `gpcrdb` | [x] Public API | `protein`, `structures`, `residues` | — | semantic_passed |
| 24 | [GTEx](https://gtexportal.org/api/v2/redoc) / `gtex` | [x] Public API | `genes`, `expression`, `eqtl`, `datasets` | — | semantic_passed |
| 25 | [GtoPdb](https://www.guidetopharmacology.org/webServices.jsp) / `gtopdb` | [x] Auth adapter | `ligands`, `targets`, `ligand`, `interactions` | — | deferred_credentials |
| 26 | [Human Protein Atlas](https://www.proteinatlas.org/about/help/dataaccess) / `hpa` | [x] Public API | `gene`, `search`, `files` | `artifact` | semantic_passed |
| 27 | [KLIFS](https://klifs.net/swagger/) / `klifs` | [x] Public API | `kinases`, `structures` | — | semantic_passed |
| 28 | [Lens](https://docs.api.lens.org/) / `lens` | [x] Auth adapter | `search` | — | deferred_credentials |
| 29 | [Life Chemicals](https://lifechemicals.com/downloads) / `lifechemicals` | [x] File discovery | `files` | `artifact` | partial |
| 30 | [LOTUS](https://lotus.naturalproducts.net/documentation) / `lotus` | [x] Public API | `search`, `exact` | — | semantic_passed |
| 31 | [MedChemExpress](https://www.medchemexpress.com/screening/Bioactive_Compound_Library.html) / `mce` | [x] Local import; [ ] Remote API | Official access guide and local search | — | blocked |
| 32 | [Mcule](https://doc.mcule.com/api) / `mcule` | [x] Public API | `database_files`, `compound`, `lookup` | `dataset` | semantic_passed |
| 33 | [MolPort](https://api.molport.com/openapi) / `molport` | [x] Auth adapter | `availability`, `results` | — | deferred_credentials |
| 34 | [Open Targets Platform](https://platform-docs.opentargets.org/data-access/graphql-api) / `opentargets` | [x] Public API | `target`, `search`, `diseases`, `files` | `artifact` | semantic_passed |
| 35 | [FDA Orange Book](https://open.fda.gov/apis/drug/orange-book/) / `orangebook` | [x] Public API | `search` | `partition` | semantic_passed |
| 36 | [Open Reaction Database](https://github.com/open-reaction-database/ord-data) / `ord` | [x] File discovery | `tree` | `dataset` | semantic_passed |
| 37 | [PDBbind+](https://www.pdbbind-plus.org.cn/) / `pdbbind` | [x] Local import; [ ] Remote API | Official access guide and local search | — | website_only |
| 38 | [PLINDER](https://plinder-org.github.io/plinder/tutorial/dataset.html) / `plinder` | [x] File discovery | `objects` | `object` | blocked |
| 39 | [PubChem Compound / BioAssay](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest) / `pubchem` | [x] Public API | `properties`, `batch_properties`, `source_categories`, `similarity`, `substructure`, `assay`, `files` | `sdf`, `artifact` | semantic_passed; substructure live smoke 2026-09-23 |
| 40 | [RCSB PDB / wwPDB](https://www.rcsb.org/docs/programmatic-access/web-apis-overview) / `rcsb` | [x] Public API | `entry`, `ligand`, `nonpolymer_entity`, `polymer_entity`, `search`, `by_uniprot` | `structure` | semantic_passed |
| 41 | [Reactome](https://reactome.org/ContentService/) / `reactome` | [x] Public API | `pathway`, `pathways_by_uniprot`, `files` | `artifact` | semantic_passed |
| 42 | [STRING](https://string-db.org/help/api/) / `string` | [x] Public API | `map_ids`, `network`, `partners`, `enrichment`, `version` | — | semantic_passed |
| 43 | [SureChEMBL](https://chembl.gitbook.io/surechembl/api/api-documentation) / `surechembl` | [x] Public API | `chemical`, `chemical_by_name`, `chemical_by_smiles`, `family`, `files` | `artifact` | semantic_passed |
| 44 | [TargetMol](https://www.targetmol.com/) / `targetmol` | [x] Local import; [ ] Remote API | Official access guide and local search | — | blocked |
| 45 | [Therapeutics Data Commons](https://tdcommons.ai/) / `tdc` | [x] File discovery | `datasets`, `dataset` | `dataset` | semantic_passed |
| 46 | [TTD](https://ttd.idrblab.cn/) / `ttd` | [x] Local import; [ ] Remote API | Official access guide and local search | — | website_only |
| 47 | [UniChem](https://www.ebi.ac.uk/unichem/api/docs) / `unichem` | [x] Public API | `sources`, `mapping`, `files` | `artifact` | semantic_passed |
| 48 | [UniProt](https://www.uniprot.org/help/api) / `uniprot` | [x] Public API | `entry`, `search`, `files` | `fasta`, `artifact` | semantic_passed |
| 49 | [USPTO ODP](https://data.uspto.gov/apis/patent-file-wrapper/search) / `uspto` | [x] Auth adapter | `application` | — | deferred_credentials |
| 50 | [WuXi GalaXi](https://chemistry.wuxiapptec.com/library) / `wuxi` | [x] Local import; [ ] Remote API | Official access guide and local search | — | website_only |
| 51 | [ZINC22 / Cartblanche](https://files.docking.org/zinc22/) / `zinc` | [x] File discovery | `files` | `artifact` | discovery_passed |

`files → artifact` is available for 15 official file roots. File discovery does not crawl whole websites; artifact URLs must come from a selected official listing. Login pages, JavaScript-only pages and schema changes do not become false empty datasets.

Known boundaries:

- PDBbind+, DrugBank, TTD, eMolecules, ChemDiv, MCE, TargetMol, WuXi, Aladdin and BLD expose access guidance and authorized local import, not remote catalog APIs.
- Life Chemicals sample downloads redirect to login. ZINC `subsets/` discovery works, while tested `2d/` directories return 401. PLINDER anonymous GCS access returned 401.
- DepMap requires a supplied Figshare article ID. TDC supports the official single-file registry and follows its original Dataverse records; it does not run remote Python or cover all benchmarks.
- Google Patents provides parameterized English-title SQL, dry-run, search and job results, not full claims NLP. Execution requires explicit billing limits and `dry_run=false`.
- SureChEMBL provides identity lookup, family lookup and bulk discovery, not a local full-corpus structure index or FTO analysis.
- CompTox currently covers identity/ToxValDB. Full ToxCast/ToxRefDB ETL and Mcule authenticated quotes remain future work. MolPort search does not submit purchases.

## Categories

| Category | Responsibility and examples |
| --- | --- |
| `chemical_identity` | Structures and cross-references: PubChem, ChEBI, UniChem, COCONUT, LOTUS |
| `experimental_structure` | Complexes and pockets: RCSB, BioLiP, KLIFS, GPCRdb, PLINDER, PDBbind+ |
| `predicted_structure` | Predicted coordinates/confidence: AlphaFold DB |
| `bioactivity` | Binding, pharmacology and cellular activity: ChEMBL, BindingDB, GtoPdb, DepMap |
| `target_biology` | Sequences, expression, networks and pathways: UniProt, Open Targets, HPA, GTEx, STRING, Reactome |
| `clinical_regulatory` | Approval and trials: Drugs@FDA, Orange Book, DrugCentral, ClinicalTrials.gov |
| `patent` | Patent chemistry, full text, families and legal events: SureChEMBL, Google Patents, Lens, EPO, USPTO |
| `procurement` | Catalogs and sourcing: Mcule, Chemspace, Enamine, MolPort, ZINC, regional suppliers |
| `safety_admet` | Toxicology, ADMET and PGx: CompTox, ClinPGx, TDC and original activity sources |
| `synthesis` | Reactions and synthesis space: ORD, TDC, Enamine |
| `ml_benchmark` | Derived benchmarks: TDC, PLINDER, PDBbind+ |
| `literature` | Primary papers, abstracts and accessible full text: Europe PMC/PubMed |

## Agent interfaces

```json
{
  "mcpServers": {
    "molquarry": {
      "command": "/absolute/path/MolQuarry/.venv/bin/molquarry-mcp",
      "env": {
        "MOLQUARRY_HOME": "/absolute/path/workspace/.molquarry",
        "MOLQUARRY_WORKSPACE": "/absolute/path/workspace"
      }
    }
  }
}
```

There are **15 MCP tools**: `list_skills`, `read_skill`, `list_sources`, `describe_source`, `query_database`, `plan_download`, `download_data`, `import_catalog`, `local_catalogs`, `search_local_catalog`, `collect_target_evidence`, `build_target_bundle`, `import_af3_results`, `screen_compound_sourcing`, and `export_target_inventory`. Workflow paths are relative to `MOLQUARRY_WORKSPACE`, defaulting to the server's current working directory. Set it to the agent's project directory when starting a server from another location. Workflow outputs never default to `~`.

Skills and supporting references ship inside the wheel and source distribution. Discover them through MCP `list_skills`, load an entrypoint with `read_skill(name)`, and read a listed reference with `read_skill(name, path)`. The SDK equivalents are `molquarry.skills.list_skills()` and `read_skill(name, path="SKILL.md")`; discovery returns each name, description, entrypoint and readable resources. No global home-directory installation is performed.

```bash
molquarry skills
molquarry skill molquarry-analogue-search
molquarry skill molquarry-target-modulators --path references/workflow.md
molquarry query pubchem substructure --params '{"smiles":"CC(=O)Oc1ccccc1C(=O)O","limit":5}'
```

Start with `list_skills → read_skill → describe_source → query_database`, or use `collect_target_evidence` and the reviewed bundle workflow. Substructure defaults to exact stereochemistry, charge and isotope matching (unspecified query centers remain unspecified). It returns capped CIDs with unknown total and no continuation. Retain this limit and all matching options; neither a hit nor a missing record proves activity or novelty. MCP network tasks run outside the event loop; large files are returned as paths and manifests.

SDK example:

```python
from molquarry import MolQuarry
from molquarry.workflows import ModulatorSearch, collect_modulator_evidence

with MolQuarry() as q:
    for page in q.iter_pages("chembl", "activities", target_chembl_id="CHEMBL203", max_pages=2):
        print(page.records, page.next_parameters)
    result = collect_modulator_evidence(
        q, ModulatorSearch(targets=["ADRB2"], mode="agonist"), ".molquarry/adrb2-search"
    )
```

A remaining continuation means the result is incomplete. Some APIs, including BindingDB and full-text XML, fetch a whole bounded response before local pagination. Use official bulk snapshots for large training corpora.

## Downloads and local catalogs

```bash
molquarry query chembl files --params '{"path":"latest/","limit":10}'
molquarry plan alphafold structure --params '{"accession":"P00533","format":"cif"}' --output af-plan.json
molquarry fetch af-plan.json --max-bytes 10000000
molquarry import-catalog mce .molquarry/imports/mce.csv --source-version vendor-export-2026-09
molquarry local-catalogs
molquarry local-search mce-REPLACE_WITH_SNAPSHOT_HASH --field catalog_id --value HY-123
```

Plans do not download bytes; `fetch` uses the saved plan without resolving “latest” again. Downloads default to 100 MiB, preserve source bytes, validate host/signature/checksum where available, and refuse overwrite. Local imports support gzip, preserve original fields and use SQLite text/exact-field search. Default import limits are 100 MiB and 100,000 rows. Identical imports reuse snapshots. MCP imports are restricted to the configured imports/downloads directories; scientific workflow paths use the project workspace.

## Credentials and provenance

| Source | Environment variables |
| --- | --- |
| GtoPdb | `GTOPDB_API_KEY` |
| Enamine REAL | `REAL_API_KEY` |
| Chemspace | `CHEMSPACE_API_KEY` |
| MolPort | `MOLPORT_API_KEY` |
| Lens | `LENS_API_TOKEN` |
| EPO OPS | `EPO_CONSUMER_KEY`, `EPO_CONSUMER_SECRET` |
| CompTox | `COMPTOX_API_KEY` |
| USPTO ODP | `USPTO_API_KEY` |
| Google Patents | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_ACCESS_TOKEN` |
| PLINDER, optional | `PLINDER_GCP_ACCESS_TOKEN` |

Missing credentials fail before requests. Authenticated responses do not enter the shared disk cache. QueryResult preserves raw records, total/returned counts, continuation, source URLs, retrieval times, request/response hashes, source versions when provided and license links. License metadata does not grant commercial training or redistribution rights.

## Engineering checklist

- [x] 51 sources, 12 categories, strict schemas and source-specific access levels.
- [x] 41 remote adapters and 10 authorized local-file workflows.
- [x] SDK, JSON CLI and 15 MCP tools, including installed skill/resource discovery.
- [x] Six distributed search skills, including analogue/core expansion, selectivity evidence, structure templates and primary assay review.
- [x] Pacing, retries, cache isolation, response limits, original-file manifests and bounded local imports.
- [x] Europe PMC abstracts/full text, ChEMBL assay/document expansion and RCSB entity traversal.
- [x] Inhibitor/agonist collection with identity resolution, provenance, review queue and coverage.
- [x] Explicit evidence curation, preserved censoring and unresolved source-value conflicts.
- [x] Experimental PDB export, residue-mapped alignment and available resolution metadata.
- [x] Excel compound/measurement/procurement workbook and RDKit-prepared 3D SDF output.
- [x] Official AF3 input export, actual-result ZIP import and explicit local-runner interface.
- [x] Public-source FDA approval example and MTDH/SND1 worked case.
- [x] STK17B all-ligand inventory, full shortlist screen, exact stereo checks and English sourcing skill.
- [x] Batched identity/detail queries, resumable request evidence and bounded parallel conformer generation.
- [x] Reviewed URL query preservation, pagination-loop handling, archived-input integrity and SDF read-back/resume checks.
- [x] Excel export avoids repeated worksheet scans; the local 500-row × 24-column benchmark retained identical values and ran about 10.8× faster than the baseline.
- [ ] Authenticated live tests for nine credentialed sources and restricted vendor downloads.
- [ ] AlphaFold Server browser automation/session integration; actual AF3 inference acceptance.
- [ ] Verified supplier offers with current stock, prices, delivery region and lead time.
- [ ] Exhaustive paper/SI/correction review and full chemical identity assignment for all local labels.
- [ ] Versioned RDKit standardization, persistent compound/target registries and assay harmonization.
- [ ] Patent/Markush structure indexing, full claims context and full-source federated search.
- [ ] Resumable multi-file snapshots, background jobs, shared quotas and incremental release discovery.
- [ ] Native licensed feeds, additional import formats, hosted multi-user API and UI.

## Verification

```bash
pytest -q
ruff check .
ruff format --check .
python -m build
# Explicit live checks; ordinary pytest stays offline.
python scripts/verify_public.py --source europepmc --source drugsfda --source orangebook
python -m pip install -e '.[mcp,dev,verify,deliverables]'
python scripts/verify_public.py --output .molquarry/public-verification.json
```

See architecture (`docs/architecture.md`, local workspace), source verification (`docs/verification.md`, local workspace), workflow review (`docs/workflow-review.md`, local workspace), MTDH/SND1 (`examples/mtdh_snd1/README.md`, local workspace), EGFR SDK usage (`examples/egfr_workflow.py`, local workspace), and Gleevec approval evidence (`examples/imatinib_evidence.py`, local workspace). Real failures and deferred authentication remain visible; mock tests are not live source validation. Source APIs, data releases, licenses and inventory can change.
