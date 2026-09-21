"""Source-specific official access surfaces. No inferred commercial data rights."""

# integration, documentation, licensing/rights note, credential environment names.
ACCESS = {
    "bindingdb": (
        "api",
        "https://www.bindingdb.org/rwd/bind/BindingDBRESTfulAPI.jsp",
        "Keep citations, assay context and upstream data-source license terms.",
        (),
    ),
    "gtopdb": (
        "authenticated_api",
        "https://www.guidetopharmacology.org/webServices.jsp",
        (
            "ODbL database / CC BY-SA contents; current registration and commercial access "
            "policy applies."
        ),
        ("GTOPDB_API_KEY",),
    ),
    "pdbbind": (
        "manual",
        "https://www.pdbbind-plus.org.cn/",
        "Licensed releases; use authorized local files. No public download API has been verified.",
        (),
    ),
    "plinder": (
        "bulk",
        "https://plinder-org.github.io/plinder/tutorial/dataset.html",
        (
            "Record release/iteration; inspect release-specific data licenses and known affinity "
            "integration issues."
        ),
        (),
    ),
    "biolip": (
        "bulk",
        "https://zhanggroup.org/BioLiP/download.html",
        (
            "Keep BioLiP/PDB references and release-specific terms; annotation files differ from "
            "experimental affinity."
        ),
        (),
    ),
    "klifs": (
        "api",
        "https://klifs.net/swagger/",
        "Retain KLIFS and original structure attribution.",
        (),
    ),
    "gpcrdb": (
        "api",
        "https://docs.gpcrdb.org/web_services.html",
        "Use GPCRdb citation and source terms; retain numbering and structure provenance.",
        (),
    ),
    "drugcentral": (
        "bulk",
        "https://drugcentral.org/download",
        (
            "Files have different release dates; retain each artifact's original license and "
            "provenance."
        ),
        (),
    ),
    "ttd": (
        "manual",
        "https://ttd.idrblab.cn/",
        (
            "Current site is a JavaScript application; no documented public query API verified. "
            "Authorized local releases supported."
        ),
        (),
    ),
    "depmap": (
        "bulk",
        "https://depmap.org/portal/data_page/",
        "Figshare release licenses vary; use the license on the selected article/version.",
        (),
    ),
    "chebi": (
        "api",
        "https://www.ebi.ac.uk/chebi/backend/api/docs/",
        "Retain ChEBI attribution; ontology identity is not a molecule standardization policy.",
        (),
    ),
    "unichem": (
        "api",
        "https://www.ebi.ac.uk/unichem/api/docs",
        "Mapping availability does not replace underlying source licenses.",
        (),
    ),
    "zinc": (
        "bulk",
        "https://files.docking.org/zinc22/",
        "Archive/tranche availability is not live supplier stock.",
        (),
    ),
    "coconut": (
        "api",
        "https://coconut.naturalproducts.net/api-documentation",
        (
            "COCONUT places no extra restrictions beyond original data owners; software MIT is "
            "not a blanket data license."
        ),
        (),
    ),
    "lotus": (
        "api",
        "https://lotus.naturalproducts.net/documentation",
        "Retain LOTUS citation and occurrence evidence; inspect original dataset licenses.",
        (),
    ),
    "drugsfda": (
        "api",
        "https://open.fda.gov/apis/drug/drugsfda/",
        "FDA data; preserve application/product identity and release date.",
        (),
    ),
    "orangebook": (
        "api",
        "https://open.fda.gov/apis/drug/orange-book/",
        "US product-listed patents/exclusivities only; not a general patent or FTO database.",
        (),
    ),
    "drugbank": (
        "manual",
        "https://go.drugbank.com/releases/latest",
        (
            "Programmatic/full data access requires an appropriate DrugBank license. Authorized "
            "local exports supported."
        ),
        (),
    ),
    "surechembl": (
        "api",
        "https://chembl.gitbook.io/surechembl/api/api-documentation",
        (
            "Patent chemistry extraction is incomplete and is not claim interpretation or an FTO "
            "verdict."
        ),
        (),
    ),
    "google_patents": (
        "configured_api",
        "https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query",
        "Requires GCP project, credentials and query budget; public corpus coverage varies.",
        ("GOOGLE_CLOUD_PROJECT", "GOOGLE_ACCESS_TOKEN"),
    ),
    "epo": (
        "authenticated_api",
        "https://www.epo.org/en/searching-for-patents/data/web-services/ops",
        "OPS account, fair use quota and license terms apply.",
        ("EPO_CONSUMER_KEY", "EPO_CONSUMER_SECRET"),
    ),
    "lens": (
        "authenticated_api",
        "https://docs.api.lens.org/",
        "Lens approved API access and allowed uses depend on account/license.",
        ("LENS_API_TOKEN",),
    ),
    "uspto": (
        "authenticated_api",
        "https://data.uspto.gov/apis/patent-file-wrapper/search",
        "ODP account/API key and current source quotas required.",
        ("USPTO_API_KEY",),
    ),
    "enamine": (
        "authenticated_api",
        "https://real.enamine.net/api/static/API_usage.html",
        (
            "REAL account and supplier terms apply; do not infer synthesis availability or "
            "redistribute licensed space."
        ),
        ("REAL_API_KEY",),
    ),
    "chemspace": (
        "authenticated_api",
        "https://api.chem-space.com/docs/",
        "Chemspace v5 account/API terms; offers are time- and region-specific.",
        ("CHEMSPACE_API_KEY",),
    ),
    "molport": (
        "authenticated_api",
        "https://api.molport.com/openapi",
        "Account API key; this adapter submits availability searches only, never orders.",
        ("MOLPORT_API_KEY",),
    ),
    "emolecules": (
        "manual",
        "https://www.emolecules.com/data-downloads",
        "Full ePLUS feed is licensed; free monthly download requires its stated access workflow.",
        (),
    ),
    "chemdiv": (
        "manual",
        "https://www.chemdiv.com/catalog/screening-libraries/",
        (
            "Supplier library exports are not an unrestricted full-catalog feed. Authorized "
            "local exports supported."
        ),
        (),
    ),
    "lifechemicals": (
        "bulk",
        "https://lifechemicals.com/downloads",
        "Library exports are governed by supplier terms; prices/stock require a current quote.",
        (),
    ),
    "mce": (
        "manual",
        "https://www.medchemexpress.com/screening/Bioactive_Compound_Library.html",
        (
            "Download controls use the vendor workflow; no stable public full-catalog API "
            "verified. Import authorized SDF/CSV exports."
        ),
        (),
    ),
    "targetmol": (
        "manual",
        "https://www.targetmol.com/",
        "Library exports/account feeds per supplier terms; import authorized SDF/CSV exports.",
        (),
    ),
    "wuxi": (
        "manual",
        "https://chemistry.wuxiapptec.com/library",
        "GalaXi is a project/service chemical space; project delivery files supported locally.",
        (),
    ),
    "aladdin": (
        "manual",
        "https://www.aladdinsci.com/",
        (
            "Selected-set exports are not a public full-catalog license; import authorized "
            "TSV/CSV/SDF."
        ),
        (),
    ),
    "bld": (
        "manual",
        "https://www.bldpharm.com/",
        (
            "Supplier feed requires arrangement; import an authorized catalog without scraping "
            "product pages."
        ),
        (),
    ),
    "hpa": (
        "api",
        "https://www.proteinatlas.org/about/help/dataaccess",
        "Keep HPA attribution and assay/tissue context.",
        (),
    ),
    "gtex": (
        "api",
        "https://gtexportal.org/api/v2/redoc",
        "Only public aggregate data; donor/genotype restricted data are outside this adapter.",
        (),
    ),
    "string": (
        "api",
        "https://string-db.org/help/api/",
        (
            "Retain STRING attribution/version; network associations are not all direct physical "
            "interactions."
        ),
        (),
    ),
    "reactome": (
        "api",
        "https://reactome.org/ContentService/",
        "Retain Reactome license and attribution.",
        (),
    ),
    "comptox": (
        "authenticated_api",
        "https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis",
        (
            "Free CTX API key required; source assay and toxicity endpoint semantics must be "
            "preserved."
        ),
        ("COMPTOX_API_KEY",),
    ),
    "ord": (
        "bulk",
        "https://github.com/open-reaction-database/ord-data",
        "ORD data CC BY-SA 4.0; code Apache-2.0. Parquet contains serialized Protocol Buffers.",
        (),
    ),
    "clinpgx": (
        "api",
        "https://api.clinpgx.org/swagger/",
        (
            "ClinPGx terms and attribution/share-alike requirements apply; annotations are not "
            "patient-specific advice."
        ),
        (),
    ),
    "tdc": (
        "bulk",
        "https://tdcommons.ai/",
        (
            "Check each underlying dataset's license; TDC code license does not grant "
            "data/model-training rights."
        ),
        (),
    ),
}

