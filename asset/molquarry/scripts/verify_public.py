"""Opt-in live semantic acceptance checks. No credentials, cached answers or mocked services.

Each public source has a scientific example or a real artifact check. Manual sources
receive a single public-page visit, which is explicitly NOT a database-query pass.
Authenticated sources are deferred, even if credentials exist in the environment.
"""

import argparse
import concurrent.futures
import csv
import gzip
import hashlib
import json
import math
import os
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from molquarry import MolQuarry, MolQuarryError
from molquarry.catalog import SOURCES
from molquarry.http import HttpClient
from molquarry.models import utcnow

IMATINIB_KEY = "KTUFNOKKBVMGRW-UHFFFAOYSA-N"
ASPIRIN_KEY = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
WEB_CASES = {
    "pdbbind": ("https://www.pdbbind-plus.org.cn/", ["pdbbind"]),
    "drugbank": ("https://go.drugbank.com/drugs/DB00619", ["imatinib"]),
    "ttd": ("https://ttd.idrblab.cn/", ["therapeutic", "target"]),
    "emolecules": ("https://www.emolecules.com/data-downloads", ["download"]),
    "chemdiv": ("https://www.chemdiv.com/catalog/screening-libraries/", ["screening"]),
    "mce": ("https://www.medchemexpress.com/Imatinib.html", ["imatinib", "152459-95-5"]),
    "targetmol": ("https://www.targetmol.com/compound/imatinib", ["imatinib", "152459-95-5"]),
    "wuxi": ("https://chemistry.wuxiapptec.com/library", ["galaxi"]),
    "aladdin": ("https://www.aladdinsci.com/", ["aladdin"]),
    "bld": ("https://www.bldpharm.com/", ["bld"]),
}


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class Check:
    def __init__(self, source, client, directory, args):
        self.source, self.client, self.directory, self.args = source, client, directory, args
        self.row = {
            "source": source,
            "checked_at": utcnow(),
            "integration": SOURCES[source].integration,
            "status": "running",
            "steps": [],
            "assertions": [],
            "facts": {},
        }

    def equal(self, label, actual, expected):
        self.row["assertions"].append(
            {"check": label, "expected": expected, "actual": actual, "passed": actual == expected}
        )
        if actual != expected:
            raise AssertionError(f"{label}: expected {expected!r}, received {actual!r}")

    def query(self, operation, **params):
        step = {"kind": "query", "operation": operation, "parameters": params, "status": "running"}
        self.row["steps"].append(step)
        result = self.client.query(self.source, operation, **params)
        if not result.provenance or any(p.cached for p in result.provenance):
            raise AssertionError("Acceptance requires uncached live provenance")
        self.directory.mkdir(parents=True, exist_ok=True)
        raw = self.directory / f"{len(self.row['steps']):02d}-{operation}.json"
        raw.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        step.update(
            status="received",
            returned=result.returned,
            total=result.total,
            provenance=[p.model_dump() for p in result.provenance],
            local_evidence_file=raw.name,
        )
        return result.records

    def download(self, operation, **params):
        step = {
            "kind": "download",
            "operation": operation,
            "parameters": params,
            "status": "running",
        }
        self.row["steps"].append(step)
        plan = self.client.plan_download(self.source, operation, **params)
        result = self.client.download(
            plan, output_dir=self.directory / "downloads", max_bytes=self.args.max_file_bytes
        )
        step.update(status="received", manifest=result.manifest)
        return Path(result.path)

    def artifact(self, *, query, path=""):
        files = self.query("files", query=query, path=path, limit=100)
        matching = [f for f in files if f["kind"] == "file"]
        self.equal("official listing contains the requested artifact", bool(matching), True)
        item = matching[0]
        return self.download("artifact", **{k: item[k] for k in ["root", "path", "url"]})

    def text_file(self, path):
        # Acceptance parsers bound expanded data, too. Never extract archive members to disk.
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as f:
            data = f.read(self.args.max_expanded_bytes + 1)
        if len(data) > self.args.max_expanded_bytes:
            raise AssertionError("Expanded sample exceeds acceptance budget")
        return data.decode("utf-8-sig")


