# Inspect or import a design bundle

Use this when the input is a producer-exported
`prism-design-candidate-bundle/1`, not an arbitrary SDF or Astra text proposal.
ETALON consumes the bundle; the independent PRISM producer retains ownership of
generation, chemistry checks and its evidence. Existing schema/CLI compatibility
is retained. No generation or scoring is required for these SDK operations.

```python
from pathlib import Path
from etalon.data.cli import existing_store
from etalon.data.design import read_bundle, import_bundle, verify_import, design_index

path = Path("/absolute/design-export/bundle.json")
checked = read_bundle(path)  # read-only: schema, references, bytes, hashes, identities
# Inspection can finish here without opening a campaign.
store = existing_store(Path("/absolute/campaign.sqlite"))
receipt = import_bundle(store, path, mode="index_only")
verified = verify_import(store, path, mode="index_only")  # read-only receipt check
index = design_index(store)  # provenance/diagnostics independent of the scalar table
```

`index_only` retains every occurrence, molecule/state/pose and failure without
inventing features for an incompatible campaign representation. Use `mode="pool"`
only for an existing `MOLECULAR_REPRESENTATION` campaign when admitting eligible
molecules is the task. Importing the same bundle/mode again returns
`imported=False`, `added_candidates=0`, `added_records=0`; it appends no new import
event. Index-only can precede a later explicit pool import without rewriting the
source occurrence records. Verification reports the stored original receipt,
not the zero-additions replay receipt.

Interpret `indexed_slots`, `eligible_molecules` and `added_candidates` separately.
Two source slots can represent one molecule and different poses. Keep duplicate
slots, DECODE_FAILED/NOT_ATTEMPTED and unknown/not-evaluated validation. Eligibility
requires the producer's bound evidence; successful JSON parsing is insufficient.
Read/hash checks do not rerun chemistry or establish activity. Import creates no
observations, spends no scientific execution budget, grants no MD capability and
does not attach an execution-ready handoff. CPU processing cost is a separate
measurement, not implied zero by `observations_created=0`.

Stop on changed artifact bytes, invalid schema/identity, path escape, slot-count
mismatch, conflicting provenance, missing import or active campaign work. Do not
repair a producer bundle in place. Retain the rejected bundle and obtain a new
producer export if appropriate. An eligible claim with unknown QC is rejected;
do not downgrade the check or discard rejected slots to force admission.

The source example accepts `--bundle tests/fixtures/prism_design_bundle_v1/bundle.json`
for a known engineering fixture. It creates a separate index-only synthetic store,
checks replay and retains original source roles; this is interface evidence,
not new generated chemistry, physical preparation or an independent science test.
