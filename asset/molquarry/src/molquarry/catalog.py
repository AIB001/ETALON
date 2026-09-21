"""Source responsibilities and access levels, including explicit manual-export boundaries."""

from pydantic import BaseModel, Field

from .access_config import ACCESS, ROOTS

CATEGORIES = {
    "chemical_identity": "Chemical identity, standardization and cross-references",
    "experimental_structure": "Experimental structures, ligands and binding pockets",
    "predicted_structure": "Predicted structures and confidence",
    "bioactivity": "Affinity, pharmacology and cellular activity",
    "target_biology": "Targets, omics, expression and pathways",
    "clinical_regulatory": "Approved drugs, clinical trials and repurposing",
    "patent": "Patent chemistry, full text, families and legal status",
    "procurement": "Supplier catalogs, inventory and make-on-demand chemistry",
    "safety_admet": "ADMET, toxicology and pharmacogenomics",
    "synthesis": "Reactions and synthesis data",
    "ml_benchmark": "Derived ML datasets and benchmarks",
    "literature": "Primary literature, abstracts and open full-text evidence",
}


class SourceSpec(BaseModel):
    id: str
    name: str
    categories: list[str]
    homepage: str
    docs_url: str
    license_url: str
    license_notes: str
    access: list[str]
    auth: str = "public"
    bulk_entrypoints: list[str] = Field(default_factory=list)
    docs_checked_at: str | None = "2026-09-19"
    min_interval_seconds: float = 0.5
    cache_ttl_seconds: float = 3600
    integration: str = "api"
    credential_env: list[str] = Field(default_factory=list)
    availability_note: str | None = None
    download_login_urls: list[str] = Field(default_factory=list)


SOURCES = {
    s.id: s
    for s in [
        SourceSpec(
            id="europepmc",
            name="Europe PMC / PubMed literature",
            categories=["literature"],
            homepage="https://europepmc.org/",
            docs_url="https://europepmc.org/RestfulWebService",
            license_url="https://europepmc.org/Copyright",
            license_notes="Public metadata API; abstracts and full texts have article-specific "
            "copyright/licensing. Full-text access is not blanket redistribution permission.",
            access=["rest", "open_access_xml"],
        ),
        SourceSpec(
            id="pubchem",
            name="PubChem Compound / BioAssay",
            categories=["chemical_identity", "bioactivity", "safety_admet"],
            homepage="https://pubchem.ncbi.nlm.nih.gov/",
            docs_url="https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest",
            license_url="https://pubchem.ncbi.nlm.nih.gov/docs/copyright",
            license_notes="Public access; review contributor terms and retain provenance.",
            access=["rest", "bulk"],
            min_interval_seconds=0.25,
            bulk_entrypoints=["https://ftp.ncbi.nlm.nih.gov/pubchem/"],
        ),
        SourceSpec(
            id="chembl",
            name="ChEMBL",
            categories=["bioactivity", "chemical_identity", "safety_admet"],
            homepage="https://www.ebi.ac.uk/chembl/",
            docs_url="https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services",
            license_url="https://chembl.gitbook.io/chembl-interface-documentation/about",
            license_notes=(
                "ChEMBL attribution/share-alike terms; code licenses do not replace data terms."
            ),
            access=["rest", "bulk"],
            bulk_entrypoints=["https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/"],
        ),
        SourceSpec(
            id="rcsb",
            name="RCSB PDB / wwPDB",
            categories=["experimental_structure"],
            homepage="https://www.rcsb.org/",
            docs_url="https://www.rcsb.org/docs/programmatic-access/web-apis-overview",
            license_url="https://www.wwpdb.org/about/usage-policies",
            license_notes="Archive usage policies and scientific citations apply.",
            access=["rest", "search_api", "graphql", "bulk"],
            bulk_entrypoints=[
                "https://www.rcsb.org/docs/programmatic-access/file-download-services"
            ],
        ),
        SourceSpec(
            id="uniprot",
            name="UniProt",
            categories=["target_biology"],
            homepage="https://www.uniprot.org/",
            docs_url="https://www.uniprot.org/help/api",
            license_url="https://www.uniprot.org/help/license",
            license_notes="CC BY 4.0; retain attribution.",
            access=["rest", "bulk"],
            bulk_entrypoints=["https://ftp.uniprot.org/pub/databases/uniprot/"],
        ),
        SourceSpec(
            id="opentargets",
            name="Open Targets Platform",
            categories=["target_biology"],
            homepage="https://platform.opentargets.org/",
            docs_url="https://platform-docs.opentargets.org/data-access/graphql-api",
            license_url="https://platform-docs.opentargets.org/licence",
            license_notes="Platform CC0 1.0; retain evidence provenance and licensing context.",
            access=["graphql", "parquet", "bigquery"],
            bulk_entrypoints=["https://platform-docs.opentargets.org/data-access/datasets"],
        ),
        SourceSpec(
            id="alphafold",
            name="AlphaFold DB",
            categories=["predicted_structure"],
            homepage="https://alphafold.ebi.ac.uk/",
            docs_url="https://alphafold.ebi.ac.uk/api-docs",
            license_url="https://alphafold.ebi.ac.uk/faq",
            license_notes="Structure data CC BY 4.0; cite the model and version.",
            access=["rest", "bulk"],
            bulk_entrypoints=["https://alphafold.ebi.ac.uk/download"],
        ),
        SourceSpec(
            id="clinicaltrials",
            name="ClinicalTrials.gov",
            categories=["clinical_regulatory"],
            homepage="https://clinicaltrials.gov/",
            docs_url="https://clinicaltrials.gov/data-api/api",
            license_url="https://clinicaltrials.gov/about-site/terms-conditions",
            license_notes="Public study records under source terms; retain NCT IDs and dates.",
            access=["rest", "export"],
            cache_ttl_seconds=900,
        ),
        SourceSpec(
            id="mcule",
            name="Mcule",
            categories=["procurement", "chemical_identity"],
            homepage="https://mcule.com/",
            docs_url="https://doc.mcule.com/api",
            license_url="https://mcule.com/terms/",
            license_notes="Public API/download access is not a blanket redistribution license. "
            "Private inventory and prices require a separately authorized account.",
            access=["rest", "bulk"],
            auth="public subset implemented; private endpoints need token",
            min_interval_seconds=6.1,
            cache_ttl_seconds=3600,
        ),
    ]
}