def pubchem(c):
    r = c.query("properties", identifier="imatinib")[0]
    c.equal("imatinib CID", r["CID"], 5291)
    c.equal("imatinib full InChIKey", r["InChIKey"], IMATINIB_KEY)
    c.equal("imatinib free-base formula", r["MolecularFormula"], "C29H31N7O")
    c.row["facts"]["imatinib_inchikey"] = r["InChIKey"]
    path = c.download("sdf", cids=[5291])
    text = c.text_file(path)
    c.equal("SDF carries CID and full identity", "5291" in text and IMATINIB_KEY in text, True)


def chembl(c):
    r = c.query("molecule", chembl_id="CHEMBL941")[0]
    c.equal("ChEMBL imatinib name", r["pref_name"], "IMATINIB")
    key = r["molecule_structures"]["standard_inchi_key"]
    c.equal("ChEMBL imatinib identity", key, IMATINIB_KEY)
    c.row["facts"]["imatinib_inchikey"] = key
    rows = c.query("activities", molecule_chembl_id="CHEMBL941", standard_type="IC50", limit=3)
    c.equal(
        "activity filter returns the requested molecule/type",
        bool(rows)
        and all(
            x["molecule_chembl_id"] == "CHEMBL941" and x["standard_type"] == "IC50" for x in rows
        ),
        True,
    )
    c.equal(
        "assay/units/relation remain explicit",
        all(
            all(k in x for k in ("assay_chembl_id", "standard_units", "standard_relation"))
            for x in rows
        ),
        True,
    )


def rcsb(c):
    r = c.query("entry", pdb_id="1IEP")[0]
    c.equal("reference PDB entry", r["rcsb_id"], "1IEP")
    c.equal(
        "experimental ABL-STI571 complex",
        "STI-571" in r["struct"]["title"]
        and any(x["method"] == "X-RAY DIFFRACTION" for x in r["exptl"]),
        True,
    )
    ligand = c.query("ligand", ccd_id="STI")[0]
    keys = [d["descriptor"] for d in ligand["pdbx_chem_comp_descriptor"] if d["type"] == "InChIKey"]
    c.equal("CCD STI is imatinib", keys, [IMATINIB_KEY])
    c.row["facts"]["imatinib_inchikey"] = keys[0]
    path = c.download("structure", pdb_id="1IEP")
    text = c.text_file(path)
    c.equal(
        "mmCIF has entry, coordinates and STI ligand",
        text.startswith("data_1IEP") and "_atom_site." in text and "STI" in text,
        True,
    )


def uniprot(c):
    r = c.query("entry", accession="P00519")[0]
    c.equal("human ABL1 accession", r["primaryAccession"], "P00519")
    c.equal("human organism", r["organism"]["taxonId"], 9606)
    c.equal("ABL1 gene mapping", any(g["geneName"]["value"] == "ABL1" for g in r["genes"]), True)
    path = c.download("fasta", accession="P00519")
    lines = c.text_file(path).splitlines()
    c.equal("FASTA sequence equals live JSON sequence", "".join(lines[1:]), r["sequence"]["value"])
    # Keep long sequences in raw evidence, not in the public report.
    c.row["assertions"][-1].update(
        expected="same sequence as JSON", actual=f"{len(r['sequence']['value'])} residues"
    )


def opentargets(c):
    r = c.query("target", ensembl_id="ENSG00000097007")[0]
    c.equal("Ensembl to ABL1", r["approvedSymbol"], "ABL1")
    c.equal("tractability evidence present", bool(r["tractability"]), True)
    drugs = c.query("search", query="imatinib", limit=10)
    c.equal(
        "imatinib drug entity",
        any(x["entity"] == "drug" and x["id"] == "CHEMBL941" for x in drugs),
        True,
    )


def alphafold(c):
    rows = c.query("prediction", accession="P00519")
    model = next(x for x in rows if x["uniprotAccession"] == "P00519")
    c.equal("canonical ABL1 model", model["gene"], "ABL1")
    c.equal("pLDDT summary in [0,100]", 0 <= model["globalMetricValue"] <= 100, True)
    c.row["facts"].update(model=model["modelEntityId"], predicted_not_experimental=True)


def clinicaltrials(c):
    rows = c.query("search", query="imatinib", intervention="imatinib", limit=3)
    c.equal("drug-specific trials found", bool(rows), True)
    ids = []
    for r in rows:
        p = r["protocolSection"]
        ids.append(p["identificationModule"]["nctId"])
        names = [x["name"] for x in p["armsInterventionsModule"]["interventions"]]
        c.equal(
            "trial has imatinib or its Gleevec/Glivec brand name as an intervention",
            any(
                any(alias in n.casefold() for alias in ("imatinib", "gleevec", "glivec"))
                for n in names
            ),
            True,
        )
        c.row["facts"].setdefault("interventions", []).append({"nct_id": ids[-1], "names": names})
    c.row["facts"].update(nct_ids=ids, trial_presence_is_not_approval=True)