# root URL, directory navigation allowed, additional official artifact hosts.
ROOTS = {
    "bindingdb": {
        "default": ("https://www.bindingdb.org/rwd/bind/chemsearch/marvin/Download.jsp", False, [])
    },
    "biolip": {"default": ("https://zhanggroup.org/BioLiP/download.html", False, [])},
    "drugcentral": {"default": ("https://drugcentral.org/download", False, ["unmtid-dbs.net"])},
    "chebi": {"default": ("https://ftp.ebi.ac.uk/pub/databases/chebi/", True, [])},
    "unichem": {"default": ("https://ftp.ebi.ac.uk/pub/databases/chembl/UniChem/", True, [])},
    "zinc": {"default": ("https://files.docking.org/zinc22/", True, [])},
    "coconut": {
        "default": (
            "https://coconut.naturalproducts.net/download",
            False,
            ["coconut.s3.uni-jena.de"],
        )
    },
    "surechembl": {
        "default": ("https://ftp.ebi.ac.uk/pub/databases/chembl/SureChEMBL/bulk_data/", True, [])
    },
    "lifechemicals": {"default": ("https://lifechemicals.com/downloads", False, [])},
    "hpa": {"default": ("https://www.proteinatlas.org/about/download", False, [])},
    "reactome": {"default": ("https://reactome.org/download/current/", True, [])},
    "chembl": {"default": ("https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/", True, [])},
    "pubchem": {"default": ("https://ftp.ncbi.nlm.nih.gov/pubchem/", True, [])},
    "uniprot": {"default": ("https://ftp.uniprot.org/pub/databases/uniprot/", True, [])},
    "opentargets": {
        "default": ("https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/", True, [])
    },
}
