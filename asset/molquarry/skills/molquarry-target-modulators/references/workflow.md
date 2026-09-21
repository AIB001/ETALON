# Executable workflow and review contract

Requires MolQuarry 0.3 or later. Install the project with `python -m pip install -e '.[mcp]'` if it is not already available. The skill does not require a specific repository location or create credentials.

## Collector configuration

```json
{
  "targets": ["MTDH", "SND1"],
  "taxon": 9606,
  "literature_until": "2026-09-19",
  "aliases": {"MTDH": ["AEG-1", "metadherin"], "SND1": ["Tudor-SN"]},
  "max_pages": 10,
  "max_details": 100,
  "review_pmids": ["35121987", "39792778"],
  "max_fulltexts": 8,
  "search_assay_descriptions": true,
  "pubchem_ccd_xrefs": true,
  "bindingdb_cutoff_nm": 10000000
}
```

```bash
molquarry inhibitors --config search.json --output-dir .molquarry/my-search
```

`max_details` independently bounds assay/document/molecule/structure/CCD detail lists and additional assay-activity expansion. It is not a total request quota. The per-query `max_pages` limit still applies. Large targets may need narrower queries or official bulk snapshots. Alias phrases are quoted; primary symbols are used for ChEMBL assay-description searches. Extend synonyms with additional explicit queries as needed.

`literature_until` filters Europe PMC `FIRST_PDATE` (first publication), not issue date. Activity and structure endpoints return current snapshots; the collector does not reconstruct a historical database. User-supplied review PMIDs are explicit follow-ups and can be outside the literature date filter; review their dates before including them in the requested period.

SDK equivalent:

```python
from molquarry import MolQuarry
from molquarry.workflows import InhibitorSearch, collect_inhibitor_evidence

config = InhibitorSearch(targets=["MTDH", "SND1"], max_pages=10)
with MolQuarry() as quarry:
    summary = collect_inhibitor_evidence(quarry, config, ".molquarry/my-search")
```

## Follow-up queries

```bash
molquarry query chembl activities --params '{"document_chembl_id":"CHEMBL6087484","limit":100}'
molquarry query chembl activities --params '{"assay_chembl_id":"CHEMBL6091566","limit":100}'
molquarry query europepmc article --params '{"pmid":"39792778"}'
molquarry query europepmc fulltext --params '{"pmcid":"PMC8818087","contains":"C26","limit":100}'
molquarry query rcsb nonpolymer_entity --params '{"pdb_id":"7KNX","entity_id":"4"}'
```

Use `describe` to inspect current parameter schemas. Examples identify real records but are not universal constants. Save additional responses with provenance and extend the dossier before referencing records that were not collected.

## Explicit review schema

Use `Curation.model_json_schema()` from `molquarry.workflows.review` for the full strict schema. A minimal candidate has:

```json
{
  "candidate_id": "doi:10.1021/acs.jmedchem.4c02574#C19",
  "label": "C19",
  "modality": "small_molecule",
  "mechanism": "ppi_disruption",
  "status": "supported",
  "evidence_level": "abstract_reviewed",
  "target_accessions": ["Q86UE4", "Q7KZF4"],
  "references": [{
    "source": "europepmc",
    "record_id": "39792778",
    "locator": "abstractText",
    "url": "https://doi.org/10.1021/acs.jmedchem.4c02574"
  }],
  "measurements": [],
  "identities": [],
  "notes": ["Primary abstract reviewed; full text and SI not retrieved."]
}
```

Curation also requires `reviewed_at`, `papers_reviewed` (`pmid`, `decision`, `reason`) and `limitations`. Every measurement has `endpoint`, numeric `value`, `unit`, `relation`, `assay`, `context`, `reference`, and optional `error` and `comparison_group`. Use a comparison group only for records believed to represent the same assay/measurement. A hundredfold or larger difference triggers an unresolved conflict; no value is overwritten.

A reference uses source record identity: PMID or PMCID for Europe PMC, ChEMBL activity number or CHEMBL ID, PDB/CCD ID for RCSB, CID for PubChem. Full-text locators are returned `block_id` or XML `locator`. BindingDB REST rows lack an experiment identifier; `bindingdb_evidence_id(raw_record)` creates an explicitly local row hash. It is not a BindingDB accession.

```bash
molquarry review-inhibitors .molquarry/my-search/dossier.json curation.json \
  --output .molquarry/my-search/reviewed.json
```

The source dossier remains unchanged. The output refuses overwrite. An unfetched reference or unknown paragraph fails validation. A successful validation means the curation is traceable and schema-valid; scientific interpretation remains the reviewer's responsibility.

## Agonists and deliverables

`mode` accepts `inhibitor`, `agonist` or `modulator`. The public aliases `ModulatorSearch` and `collect_modulator_evidence` expose the same collector. Curation supports `activity_direction`: inhibitor, agonist, antagonist, positive_modulator, negative_modulator or unknown. Receptor agonism uses mechanism `receptor_activation`; a positive allosteric modulator can use `positive_allosteric_modulation`.

```bash
python -m pip install -e '.[mcp,deliverables]'
molquarry collect ADRB2 --mode agonist --output-dir .molquarry/adrb2-evidence
molquarry bundle .molquarry/adrb2-evidence/dossier.json curation.json
```

Bundle defaults: all discovered structure IDs; 1 GB total structure-download budget; 100 MB per structure; at most 20 public Mcule identity queries; generated fallback conformers labeled explicitly. A large run may hit these bounds. Set `--max-total-bytes`, `--max-structures` or `--max-purchase-queries` deliberately and report resulting limits. `--no-purchasing` records that sourcing was not requested. Source and aligned coordinates, preparation logs and unknown chemical identities remain distinct.

The workbook is a readable index. Scientific claims still point to source evidence, and the raw JSON contains full fields beyond Excel's cell limits. Frozen rows, filters, explicit string cells and separate sheets support review without interpreting source text as spreadsheet formulas.