def drugsfda(c):
    rows = c.query("search", query='products.brand_name:"GLEEVEC"', limit=10)
    applications = {r["application_number"]: r for r in rows}
    c.equal(
        "Gleevec capsule and tablet applications",
        {"NDA021335", "NDA021588"} <= applications.keys(),
        True,
    )
    facts = []
    for application, expected_date in [("NDA021335", "20010510"), ("NDA021588", "20030418")]:
        r = applications[application]
        approved = [
            s
            for s in r["submissions"]
            if s["submission_type"] == "ORIG" and s["submission_status"] == "AP"
        ]
        c.equal(
            f"{application} original approval date",
            [s["submission_status_date"] for s in approved],
            [expected_date],
        )
        products = r["products"]
        c.equal(
            f"{application} ingredient identity",
            all(
                any(a["name"] == "IMATINIB MESYLATE" for a in p["active_ingredients"])
                for p in products
            ),
            True,
        )
        facts.append(
            {
                "application_number": application,
                "original_approval_date": expected_date,
                "products": [
                    {
                        k: p[k]
                        for k in ("product_number", "brand_name", "dosage_form", "marketing_status")
                    }
                    for p in products
                ],
            }
        )
    c.row["facts"]["gleevec_applications"] = facts


def orangebook(c):
    rows = c.query("search", query='products.application_number:"021335"', limit=10)
    c.equal(
        "original Gleevec capsule product dates",
        {r["approval_date"] for r in rows} == {"20010510"},
        True,
    )
    c.equal(
        "Orange Book product/application/ingredient mapping",
        bool(rows)
        and all(
            any(
                p["application_number"] == "021335"
                and p["brand_name"] == "GLEEVEC"
                and any(a["name"] == "IMATINIB MESYLATE" for a in p["active_ingredients"])
                for p in r["products"]
            )
            for r in rows
        ),
        True,
    )
    c.row["facts"].update(
        application_number="021335",
        approval_date=rows[0]["approval_date"],
        marketing_statuses=sorted({p["marketing_status"] for r in rows for p in r["products"]}),
    )


def bindingdb(c):
    rows = c.query("by_uniprot", uniprot="P00519", cutoff_nm=1.0, limit=3)
    c.equal(
        "ABL1 binding measurements", bool(rows) and all("ABL1" in x["query"] for x in rows), True
    )
    c.equal(
        "measurement type, inequality and references retained",
        all(
            x["affinity_type"] in {"Ki", "Kd", "IC50", "EC50"}
            and x["affinity"]
            and (x["doi"] or x["pmid"])
            for x in rows
        ),
        True,
    )
    c.row["facts"]["measurements"] = [
        {k: x[k] for k in ("monomerid", "query", "affinity_type", "affinity", "doi")} for x in rows
    ]


def chebi(c):
    r = c.query("compound", chebi_id="15365")[0]
    c.equal("aspirin ChEBI accession", r["chebi_accession"], "CHEBI:15365")
    c.equal("aspirin identity", r["default_structure"]["standard_inchi_key"], ASPIRIN_KEY)
    c.equal("curated name", r["name"], "acetylsalicylic acid")


def unichem(c):
    r = c.query("mapping", compound="CHEMBL941", source_id=1)[0]
    c.equal("imatinib cross-source identity", r["standardInchiKey"], IMATINIB_KEY)
    mapping = {(x["shortName"], x["compoundId"]) for x in r["sources"]}
    c.equal(
        "ChEMBL to PubChem/CCD mapping",
        {("chembl", "CHEMBL941"), ("pubchem", "5291"), ("rcsb_pdb", "STI")} <= mapping,
        True,
    )
    c.row["facts"]["imatinib_inchikey"] = r["standardInchiKey"]


def gpcrdb(c):
    r = c.query("protein", entry_name="adrb2_human")[0]
    c.equal("beta2 adrenoceptor accession", r["accession"], "P07550")
    c.equal("human ADRB2", r["species"] == "Homo sapiens" and "ADRB2" in r["genes"], True)
    residues = c.query("residues", entry_name="adrb2_human")
    c.equal("residue count matches sequence", len(residues), len(r["sequence"]))
    c.equal(
        "generic numbering is present", any(x.get("display_generic_number") for x in residues), True
    )


