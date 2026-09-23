---
name: molquarry-structure-templates
description: Find and review experimental protein structures, cognate ligands and receptor templates for docking with MolQuarry RCSB, KLIFS or GPCRdb queries. Use for receptor selection, co-crystal reference ligands and cross-docking sets; distinguish experimental complexes from predicted models and assess construct and pocket coverage.
---

# Receptor and reference-ligand search

Resolve the intended species, UniProt accession, domain, mutations and binding site. Search each target independently when a complex is requested; a structure of one component alone is not evidence of the desired interface geometry.

## Traverse experimental structures

Inspect `describe_source("rcsb")`. Prefer accession mapping over free-text hits:

```json
{"source":"rcsb","operation":"by_uniprot","parameters":{"uniprot":"P00533","limit":20}}
```

Follow `next_parameters` within a stated page budget. Use `entry` with each returned `pdb_id` to read experimental method, resolution where applicable and polymer/nonpolymer entity IDs. Use `polymer_entity` with `pdb_id` and `entity_id` to verify target sequence/species/construct; use `nonpolymer_entity` to identify CCD components and then `ligand` with `ccd_id` for chemical identity. Entity IDs are entry-specific, not chain IDs. A CCD component may be buffer, ion or cofactor; do not label every nonpolymer as a reference inhibitor.

For kinase pocket annotations, query `klifs.kinases` with `name`/`species`, then `klifs.structures` with the returned `kinase_id`. For GPCRs use `gpcrdb.structures` with a verified `entry_name` such as `adrb2_human`. Inspect each source schema first; these annotations supplement the original coordinates.

## Review coordinates and select templates

Download original mmCIF through `plan_download` (`source="rcsb"`, `operation="structure"`, `parameters={"pdb_id":"..."}`) and `download_data`. Keep the plan, manifest and unmodified source. Inspect the requested pocket's residue coverage, alternate locations, occupancies, relevant waters/metals/cofactors, ligand contacts, covalent connections and assembly context in the coordinate file. Record missing information explicitly. Global resolution alone does not establish local pocket quality.

Return a template table with PDB ID, target accession, chains/entities, construct differences, method/resolution, pocket coverage, ligand CCD/full identity, cofactors, source hash and selection rationale. Select receptor conformations spanning relevant states when justified. Keep native receptor/ligand pairs separate from ligands transferred after receptor alignment, and record the alignment and residue mapping for transferred poses. An atom-mapped RMSD needs comparable ligand chemistry and the same receptor coordinate frame.

If no usable experimental structure exists, inspect `alphafold` through `describe_source` and label any selected model as predicted, retaining confidence and missing-context limits. A generated or predicted ligand pose cannot serve as an experimental redocking reference.

## Screening handoff

Provide original receptor coordinates, the chosen ligand identity and extracted pose, the site definition and a manifest of preparation decisions. Recovering a cognate ligand's experimental pose tests docking protocol performance. Re-docking a predicted docked pose tests reproducibility/self-consistency instead; name the comparison and preserve its reference type. A successful RMSD check does not establish affinity, selectivity or physical validity by itself.

The [RCSB API overview](https://www.rcsb.org/docs/programmatic-access/web-apis-overview) documents the entry/entity hierarchy and separate search/data APIs. MolQuarry's adapter currently accepts legacy four-character PDB IDs and does not expose every RCSB search modality.
