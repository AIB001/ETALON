"""Collect a target dossier; experimental interpretation remains an explicit review step."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from ..errors import MolQuarryError
from ..models import InputModel, utcnow
from ..providers.uniprot import ACCESSION

TargetName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")]
PMID = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,9}$")]


class InhibitorSearch(InputModel):
    targets: list[TargetName] = Field(min_length=1, max_length=5)
    taxon: int = Field(default=9606, ge=1)
    mode: Literal["inhibitor", "agonist", "modulator", "ligand"] = "inhibitor"
    literature_until: str = Field(default_factory=lambda: date.today().isoformat())
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    max_pages: int = Field(default=10, ge=1, le=100)
    max_details: int = Field(default=60, ge=0, le=5000)
    review_pmids: list[PMID] = Field(default_factory=list, max_length=100)
    max_fulltexts: int = Field(default=8, ge=0, le=100)
    search_assay_descriptions: bool = True
    pubchem_ccd_xrefs: bool = True
    bindingdb_cutoff_nm: int = Field(default=10_000_000, ge=1, le=10_000_000)

    @field_validator("literature_until")
    @classmethod
    def check_date(cls, value):
        return date.fromisoformat(value).isoformat()

    @model_validator(mode="after")
    def check_aliases(self):
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("Provide each target once")
        if set(self.aliases) - set(self.targets):
            raise ValueError("Alias keys must identify an input target")
        for terms in self.aliases.values():
            if len(terms) > 20 or any(not t.strip() or len(t) > 100 for t in terms):
                raise ValueError("Each target supports up to 20 nonempty aliases of <=100 chars")
        return self


def literal(term):
    """A user-supplied alias must remain a phrase, not become query operators."""
    return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'


def literature_queries(targets, until, mode="inhibitor"):
    terms = {}
    for target in targets:
        aliases = list(dict.fromkeys([target["input"], target["gene"], *target["aliases"]]))
        terms[target["input"]] = (
            "(" + " OR ".join("TITLE_ABS:" + literal(a) for a in aliases if a) + ")"
        )
    cutoff = f"FIRST_PDATE:[1900-01-01 TO {until}]"
    if mode == "ligand":
        # Broad ligand discovery must not exclude binding/probe papers lacking inhibition terms.
        result = {key: f"{term} AND {cutoff}" for key, term in terms.items()}
        if len(terms) > 1:
            result["interaction"] = " AND ".join([*terms.values(), cutoff])
        return result
    vocab = {
        "inhibitor": ["inhibit*", "disrupt*", "antagonist*"],
        "agonist": ["agonist*", "activat*", '"positive allosteric modulator"'],
        "modulator": ["inhibit*", "disrupt*", "antagonist*", "agonist*", "activat*", "modulat*"],
    }
    inhibition = "(" + " OR ".join("TITLE_ABS:" + word for word in vocab[mode]) + ")"
    result = {key: f"{term} AND {inhibition} AND {cutoff}" for key, term in terms.items()}
    if len(terms) > 1:
        # Co-mention search adds PPI/complex evidence without assuming all hits are PPI inhibitors.
        result["interaction"] = " AND ".join([*terms.values(), inhibition, cutoff])
    return result


class Collector:
    def __init__(self, quarry, output, max_pages, progress=None):
        self.quarry, self.output, self.max_pages = quarry, output, max_pages
        self.progress = progress or (lambda _message: None)
        self.coverage = []
        self.artifacts = []
        (output / "raw").mkdir(parents=True, exist_ok=False)

    def save(self, relative, data):
        content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        with (self.output / relative).open("xb") as f:
            f.write(content)
        self.artifacts.append(
            {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        )

    def query(self, key, source, operation, **parameters):
        status = {
            "key": key,
            "source": source,
            "operation": operation,
            "parameters": parameters,
            "status": "running",
            "pages": 0,
            "returned": 0,
            "total": None,
            "files": [],
        }
        self.coverage.append(status)
        rows = []
        self.progress(f"{key}: {source}.{operation}")
        try:
            for page in self.quarry.iter_pages(
                source, operation, max_pages=self.max_pages, **parameters
            ):
                path = f"raw/{len(self.coverage):04d}-{status['pages'] + 1:03d}.json"
                self.save(path, page.model_dump())
                rows.extend(page.records)
                status.update(
                    pages=status["pages"] + 1,
                    returned=len(rows),
                    total=page.total,
                    next_parameters=page.next_parameters,
                )
                status["files"].append(path)
            status["status"] = "truncated" if status.get("next_parameters") else "exhausted"
            if not rows:
                status["status"] = "empty"
            elif (
                status["status"] == "exhausted"
                and status["total"] is not None
                and len(rows) < status["total"]
            ):
                status["status"] = "incomplete"
        except MolQuarryError as exc:
            status.update(status="partial_error" if rows else "error", error=exc.as_dict()["error"])
        return rows

    def limited(self, key, items, maximum):
        self.coverage.append(
            {
                "key": key,
                "status": "limited" if len(items) > maximum else "selected",
                "available": len(items),
                "selected": min(len(items), maximum),
                "remaining": max(0, len(items) - maximum),
            }
        )
        return items[:maximum]


def resolve_targets(collector, config):
    resolved, decisions = [], []
    for name in config.targets:
        if re.fullmatch(ACCESSION, name):
            records = collector.query(f"resolve:{name}", "uniprot", "entry", accession=name)
        else:
            records = collector.query(
                f"resolve:{name}",
                "uniprot",
                "search",
                query=(
                    f"gene_exact:{literal(name)} AND organism_id:{config.taxon} AND reviewed:true"
                ),
                limit=100,
            )
        matches = [r for r in records if r.get("organism", {}).get("taxonId") == config.taxon]
        candidates = [r["primaryAccession"] for r in matches]
        # A partial search can never establish an unambiguous mapping.
        complete = collector.coverage[-1]["status"] == "exhausted"
        if len(matches) != 1 or not complete:
            decisions.append(
                {
                    "input": name,
                    "status": "unresolved",
                    "accessions": candidates,
                    "action": "Specify a UniProt accession/taxon; do not pick first hit.",
                }
            )
            continue
        record = matches[0]
        genes = record.get("genes", [])
        gene = genes[0].get("geneName", {}).get("value", name) if genes else name
        aliases = [s["value"] for g in genes for s in g.get("synonyms", [])]
        description = record.get("proteinDescription", {})
        for protein in [
            description.get("recommendedName", {}),
            *description.get("alternativeNames", []),
        ]:
            if protein.get("fullName", {}).get("value"):
                aliases.append(protein["fullName"]["value"])
            aliases.extend(n["value"] for n in protein.get("shortNames", []))
        resolved.append(
            {
                "input": name,
                "accession": record["primaryAccession"],
                "gene": gene,
                "taxon": config.taxon,
                "aliases": sorted(set(aliases + config.aliases.get(name, []))),
                "sequence_sha256": hashlib.sha256(record["sequence"]["value"].encode()).hexdigest(),
                "sequence": record["sequence"]["value"],
            }
        )
        decisions.append({"input": name, "status": "resolved", "accessions": candidates})
    return resolved, decisions


def collect_inhibitor_evidence(quarry, config: InhibitorSearch, output_dir, *, progress=None):
    """Create a new auditable directory. No private sources or automatic inhibitor claims."""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    collector = Collector(quarry, output, config.max_pages, progress)
    collector.save("config.json", config.model_dump())
    targets, resolution = resolve_targets(collector, config)
    activities, target_rows, binding, structures, paper_map = [], [], [], {}, {}
    for target in targets:
        name, accession = target["input"], target["accession"]
        mapped = collector.query(
            f"chembl-targets:{name}", "chembl", "targets_by_uniprot", uniprot=accession, limit=100
        )
        for t in mapped:
            components = [c.get("accession") for c in t.get("target_components", [])]
            accepted = accession in components and t.get("tax_id") == config.taxon
            target_rows.append({"input_target": name, "accepted": accepted, "record": t})
            if not accepted:
                continue
            for row in collector.query(
                f"activities:{t['target_chembl_id']}",
                "chembl",
                "activities",
                target_chembl_id=t["target_chembl_id"],
                limit=100,
            ):
                activities.append(
                    {
                        "input_target": name,
                        "source": "chembl",
                        "record": row,
                        "review_status": "unreviewed_measurement",
                    }
                )
        for row in collector.query(
            f"bindingdb:{name}",
            "bindingdb",
            "by_uniprot",
            uniprot=accession,
            cutoff_nm=config.bindingdb_cutoff_nm,
            limit=100,
        ):
            binding.append(
                {
                    "input_target": name,
                    "source": "bindingdb",
                    "record": row,
                    "review_status": "unreviewed_measurement",
                }
            )
        for row in collector.query(
            f"pdb:{name}", "rcsb", "by_uniprot", uniprot=accession, limit=100
        ):
            structures.setdefault(row["identifier"], []).append(name)

    # PPI assays may be assigned to Unchecked instead of a UniProt-mapped protein.
    mention_assays, mention_activities = {}, []
    if config.search_assay_descriptions:
        for target in targets:
            for row in collector.query(
                f"assay-mentions:{target['input']}",
                "chembl",
                "search_assays",
                query=target["gene"],
                limit=100,
            ):
                mention_assays[row["assay_chembl_id"]] = row
        known = {a["record"]["assay_chembl_id"] for a in activities}
        extra = sorted(set(mention_assays) - known)
        for aid in collector.limited("assay-mention-activities", extra, config.max_details):
            for row in collector.query(
                f"assay-activities:{aid}", "chembl", "activities", assay_chembl_id=aid, limit=100
            ):
                mention_activities.append(
                    {"source": "chembl", "record": row, "review_status": "unreviewed_assay_mention"}
                )
    details = {}
    for op, field in [
        ("assay", "assay_chembl_id"),
        ("document", "document_chembl_id"),
        ("molecule", "molecule_chembl_id"),
    ]:
        identifiers = sorted(
            {
                a["record"][field]
                for a in [*activities, *mention_activities]
                if a["record"].get(field)
            }
        )
        details[op] = []
        selected = collector.limited(f"{op}-details", identifiers, config.max_details)
        for start in range(0, len(selected), 50):
            details[op].extend(
                collector.query(
                    f"{op}-batch:{start // 50 + 1}",
                    "chembl",
                    "batch_details",
                    entity=op,
                    chembl_ids=selected[start : start + 50],
                    limit=100,
                )
            )

    entries, polymers, nonpolymers, ligands = [], [], [], []
    entity_jobs, ccd_ids = [], set()
    for pdb in collector.limited("structure-details", sorted(structures), config.max_details):
        rows = collector.query(f"entry:{pdb}", "rcsb", "entry", pdb_id=pdb)
        entries.extend(rows)
        for row in rows:
            identifiers = row.get("rcsb_entry_container_identifiers", {})
            for op, field in [
                ("polymer_entity", "polymer_entity_ids"),
                ("nonpolymer_entity", "non_polymer_entity_ids"),
            ]:
                entity_jobs.extend((pdb, op, eid) for eid in identifiers.get(field, []))
    for pdb, op, eid in collector.limited("structure-entities", entity_jobs, config.max_details):
        rows = collector.query(f"{op}:{pdb}:{eid}", "rcsb", op, pdb_id=pdb, entity_id=eid)
        (polymers if op == "polymer_entity" else nonpolymers).extend(rows)
        for row in rows:
            ccd = row.get("pdbx_entity_nonpoly", {}).get("comp_id")
            if ccd:
                ccd_ids.add(ccd)
    for ccd in collector.limited("ccd-details", sorted(ccd_ids), config.max_details):
        ligands.extend(collector.query(f"ccd:{ccd}", "rcsb", "ligand", ccd_id=ccd))

    pubchem, identity_checks = [], []
    if config.pubchem_ccd_xrefs:
        for ligand in ligands:
            descriptor = ligand.get("rcsb_chem_comp_descriptor", {})
            for ref in ligand.get("rcsb_chem_comp_related", []):
                if ref["resource_name"] != "PubChem":
                    continue
                cid = ref["resource_accession_code"]
                rows = collector.query(
                    f"pubchem:{cid}", "pubchem", "properties", namespace="cid", identifier=cid
                )
                pubchem.extend(rows)
                identity_checks.append(
                    {
                        "ccd_id": ligand["chem_comp"]["id"],
                        "cid": cid,
                        "inchikey": descriptor.get("InChIKey"),
                        "status": "matched"
                        if rows
                        and descriptor.get("InChIKey")
                        and all(r.get("InChIKey") == descriptor["InChIKey"] for r in rows)
                        else "unresolved_or_mismatch",
                    }
                )

    for key, query in literature_queries(targets, config.literature_until, config.mode).items():
        for row in collector.query(
            f"literature:{key}", "europepmc", "search", query=query, limit=100
        ):
            paper_map[f"{row['source']}:{row['id']}"] = row
    # Seed papers are explicitly supplied for follow-up, not silently assumed to be search hits.
    for pmid in config.review_pmids:
        if f"MED:{pmid}" not in paper_map:
            for row in collector.query(f"review-paper:{pmid}", "europepmc", "article", pmid=pmid):
                paper_map[f"{row['source']}:{row['id']}"] = row
    fulltexts = {}
    readable = [p for p in config.review_pmids if paper_map.get(f"MED:{p}", {}).get("pmcid")]
    for pmid in collector.limited("review-fulltexts", readable, config.max_fulltexts):
        pmcid = paper_map[f"MED:{pmid}"]["pmcid"]
        fulltexts[pmcid] = collector.query(
            f"fulltext:{pmcid}", "europepmc", "fulltext", pmcid=pmcid, limit=100
        )

    incomplete = [
        c["key"]
        for c in collector.coverage
        if c["status"] in {"error", "partial_error", "truncated", "incomplete", "limited"}
    ]
    dossier = {
        "schema_version": "1",
        "collected_at": utcnow(),
        "config": config.model_dump(),
        "scope": {
            "access": "public_no_credentials",
            "literature_date_field": "FIRST_PDATE",
            "activity_and_structure_dates": "Current snapshots, not historical as-of queries.",
            "claim": "Evidence collection requiring review; not an exhaustive inhibitor census.",
            "not_searched": [
                "credentialed databases",
                "patent full-text/Markush",
                "unindexed literature",
                "licensed supplements",
                "vendor inventory",
                "GtoPdb bulk",
                "PubChem BioAssay target-wide screen",
                "local catalogs",
            ],
            "bindingdb_cutoff_nm": config.bindingdb_cutoff_nm,
        },
        "targets": targets,
        "target_resolution": resolution,
        "chembl_targets": target_rows,
        "activities": activities,
        "assay_mentions": list(mention_assays.values()),
        "assay_mention_activities": mention_activities,
        "bindingdb": binding,
        "chembl_details": details,
        "structure_hits": structures,
        "structures": entries,
        "polymer_entities": polymers,
        "nonpolymer_entities": nonpolymers,
        "ccd_ligands": ligands,
        "pubchem": pubchem,
        "identity_checks": identity_checks,
        "papers": list(paper_map.values()),
        "fulltexts": fulltexts,
        "coverage": collector.coverage,
        "incomplete_steps": incomplete,
        "review_queue": [
            {
                "source": p["source"],
                "id": p["id"],
                "title": p.get("title"),
                "status": "unreviewed",
                "pmcid": p.get("pmcid"),
                "publication_types": p.get("pubTypeList", {}),
                "comments_corrections": p.get("commentCorrectionList", {}),
            }
            for p in paper_map.values()
        ],
    }
    collector.save("dossier.json", dossier)
    counts = {
        "resolved_targets": len(targets),
        "chembl_activities": len(activities),
        "assay_mention_activities": len(mention_activities),
        "bindingdb_measurements": len(binding),
        "pdb_entries": len(structures),
        "unique_papers": len(paper_map),
        "fulltexts_with_passages": sum(bool(v) for v in fulltexts.values()),
    }
    result = {
        "ok": True,
        "status": "needs_target_resolution"
        if len(targets) != len(config.targets)
        else "partial"
        if incomplete
        else "collected_requires_review",
        "output_directory": str(output),
        "counts": counts,
        "incomplete_steps": incomplete,
        "automatic_inhibitor_claims": 0,
    }
    collector.save("summary.json", result)
    manifest = {
        "schema_version": "1",
        "created_at": utcnow(),
        "files": collector.artifacts,
        "note": "Raw QueryResult files retain request/response hashes, URLs, licenses and dates.",
    }
    with (output / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return result


# Preserve the first public API while exposing the broader agonist/modulator workflow.
ModulatorSearch = InhibitorSearch
collect_modulator_evidence = collect_inhibitor_evidence