def klifs(c):
    kinases = c.query("kinases", name="ABL1", species="Human", limit=5)
    c.equal("human ABL1 KLIFS identifier", [x["kinase_ID"] for x in kinases], [392])
    rows = c.query("structures", kinase_id=kinases[0]["kinase_ID"], limit=3)
    c.equal(
        "ABL1 pockets have 85 aligned positions",
        bool(rows) and all(x["kinase_ID"] == 392 and len(x["pocket"]) == 85 for x in rows),
        True,
    )
    c.row["facts"]["pdb_ids"] = [x["pdb"] for x in rows]


def hpa(c):
    r = c.query("gene", ensembl_id="ENSG00000097007")[0]
    c.equal("ABL1 gene", r["Gene"], "ABL1")
    c.equal("ABL1 Ensembl", r["Ensembl"], "ENSG00000097007")
    c.equal("ABL1 protein reference", "P00519" in r["Uniprot"], True)
    c.equal("tissue expression annotation present", bool(r["RNA tissue distribution"]), True)


def gtex(c):
    genes = c.query("genes", query="ABL1", limit=5)
    gene = next(g for g in genes if g["geneSymbol"] == "ABL1")
    c.equal("versioned ABL1 Ensembl mapping", gene["gencodeId"].split(".")[0], "ENSG00000097007")
    rows = c.query("expression", gencode_id=gene["gencodeId"], limit=3)
    c.equal(
        "ABL1 expression with tissue, dataset and unit",
        bool(rows)
        and all(
            x["gencodeId"] == gene["gencodeId"]
            and x["unit"] == "TPM"
            and x["median"] >= 0
            and x["tissueSiteDetailId"]
            for x in rows
        ),
        True,
    )
    c.row["facts"]["expression"] = rows


def string(c):
    r = c.query("map_ids", identifiers=["ABL1"], limit=3)[0]
    c.equal("STRING human ABL1 mapping", (r["preferredName"], r["ncbiTaxonId"]), ("ABL1", 9606))
    rows = c.query("network", identifiers=["EGFR", "GRB2", "KRAS"], limit=10)
    c.equal(
        "expected EGFR-GRB2 functional association",
        any({x["preferredName_A"], x["preferredName_B"]} == {"EGFR", "GRB2"} for x in rows),
        True,
    )
    c.equal("association confidence bounds", all(0 <= x["score"] <= 1 for x in rows), True)


def reactome(c):
    rows = c.query("pathways_by_uniprot", identifier="P00519")
    c.equal(
        "human ABL1 pathway mappings",
        bool(rows)
        and all(
            x["speciesName"] == "Homo sapiens" and x["stId"].startswith("R-HSA-") for x in rows
        ),
        True,
    )
    c.row["facts"]["pathways"] = [{k: x[k] for k in ("stId", "displayName")} for x in rows[:3]]


def clinpgx(c):
    r = c.query("chemical", name="warfarin")[0]
    c.equal("warfarin PGx identifier", r["id"], "PA451906")
    c.equal("drug name", r["name"], "warfarin")
    gene = c.query("gene", symbol="CYP2C9")[0]
    c.equal("CYP2C9 gene resolution", gene["symbol"], "CYP2C9")


def surechembl(c):
    r = c.query("chemical_by_name", name="imatinib")[0]
    c.equal("patent chemistry resolves imatinib", r["inchi_key"], IMATINIB_KEY)
    c.row["facts"].update(imatinib_inchikey=r["inchi_key"], chemical_id=r["chemical_id"])
    structure = c.query("chemical_by_smiles", smiles=r["smiles"])[0]
    c.equal("name and structure lookup agree", structure["inchi_key"], r["inchi_key"])
    family = c.query("family", publication="US20160355508A1")
    # This tests publication normalization/family metadata, not a chemical-to-patent link.
    c.equal(
        "patent family has the queried normalized publication",
        "US-20160355508-A1" in json.dumps(family),
        True,
    )


def coconut(c):
    rows = c.query("search", query="caffeine", limit=5)
    r = next(x for x in rows if x["name"].casefold() == "caffeine")
    c.equal("caffeine natural-product identity", r["identifier"].split(".")[0], "CNP0228556")
    c.equal(
        "structure and occurrence evidence",
        bool(r["canonical_smiles"]) and r["organism_count"] > 0 and r["citation_count"] > 0,
        True,
    )