# Stable source identities from the initial research; access capabilities live in ACCESS.
SOURCE_INDEX = [
    (
        "bindingdb",
        "BindingDB",
        ["bioactivity"],
        "https://www.bindingdb.org/",
        "bulk TSV/SDF + REST",
    ),
    (
        "gtopdb",
        "GtoPdb",
        ["bioactivity", "clinical_regulatory"],
        "https://www.guidetopharmacology.org/",
        "keyed REST + bulk",
    ),
    (
        "pdbbind",
        "PDBbind+",
        ["experimental_structure", "bioactivity", "ml_benchmark"],
        "https://www.pdbbind-plus.org.cn/",
        "licensed data packages",
    ),
    (
        "plinder",
        "PLINDER",
        ["experimental_structure", "ml_benchmark"],
        "https://plinder-org.github.io/plinder/",
        "Python + GCS releases",
    ),
    (
        "biolip",
        "BioLiP2 / BioLiP3",
        ["experimental_structure", "bioactivity"],
        "https://zhanggroup.org/BioLiP/",
        "bulk annotations/coordinates",
    ),
    (
        "klifs",
        "KLIFS",
        ["experimental_structure"],
        "https://klifs.net/",
        "web service; verify endpoint",
    ),
    (
        "gpcrdb",
        "GPCRdb",
        ["experimental_structure", "bioactivity", "target_biology"],
        "https://gpcrdb.org/",
        "REST + exports",
    ),
    (
        "drugcentral",
        "DrugCentral",
        ["clinical_regulatory", "bioactivity"],
        "https://drugcentral.org/download",
        "SDF/TSV/PostgreSQL",
    ),
    (
        "ttd",
        "TTD",
        ["target_biology", "bioactivity"],
        "https://db.idrblab.net/ttd/",
        "selected downloads; license review",
    ),
    (
        "depmap",
        "DepMap / PRISM",
        ["target_biology", "bioactivity"],
        "https://depmap.org/portal/data_page/",
        "versioned release files",
    ),
    (
        "chebi",
        "ChEBI",
        ["chemical_identity"],
        "https://www.ebi.ac.uk/chebi/",
        "REST + ontology/bulk",
    ),
    (
        "unichem",
        "UniChem",
        ["chemical_identity"],
        "https://www.ebi.ac.uk/unichem/",
        "REST + cross-reference bulk",
    ),
    (
        "zinc",
        "ZINC22 / Cartblanche",
        ["chemical_identity", "procurement"],
        "https://cartblanche22.docking.org/",
        "tranche files",
    ),
    (
        "coconut",
        "COCONUT",
        ["chemical_identity"],
        "https://coconut.naturalproducts.net/",
        "REST + versioned bulk",
    ),
    (
        "lotus",
        "LOTUS",
        ["chemical_identity"],
        "https://lotus.naturalproducts.net/",
        "search + occurrence dumps",
    ),
    (
        "drugsfda",
        "Drugs@FDA / openFDA",
        ["clinical_regulatory"],
        "https://open.fda.gov/apis/drug/drugsfda/",
        "REST + ZIP tables",
    ),
    (
        "orangebook",
        "FDA Orange Book",
        ["clinical_regulatory", "patent"],
        "https://open.fda.gov/apis/drug/orange-book/",
        "REST + ZIP tables",
    ),
    (
        "drugbank",
        "DrugBank",
        ["clinical_regulatory", "bioactivity", "safety_admet"],
        "https://go.drugbank.com/",
        "licensed API/releases",
    ),
    (
        "surechembl",
        "SureChEMBL",
        ["patent", "chemical_identity"],
        "https://www.surechembl.org/",
        "API + Parquet bulk",
    ),
    (
        "google_patents",
        "Google Patents Public Datasets",
        ["patent"],
        "https://cloud.google.com/blog/products/gcp/google-patents-public-datasets-connecting-public-paid-and-private-patent-data",
        "BigQuery; project/billing configuration",
    ),
    ("epo", "EPO OPS", ["patent"], "https://developers.epo.org/", "credentialed REST/XML"),
    ("lens", "Lens", ["patent"], "https://docs.api.lens.org/", "approved API token"),
    ("uspto", "USPTO ODP", ["patent"], "https://data.uspto.gov/", "current API + bulk schemas"),
    (
        "enamine",
        "Enamine REAL",
        ["procurement", "synthesis"],
        "https://real.enamine.net/api/static/API_usage.html",
        "keyed search + licensed enumeration",
    ),
    (
        "chemspace",
        "Chemspace",
        ["procurement"],
        "https://chem-space.com/purchasing-saas/chemspace-api",
        "keyed search/offers",
    ),
    ("molport", "MolPort", ["procurement"], "https://api.molport.com/", "account/OpenAPI"),
    (
        "emolecules",
        "eMolecules",
        ["procurement"],
        "https://www.emolecules.com/data-downloads",
        "licensed catalog feed",
    ),
    ("chemdiv", "ChemDiv", ["procurement"], "https://www.chemdiv.com/", "library SDF"),
    (
        "lifechemicals",
        "Life Chemicals",
        ["procurement"],
        "https://lifechemicals.com/",
        "library SDF",
    ),
    (
        "mce",
        "MedChemExpress",
        ["procurement"],
        "https://www.medchemexpress.com/",
        "library SDF/Excel; no assumed full-site API",
    ),
    ("targetmol", "TargetMol", ["procurement"], "https://www.targetmol.com/", "library SDF/Excel"),
    (
        "wuxi",
        "WuXi GalaXi",
        ["procurement", "synthesis"],
        "https://chemistry.wuxiapptec.com/library",
        "project/service integration",
    ),
    ("aladdin", "Aladdin", ["procurement"], "https://www.aladdinsci.com/", "selected-set exports"),
    (
        "bld",
        "BLD Pharmatech",
        ["procurement"],
        "https://www.bldpharm.com/",
        "supplier feed negotiation",
    ),
    (
        "hpa",
        "Human Protein Atlas",
        ["target_biology"],
        "https://www.proteinatlas.org/about/download",
        "gene JSON + bulk",
    ),
    (
        "gtex",
        "GTEx",
        ["target_biology"],
        "https://gtexportal.org/",
        "open releases; controlled donor data separate",
    ),
    ("string", "STRING", ["target_biology"], "https://string-db.org/help/api/", "HTTP API + bulk"),
    (
        "reactome",
        "Reactome",
        ["target_biology"],
        "https://reactome.org/ContentService/",
        "content/analysis API + graph dumps",
    ),
    (
        "comptox",
        "CompTox / ToxCast / ToxValDB",
        ["safety_admet", "bioactivity"],
        "https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis",
        "CTX APIs/key + bulk",
    ),
    (
        "ord",
        "Open Reaction Database",
        ["synthesis"],
        "https://github.com/open-reaction-database/ord-data",
        "versioned data + ord-schema",
    ),
    (
        "clinpgx",
        "ClinPGx",
        ["safety_admet", "clinical_regulatory"],
        "https://api.clinpgx.org/",
        "REST + downloads",
    ),
    (
        "tdc",
        "Therapeutics Data Commons",
        ["ml_benchmark", "safety_admet", "synthesis"],
        "https://tdcommons.ai/",
        "Python downloader; underlying licenses",
    ),
]


