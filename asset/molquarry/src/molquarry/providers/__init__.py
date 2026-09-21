from urllib.parse import urlparse

from ..access_config import ACCESS, ROOTS
from .alphafold import AlphaFold
from .authenticated import Chemspace, CompTox, Enamine, Lens, MolPort
from .base import Provider
from .biology import HPA, KLIFS, STRING, ClinPGx, GPCRdb, GTEx, Reactome
from .chembl import ChEMBL
from .chemistry import COCONUT, LOTUS, BindingDB, ChEBI, GtoPdb, SureChEMBL, UniChem
from .clinicaltrials import ClinicalTrials
from .datasets import ORD, PLINDER, TDC, DepMap
from .europepmc import EuropePMC
from .fda import DrugsFDA, OrangeBook
from .files import BulkRoot, with_access
from .mcule import Mcule
from .opentargets import OpenTargets
from .patents import EPO, USPTO, GooglePatents
from .pubchem import PubChem
from .rcsb import RCSB
from .uniprot import UniProt

PROVIDERS = {
    provider.id: provider
    for provider in (
        PubChem,
        ChEMBL,
        RCSB,
        UniProt,
        OpenTargets,
        AlphaFold,
        ClinicalTrials,
        Mcule,
        BindingDB,
        ChEBI,
        COCONUT,
        GtoPdb,
        LOTUS,
        SureChEMBL,
        UniChem,
        ClinPGx,
        GPCRdb,
        GTEx,
        HPA,
        KLIFS,
        Reactome,
        STRING,
        Chemspace,
        CompTox,
        Enamine,
        Lens,
        MolPort,
        EPO,
        GooglePatents,
        USPTO,
        DepMap,
        ORD,
        PLINDER,
        TDC,
        DrugsFDA,
        OrangeBook,
        EuropePMC,
    )
}

for _id in ACCESS:
    if _id not in PROVIDERS:
        PROVIDERS[_id] = type(_id.title(), (Provider,), {"id": _id})

PROVIDERS = {
    key: with_access(
        cls,
        {
            name: BulkRoot(url, frozenset([urlparse(url).hostname, *extra]), directory)
            for name, (url, directory, extra) in ROOTS.get(key, {}).items()
        },
    )
    for key, cls in PROVIDERS.items()
}