def lotus(c):
    rows = c.query("exact", smiles="O=C1OC(C(O)=C1O)CO", limit=10)
    c.equal(
        "erythroascorbic-acid connectivity",
        bool(rows) and all(x["inchikey"].split("-")[0] == "ZZZCUOFIHGPKAK" for x in rows),
        True,
    )
    c.row["facts"].update(
        inchikeys=[x["inchikey"] for x in rows],
        exact_search_may_include_different_stereochemistry=True,
    )


def mcule(c):
    rows = c.query("lookup", inchikey=ASPIRIN_KEY)
    c.equal("aspirin exact InChIKey lookup", bool(rows), True)
    identifier = rows[0]["mcule_id"]
    details = c.query("compound", mcule_id=identifier)[0]
    c.equal("lookup and detail refer to the same compound", details["mcule_id"], identifier)
    c.row["facts"].update(mcule_id=identifier, catalog_identity_only_no_live_stock=True)


def drugcentral(c):
    path = c.artifact(query="FDA_Approved.csv")
    rows = list(csv.reader(c.text_file(path).splitlines()))
    match = [r for r in rows if len(r) == 2 and r[1].casefold() == "imatinib"]
    c.equal("real approved-drug CSV contains imatinib", len(match), 1)
    c.row["facts"].update(drugcentral_id=match[0][0], compound_name=match[0][1], rows=len(rows))


def biolip(c):
    path = c.artifact(query="ligand.tsv.gz")
    rows = list(csv.reader(c.text_file(path).splitlines(), delimiter="\t"))
    matches = [r for r in rows if r and r[0] == "STI"]
    c.equal("BioLiP CCD ligand table includes STI", len(matches), 1)
    c.equal("BioLiP STI is imatinib", IMATINIB_KEY in matches[0], True)
    c.row["facts"].update(ligand_id="STI", ligand_identity_only_not_affinity=True, rows=len(rows))


def depmap(c):
    release = c.query("release", article_id=27993248)[0]
    c.equal("selected reproducible release", release["title"], "DepMap 24Q4 Public")
    entry = next(f for f in release["files"] if f["name"] == "AchillesCommonEssentialControls.csv")
    path = c.download("file", article_id=27993248, version=release["version"], file_id=entry["id"])
    rows = list(csv.DictReader(c.text_file(path).splitlines()))
    c.equal(
        "essential controls include AAMP (14)", any(x["Gene"] == "AAMP (14)" for x in rows), True
    )
    c.equal(
        "published Figshare MD5 matches",
        hashlib.md5(path.read_bytes()).hexdigest(),
        entry["computed_md5"],
    )
    c.row["facts"].update(rows=len(rows), release=release["title"], version=release["version"])


def tdc(c):
    r = c.query("dataset", name="caco2_wang")[0]
    c.equal("TDC resolves the Caco2 Wang original file", r["file_id"], 4259569)
    path = c.download("dataset", name="caco2_wang")
    rows = list(csv.DictReader(c.text_file(path).splitlines(), delimiter="\t"))
    c.equal(
        "ADME dataset has molecule identity, SMILES and numeric outcome",
        bool(rows)
        and all(x["Drug_ID"] and x["Drug"] and math.isfinite(float(x["Y"])) for x in rows),
        True,
    )
    c.equal(
        "known drug codeine is present",
        any(x["Drug_ID"].casefold() == "codeine" for x in rows),
        True,
    )
    c.row["facts"].update(rows=len(rows), task="Caco2_Wang", underlying_file_id=4259569)


def ord(c):
    rows = c.query("tree", path="data/00", limit=3)
    c.equal(
        "versioned ORD Parquet files exist", any(x["path"].endswith(".parquet") for x in rows), True
    )
    path = c.download(
        "dataset", path="data/00/ord_dataset-00005539a1e04c809a9a78647bea649c.parquet"
    )
    # Optional validation dependencies; they are not needed to use MolQuarry.
    import pyarrow.parquet as pq
    from ord_schema.proto import reaction_pb2

    table = pq.read_table(path)
    c.equal("Parquet contains reaction rows", table.num_rows > 0, True)
    c.row["facts"].update(rows=table.num_rows, columns=table.column_names)
    binary_columns = [f.name for f in table.schema if str(f.type) in {"binary", "large_binary"}]
    c.equal("serialized protobuf reaction column present", bool(binary_columns), True)
    reaction = reaction_pb2.Reaction()
    reaction.ParseFromString(table[binary_columns[0]][0].as_py())
    c.equal(
        "decoded reaction includes inputs and product outcomes",
        bool(reaction.inputs) and bool(reaction.outcomes),
        True,
    )
    c.row["facts"]["reaction_id"] = reaction.reaction_id


