---
name: molquarry-analogue-search
description: Find chemical analogues or compounds containing a specified core with MolQuarry PubChem similarity and substructure searches, then resolve exact identities and experimental evidence. Use to expand a seed ligand or a supplied scaffold into a bounded screening shortlist; does not establish activity, novelty or supplier stock.
---

# Analogue and core expansion

Start from the user's seed SMILES, exact identifier or SDF structure. Resolve names with `pubchem.properties`, preserving the input structure and full InChIKey. Resolve salt, stereochemistry and tautomer ambiguities before treating two hits as the same candidate. A core stripped from a seed is a derived query: record its SMILES and the transformation, and keep the original seed.

## Search modes

Inspect `describe_source("pubchem")`, then use `query_database` with one of these modes. These are example tool arguments, not a default aspirin task:

```json
{"source":"pubchem","operation":"similarity","parameters":{"smiles":"CC(=O)Oc1ccccc1C(=O)O","threshold":90,"limit":20}}
```

```json
{"source":"pubchem","operation":"substructure","parameters":{"smiles":"CC(=O)Oc1ccccc1C(=O)O","stereo":"exact","match_charges":true,"match_isotopes":true,"rings_not_embedded":false,"limit":20}}
```

Similarity uses PubChem's 2D fingerprint threshold on a 0–100 scale, not an RDKit Morgan threshold or a returned per-hit score. Substructure finds molecules containing the supplied SMILES core; it does not perform automatic scaffold hopping or a SMARTS search. Explicitly record relaxed charge/isotope/stereo settings. Exact stereo does not fill in unspecified stereocenters.

Both operations return CIDs with a cap, an unknown total and no continuation. Preserve this coverage limit even when fewer than `limit` hits return. Broad cores may time out; narrow the query or report the failure, rather than interpret it as zero matches. Deduplicate repeated queries by exact identity while preserving which query retrieved each hit. Keep a bounded query and hit budget; do not recursively expand every new hit.

## Build a reviewable shortlist

Resolve returned CIDs with `pubchem.properties` (`namespace="cid"`, `identifier` as a string); returned order is not a similarity ranking. For structure export use `plan_download` with `source="pubchem"`, `operation="sdf"`, and `parameters={"cids":[...],"record_type":"2d"}` followed by `download_data`. A 3D PubChem conformer is not a bound pose. Locally generated conformers must be labeled as generated.

Map full InChIKeys with `chembl.molecules_by_inchikey`; inspect `chembl.activities` for mapped molecules before assigning known-active status. Keep unmeasured molecules as unmeasured. For candidate sourcing, load the separate `molquarry-compound-sourcing` skill through `read_skill` rather than treat a CID or chemical similarity as catalog availability.

Return a seed/query log and a candidate table with source CID, full identity, retrieval route, retrieved-at time, experimental evidence status and unresolved identity differences. If local fingerprints or scaffolds are computed for diversity selection, record their algorithm/version and label those values as locally computed. Export the selected structures and retained raw `QueryResult` provenance into a new project run directory. Pass the exact selected identities and source hashes to screening.

## Basis and boundaries

The [official PubChem PUG REST specification](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest) defines the synchronous search endpoints and matching options. PubChem search is an expansion tool; experimental validation and a dedicated patent investigation answer different questions.
