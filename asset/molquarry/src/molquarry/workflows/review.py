"""Validate an agent's explicit curation against a collected dossier and retain conflicts."""

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from ..models import InputModel

ReferenceSource = Literal["europepmc", "chembl", "rcsb", "bindingdb", "pubchem"]


class EvidenceReference(InputModel):
    source: ReferenceSource
    record_id: str
    locator: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")


class Measurement(InputModel):
    endpoint: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    unit: str
    relation: Literal["=", "<", ">", "<=", ">=", "~"] = "="
    error: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    assay: str = Field(min_length=1)
    context: str = Field(min_length=1)
    reference: EvidenceReference
    comparison_group: str | None = None


class Candidate(InputModel):
    candidate_id: str = Field(min_length=1)
    label: str
    modality: Literal["small_molecule", "peptide", "other"]
    activity_direction: Literal[
        "inhibitor", "agonist", "antagonist", "positive_modulator", "negative_modulator", "unknown"
    ] = "unknown"
    mechanism: Literal[
        "ppi_disruption",
        "rna_binding_inhibition",
        "nuclease_inhibition",
        "direct_target_inhibition",
        "binding_only",
        "indirect",
        "computational",
        "receptor_activation",
        "positive_allosteric_modulation",
    ]
    status: Literal[
        "supported",
        "screening_hit",
        "weak",
        "binding_only",
        "indirect",
        "computational_only",
        "needs_review",
    ]
    evidence_level: Literal["fulltext_reviewed", "abstract_reviewed", "database_only"]
    target_accessions: list[str]
    references: list[EvidenceReference] = Field(min_length=1)
    measurements: list[Measurement] = Field(default_factory=list)
    identities: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def separate_predictions(self):
        if self.mechanism == "computational" and self.status != "computational_only":
            raise ValueError(
                "Computational-only evidence cannot be labeled experimentally supported"
            )
        return self


class PaperReview(InputModel):
    pmid: str
    decision: Literal["included", "excluded", "duplicate_evidence", "needs_review"]
    reason: str


class Curation(InputModel):
    schema_version: Literal["1"] = "1"
    reviewed_at: str
    method: str = "Agent review of primary abstracts/full text; not automatic extraction."
    candidates: list[Candidate]
    papers_reviewed: list[PaperReview]
    limitations: list[str]


def normalized_measurement(measurement: Measurement):
    row = measurement.model_dump()
    factors = {
        "M": "1",
        "mM": "0.001",
        "uM": "0.000001",
        "µM": "0.000001",
        "μM": "0.000001",
        "nM": "0.000000001",
        "pM": "0.000000000001",
    }
    if measurement.unit in factors:
        factor = Decimal(factors[measurement.unit])
        row["value_molar"] = float(Decimal(str(measurement.value)) * factor)
        row["error_molar"] = (
            float(Decimal(str(measurement.error)) * factor)
            if measurement.error is not None
            else None
        )
    return row


def reference_index(dossier):
    index = set()
    for p in dossier["papers"]:
        if p["source"] == "MED":
            index.add(("europepmc", p["id"]))
    for pmcid, passages in dossier["fulltexts"].items():
        if passages:
            index.add(("europepmc", pmcid))
    for section, field in [
        ("assay", "assay_chembl_id"),
        ("document", "document_chembl_id"),
        ("molecule", "molecule_chembl_id"),
    ]:
        for row in dossier["chembl_details"][section]:
            index.add(("chembl", row[field]))
    for row in [*dossier["activities"], *dossier.get("assay_mention_activities", [])]:
        index.add(("chembl", str(row["record"]["activity_id"])))
    for row in dossier["structures"]:
        index.add(("rcsb", row["rcsb_id"]))
    for row in dossier["ccd_ligands"]:
        index.add(("rcsb", row["chem_comp"]["id"]))
    for row in dossier.get("pubchem", []):
        index.add(("pubchem", str(row["CID"])))
    for row in dossier["bindingdb"]:
        index.add(("bindingdb", bindingdb_evidence_id(row["record"])))
    # Optional agent follow-ups retain the same QueryResult contract.
    for result in dossier.get("followups", []):
        if result["source"] == "pubchem":
            for row in result["records"]:
                if "CID" in row:
                    index.add(("pubchem", str(row["CID"])))
    return index


def bindingdb_evidence_id(record):
    """REST has no unique measurement ID; label this explicitly as a local row hash."""
    return "row-sha256:" + hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def integrate_review(dossier, curation: Curation):
    """Link reviewed assertions to fetched records, without pretending to validate their science."""
    index = reference_index(dossier)
    targets = {t["accession"] for t in dossier["targets"]}
    candidates, conflicts, identities, ids = [], [], defaultdict(list), set()
    for candidate in curation.candidates:
        if candidate.candidate_id in ids:
            raise ValueError("Duplicate candidate_id; use DOI-scoped labels")
        ids.add(candidate.candidate_id)
        if not set(candidate.target_accessions) <= targets:
            raise ValueError(f"{candidate.label}: target is outside the resolved search scope")
        refs = [*candidate.references, *(m.reference for m in candidate.measurements)]
        for ref in refs:
            if (ref.source, ref.record_id) not in index:
                raise ValueError(f"Unfetched evidence: {ref.source}:{ref.record_id}")
            if ref.source == "europepmc" and ref.record_id.startswith("PMC"):
                passages = dossier["fulltexts"][ref.record_id]
                if not any(ref.locator in {p["block_id"], p["locator"]} for p in passages):
                    raise ValueError(f"Unknown full-text locator: {ref.record_id}:{ref.locator}")
        row = candidate.model_dump()
        row["measurements"] = [normalized_measurement(m) for m in candidate.measurements]
        groups = defaultdict(list)
        for m in row["measurements"]:
            if m["comparison_group"] and m["relation"] == "=" and m.get("value_molar", 0) > 0:
                groups[(m["comparison_group"], m["endpoint"])].append(m)
        for (group, endpoint), ms in groups.items():
            values = [m["value_molar"] for m in ms]
            if len(values) > 1 and max(values) / min(values) >= 100:
                conflicts.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "endpoint": endpoint,
                        "comparison_group": group,
                        "fold_difference": max(values) / min(values),
                        "status": "unresolved",
                        "action": "Retain both; review source units/assay.",
                        "measurements": ms,
                    }
                )
        for identity in candidate.identities:
            key = identity.get("inchikey")
            if key and identity.get("status") == "verified":
                identities[key].append(candidate.candidate_id)
        candidates.append(row)
    reviewed = {p.pmid for p in curation.papers_reviewed}
    if len(reviewed) != len(curation.papers_reviewed):
        raise ValueError("Each paper must have one review decision")
    for pmid in reviewed:
        if ("europepmc", pmid) not in index:
            raise ValueError(f"Unfetched reviewed paper: {pmid}")
    remaining = [
        p for p in dossier["review_queue"] if p["source"] != "MED" or p["id"] not in reviewed
    ]
    return {
        "schema_version": "1",
        "reviewed_at": curation.reviewed_at,
        "method": curation.method,
        "candidates": candidates,
        "conflicts": conflicts,
        "identity_groups": dict(identities),
        "papers_reviewed": [p.model_dump() for p in curation.papers_reviewed],
        "unreviewed_paper_count": len(remaining),
        "limitations": curation.limitations,
        "coverage": dossier["coverage"],
        "claim": "Reviewed candidates within declared coverage; not all known inhibitors.",
    }