def zinc(c):
    rows = c.query("files", path="subsets/", limit=5)
    c.equal(
        "public subset directories exist",
        bool(rows) and all(x["kind"] == "directory" for x in rows),
        True,
    )
    c.row["facts"].update(
        example_paths=[r["path"] for r in rows], discovery_only_no_live_stock=True
    )
    c.row["status"] = "discovery_passed"


def lifechemicals(c):
    files = c.query("files", query="kinase", limit=10)
    c.equal(
        "actual kinase-library download links",
        bool(files) and all(x["kind"] == "file" and x["format"] == "zip" for x in files),
        True,
    )
    c.row["facts"]["files"] = [x["filename"] for x in files]
    path = c.artifact(query="LC_Aurora_A_Kinase_Targeted_Library.zip")
    import zipfile

    from rdkit import Chem

    with zipfile.ZipFile(path) as z:
        entries = [f for f in z.infolist() if f.filename.casefold().endswith(".sdf")]
        c.equal("vendor ZIP contains an SDF library", bool(entries), True)
        entry = entries[0]
        if entry.file_size > c.args.max_expanded_bytes:
            raise AssertionError("Expanded SDF exceeds acceptance budget")
        text = z.read(entry).decode("utf-8-sig")
    first = text.split("$$$$", 1)[0]
    molecule = Chem.MolFromMolBlock(first)
    c.equal(
        "downloaded library contains a parseable chemical structure",
        molecule is not None and molecule.GetNumAtoms() > 0,
        True,
    )
    c.row["facts"].update(
        sdf_records=text.count("$$$$"), first_molecule_smiles=Chem.MolToSmiles(molecule)
    )


def plinder(c):
    rows = c.query("objects", limit=3)
    c.equal("PLINDER index objects", bool(rows), True)
    c.row["status"] = "discovery_passed"


def europepmc(c):
    article = c.query("article", pmid="35121987")[0]
    c.equal("Exact primary PMID", article["id"], "35121987")
    c.equal("Primary DOI", article["doi"], "10.1038/s43018-021-00279-5")
    c.equal("Open full-text identifier", article["pmcid"], "PMC8818087")
    rows = c.query("search", query="EXT_ID:39792778 AND SRC:MED", limit=1)
    c.equal(
        "C19 abstract has compound and binding evidence",
        "C19" in rows[0]["abstractText"] and "279" in rows[0]["abstractText"],
        True,
    )
    rows = c.query("fulltext", pmcid="PMC8818087", contains="C26-A2", limit=100)
    c.equal(
        "C26 structure mechanism in primary full text",
        any("W401" in r["text"] and "C26-A2" in r["text"] for r in rows),
        True,
    )
    c.equal(
        "Full-text evidence has paragraph locators", all(bool(r["locator"]) for r in rows), True
    )


FUNCTIONS = {
    f.__name__: f
    for f in (
        pubchem,
        chembl,
        rcsb,
        uniprot,
        opentargets,
        alphafold,
        clinicaltrials,
        drugsfda,
        orangebook,
        bindingdb,
        chebi,
        unichem,
        gpcrdb,
        klifs,
        hpa,
        gtex,
        string,
        reactome,
        clinpgx,
        surechembl,
        coconut,
        lotus,
        mcule,
        drugcentral,
        biolip,
        depmap,
        tdc,
        ord,
        zinc,
        lifechemicals,
        plinder,
        europepmc,
    )
}


def website(c):
    url, terms = WEB_CASES[c.source]
    step = {"kind": "website", "url": url, "status": "running"}
    c.row["steps"].append(step)
    response = c.client.http.text(
        SOURCES[c.source],
        "GET",
        url,
        redirect_hosts=frozenset({urlparse(url).hostname}),
    )
    parser = VisibleText()
    parser.feed(response.data)
    visible = " ".join(parser.parts).casefold()
    step.update(status="received", provenance=[response.provenance.model_dump()])
    c.row["facts"].update(
        expected_visible_terms={term: term in visible for term in terms},
        database_query_tested=False,
        reason=(
            "One public page visited; no supported public query adapter or authorized file "
            "available."
        ),
    )
    c.row["status"] = "website_only" if all(t in visible for t in terms) else "website_unverified"


