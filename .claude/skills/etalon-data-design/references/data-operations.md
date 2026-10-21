# Data operations: source, candidate, assay and geometry

All paths below are absolute. `store` means an existing configured
`CampaignStore`; `etalon.data.cli.existing_store(path)` opens an existing one
without silently creating a new scientific campaign. Choose operations as needed.

| MCP tool | Direct SDK | Effect and decision-relevant result |
| --- | --- | --- |
| `etalon_data_sources(source="")` | `etalon.boundary.quarry.describe(source or None)` | No HTTP. Catalog/operation schemas; source presence is not current API availability. |
| `etalon_data_plan(request, max_requests, max_bytes, max_seconds)` | `plan_data(request, budget=DataBudget(...))` in `etalon.data.service` | No HTTP or data run; binds request, local input bytes, budget and infrastructure into `plan_id`. |
| `etalon_data_run(request, workspace, run_id, ..., plan_id="")` | `run_data(request, Path(workspace), run_id=..., budget=..., expected_plan_id=...)` | Synchronous local/HTTP acquisition, frozen inputs and sealed evidence; failure may leave an unsealed run. No observation admission. |
| `etalon_data_status(snapshot, include_files=False)` | `etalon.data.artifacts.status(Path(snapshot))` | Verifies a sealed snapshot or reports an interrupted run. Read-only; inspect coverage and usage, not just file existence. |
| `etalon_data_prepare(snapshot, workspace, run_id, id_field="", smiles_field="", allow_partial=False, identity_policy=None)` | `etalon.data.library.prepare_library(Path(snapshot), Path(workspace), run_id=..., ...)` | Writes a new library snapshot, `library.csv` and every source-row identity/rejection mapping; does not infer activity. |
| `etalon_data_import_candidates(database, snapshot, candidate_ids=None)` | `etalon.data.library.import_candidates(store, Path(snapshot), candidate_ids=...)` | Binds library/source identity and adds eligible molecular features within the pool limit; does not create observations. |
| `etalon_data_review_template(snapshot, endpoint_id, protocol)` | `etalon.data.ingress.review_template(Path(snapshot), endpoint_id=..., protocol=...)` | Read-only review draft, all observations initially withheld. Requires a library from a collected target dossier, not a plain catalog. |
| `etalon_data_import_assays(database, snapshot, review)` | `etalon.data.ingress.import_assays(store, Path(snapshot), review)` | Atomic reviewed historical measurements; exact target/state/quantity/unit checks and experiment identity deduplication. |
| `etalon_data_attach_handoffs(database, workspace, artifact_id, rationale, candidate_ids=None)` | `etalon.data.ingress.attach_handoffs(store, Path(workspace), artifact_id, rationale=..., candidate_ids=...)` | Reads a verified `md_system_input/v1` artifact and binds geometry between rounds. This mutation is not a status query and grants no PRISM execution authority. |

## A local catalog without network

Given a real local CSV with `id,smiles`, this independently prepares a library:

```python
from pathlib import Path
from etalon.boundary.quarry import DataBudget
from etalon.data.service import plan_data, run_data
from etalon.data.library import prepare_library

catalog = Path("/absolute/catalog.csv")
workspace = Path("/absolute/data-workspace")
request = {"kind": "import_catalog", "source": "chembl", "path": str(catalog),
           "options": {"source_version": "YOUR-LOCAL-CATALOG-VERSION"},
           "search": {"limit": 100}}
budget = DataBudget(max_requests=0, max_bytes=1000000, max_seconds=30)
plan = plan_data(request, budget=budget)
acquired = run_data(request, workspace, run_id="catalog-001", budget=budget,
                    expected_plan_id=plan["plan_id"])
library = prepare_library(Path(acquired["snapshot"]), workspace,
                          run_id="library-001", id_field="fields.id",
                          smiles_field="fields.smiles")
```

Here `source="chembl"` chooses a catalog adapter; it does not prove ChEMBL supplied
your CSV. Supply truthful source/version provenance. Query/catalog field mappings
are explicit; source rows and deduplicated parents are different denominators.
Match `identity_policy` to subsequent screening. Review changed states and rejected
rows before choosing a candidate subset. `allow_partial=True` is an explicit
coverage choice, not a way to claim complete retrieval.

Supported request kinds are `query`, `collect`, `import_catalog`, `search_catalog`,
`sourcing`, `download`, and `bundle`. For source-specific query/download schemas,
use `describe("chembl")` or the known provider schema. `collect` accepts the
MolQuarry `InhibitorSearch` config; `bundle` consumes a collected snapshot and an
absolute `curation_path`. A plan does not authorize network or purchasing beyond
the task. Data request/byte allowances are separate from GPU/scientific costs;
the deadline is cooperative, not a process kill guarantee.

Run IDs are append-only: repeating `run_data` or `prepare_library` under an
existing run ID is refused, not a transparent acquisition replay. Inspect the
saved run before deciding on any separately authorized new attempt. Candidate
import replays add zero existing candidates; unchanged reviewed experiments
replay without another observation. An acquisition timeout, partial result or
rejected input is not an inactive assay. Keep successful pages, continuation,
failure JSON, measured usage and unknown overhead.

## Reviewed measurements, not suggested labels

For an existing dossier-derived library and historical-only endpoint:

```python
from pathlib import Path
from etalon.data.cli import existing_store
from etalon.data.ingress import review_template, import_assays

library = Path("/absolute/data/assay-library")
store = existing_store(Path("/absolute/campaign.sqlite"))
review = review_template(library, endpoint_id="kd", protocol="assay-protocol/1")
# Save this draft and inspect inventory.json, targets.json and identity-map.json.
# Fill reviewer, reviewed_at, exact accession/taxon/construct and justified decisions.
# Keep unreviewed rows as decision="withhold"; do not invent experiment identifiers.
# After those scientific assertions are actually supplied:
# receipt = import_assays(store, library, review)
```

The target endpoint must have `queryable=False`, `requires_handoff=False` and the
exact target/protocol. Accepted entries need `measurement_index`, `decision`,
`experiment_id`, `rationale`; changed state, suspected duplicates or validity flags
need the corresponding explicit review text. Censored/approximate values cannot
become exact GP labels; Kd, Ki, IC50 and EC50 do not become interchangeable through
unit conversion. Textual review is an accountable assertion, not machine proof of
comparability. Keep raw measurements and withheld rows even when none are admitted.

Astra interpretations or editing proposals remain hypothesis artifacts with
their input/source references. Do not insert them into this review as if measured.
For a later research use, preserve development/calibration/independent-validation
roles; having once consumed evaluation feedback makes it seen for future work.

## Attach existing geometry only when needed

```python
from pathlib import Path
from etalon.data.cli import existing_store
from etalon.data.ingress import attach_handoffs

store = existing_store(Path("/absolute/campaign.sqlite"))
receipt = attach_handoffs(store, Path("/absolute/screen-workspace"),
                         "EXISTING-ARTIFACT-ID", candidate_ids=["EXISTING-PARENT-ID"],
                         rationale="Use this verified geometry for the stated follow-up")
```

Retain the screen artifact and contract identity. Missing selected rows, changed
geometry or active rounds are reasons to stop, not to silently overwrite a
binding. A graph-only candidate and an exported design pose are not automatically
a prepared MD system. Runtime preflight and actual spend authorization still apply.