for _id, _name, _categories, _url, _reference in SOURCE_INDEX:
    _level, _docs, _notes, _env = ACCESS[_id]
    SOURCES[_id] = SourceSpec(
        id=_id,
        name=_name,
        categories=_categories,
        homepage="https://ttd.idrblab.cn/" if _id == "ttd" else _url,
        docs_url=_docs,
        license_url=_docs,
        license_notes=_notes,
        integration=_level,
        credential_env=list(_env),
        auth="environment credentials required"
        if _env
        else "manual or licensed export"
        if _level == "manual"
        else "public",
        access=[_level],
        min_interval_seconds=1.6
        if _id == "chemspace"
        else 1.1
        if _id in {"gtopdb", "string", "epo", "lens", "uspto"}
        else 0.6,
    )

for _id, _roots in ROOTS.items():
    SOURCES[_id].bulk_entrypoints = [entry[0] for entry in _roots.values()]
    if "bulk" not in SOURCES[_id].access:
        SOURCES[_id].access.append("bulk")

SOURCES["plinder"].availability_note = (
    "2026-09-19: anonymous GCS listing returned HTTP 401. "
    "Adapter supports optional PLINDER_GCP_ACCESS_TOKEN; authenticated access is unverified."
)
SOURCES["plinder"].bulk_entrypoints = ["gs://plinder/"]
SOURCES["lifechemicals"].download_login_urls = ["https://shop.lifechemicals.com/login"]
SOURCES["lifechemicals"].availability_note = (
    "2026-09-19: public file discovery works; sampled Aurora A library ZIP redirects "
    "to the vendor login page. Anonymous catalog download is not verified."
)
SOURCES["zinc"].availability_note = (
    "2026-09-19: root/subsets listings are public; 2d/ and 2d-all/ return HTTP 401. "
    "Access must be checked per directory/artifact."
)
SOURCES["ord"].bulk_entrypoints = [
    "https://huggingface.co/datasets/open-reaction-database/ord-data"
]
SOURCES["tdc"].bulk_entrypoints = [
    "https://raw.githubusercontent.com/mims-harvard/TDC/main/tdc/metadata.py"
]
SOURCES["depmap"].bulk_entrypoints = ["https://depmap.figshare.com/"]


def catalog_entries():
    return sorted(
        [
            {
                **s.model_dump(),
                "implementation": "partial" if s.integration == "manual" else "implemented",
            }
            for s in SOURCES.values()
        ],
        key=lambda entry: entry["id"],
    )