def run_source(source, root, args):
    spec = SOURCES[source]
    if spec.integration in {"authenticated_api", "configured_api"} or spec.credential_env:
        return {
            "source": source,
            "integration": spec.integration,
            "status": "deferred_credentials",
            "reason": "Per user scope: test only account-free access; no authenticated calls made.",
        }
    with MolQuarry(cache=False, http=HttpClient(timeout=40, max_attempts=1)) as q:
        c = Check(source, q, root / source, args)
        try:
            if source in WEB_CASES:
                website(c)
            else:
                FUNCTIONS[source](c)
                if c.row["status"] == "running":
                    c.row["status"] = "semantic_passed"
        except MolQuarryError as exc:
            c.row.update(
                status="blocked"
                if (source in WEB_CASES and exc.details.get("status") == 412)
                or exc.code
                in {
                    "authentication_required",
                    "access_denied",
                    "rate_limited",
                    "network_error",
                    "download_interrupted",
                    "download_too_large",
                }
                else "failed",
                error=exc.as_dict()["error"],
            )
        except Exception as exc:
            c.row.update(status="failed", error={"code": type(exc).__name__, "message": str(exc)})
        for step in c.row["steps"]:
            if step["status"] == "running":
                step["status"] = c.row["status"]
        if (
            c.row["status"] == "blocked"
            and c.row["steps"][-1]["kind"] == "download"
            and any(s["status"] == "received" and s["kind"] == "query" for s in c.row["steps"])
        ):
            c.row["status"] = "partial"
            c.row["facts"]["validated_scope"] = "File discovery passed; sample download blocked."
        print(source, c.row["status"], c.row.get("error", {}).get("message", ""), flush=True)
        return c.row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", action="append", choices=sorted(SOURCES))
    p.add_argument("--output", type=Path, default=Path(".molquarry/public-verification.json"))
    p.add_argument("--workers", type=int, default=6, choices=range(1, 9))
    p.add_argument("--max-file-bytes", type=int, default=20_000_000)
    p.add_argument("--max-expanded-bytes", type=int, default=100_000_000)
    args = p.parse_args()
    # Guarantee anonymous testing for the optional GCS adapter as well.
    os.environ.pop("PLINDER_GCP_ACCESS_TOKEN", None)
    root = Path(".molquarry/acceptance") / uuid4().hex[:12]
    report = {
        "schema_version": "1",
        "started_at": utcnow(),
        "scope": "public_no_credentials",
        "raw_evidence_directory": str(root),
        "sources": [],
        "cross_checks": [],
    }
    sources = sorted(set(args.source or SOURCES))
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report["updated_at"] = utcnow()
        report["summary"] = dict(Counter(x["status"] for x in report["sources"]))
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_source, s, root, args) for s in sources]
        for future in concurrent.futures.as_completed(futures):
            report["sources"].append(future.result())
            report["sources"].sort(key=lambda x: x["source"])
            save()
    keyed = {r["source"]: r for r in report["sources"]}
    names = ["pubchem", "chembl", "unichem", "surechembl", "rcsb"]
    if all(s in keyed for s in names):
        keys = {s: keyed[s].get("facts", {}).get("imatinib_inchikey") for s in names}
        report["cross_checks"].append(
            {
                "check": "imatinib full InChIKey agrees across five sources",
                "actual": keys,
                "passed": all(v == IMATINIB_KEY for v in keys.values()),
            }
        )
    if all(s in keyed for s in ["drugsfda", "orangebook"]):
        fda = keyed["drugsfda"].get("facts", {}).get("gleevec_applications", [])
        date = next(
            (r["original_approval_date"] for r in fda if r["application_number"] == "NDA021335"),
            None,
        )
        ob_date = keyed["orangebook"].get("facts", {}).get("approval_date")
        report["cross_checks"].append(
            {
                "check": "FDA/Orange Book original approval date agrees",
                "actual": [date, ob_date],
                "passed": date == ob_date == "20010510",
            }
        )
    save()
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    # A 403/401 is evidence of a blocked check, never a successful semantic test.
    # Partial accessibility is reported in JSON; assertion/schema failures fail the command.
    return int(
        any(r["status"] == "failed" for r in report["sources"])
        or any(not r["passed"] for r in report["cross_checks"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
