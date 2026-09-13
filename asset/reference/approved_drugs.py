"""A panel of approved oral small-molecule drugs, used to calibrate defaults.

Every hard reject in the shipped cascade is a claim: *molecules like this are
not worth looking at*.  The cheapest way to find out whether a claim like that
is true is to point it at molecules that are known to be worth looking at and
count how many it deletes.  A filter that removes a third of the drugs already
on the market is not selective, it is miscalibrated, and the only way to know
which one it is, is to measure.

So this panel exists to be a positive control, and it is chosen to make that
control honest:

* **Oral small molecules only.**  Injectables, biologics and topicals are held
  to different physicochemical standards, and including them would let a filter
  look good on molecules it was never meant to judge.
* **Chemotype spread over famous-drug spread.**  Beta-lactams, quinolones,
  azoles, steroids, statins, dihydropyridines, sartans, kinase inhibitors,
  opiates, nitroimidazoles, thiazolidinediones and nucleoside analogues each
  appear, because alert catalogues and rule sets fail per chemotype, not at
  random.  Twelve kinase inhibitors would hide that.
* **The edges are included on purpose.**  ``levothyroxine`` (MW 777, four
  iodines), ``montelukast`` (MW 586) and ``lapatinib`` (MW 581) sit outside a
  conventional oral window; ``metformin`` (MW 129) and ``allopurinol`` (MW 136)
  sit below it.  A default that drops these is making a defensible choice about
  scope.  A default that drops ``caffeine`` is making an arithmetic one.

Retention here is a *floor*, never a target.  These molecules survived years of
optimisation and clinical development, so a screening funnel that keeps all of
them is almost certainly keeping too much else besides.  What the panel can
tell you is the opposite thing, and it is the thing that matters early in a
cascade: which rejects are paid for in real chemistry.

Sourced from the SMILES published for each drug in DrugBank and PubChem, then
canonicalised by RDKit; ``test_approved_drugs.py`` re-derives the formula and
monoisotopic mass of every entry so a transcription slip cannot quietly change
what a calibration test measures.
"""

from __future__ import annotations

#: ``name -> SMILES``.  Ordered by therapeutic area so that a diff which drops
#: coverage of an area is visible as a block rather than a scattering of lines.
APPROVED_ORAL_DRUGS: dict[str, str] = {
    # -- analgesics, antipyretics and NSAIDs -----------------------------
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "acetaminophen": "CC(=O)Nc1ccc(O)cc1",
    "ibuprofen": "CC(C)Cc1ccc(C(C)C(=O)O)cc1",
    "naproxen": "COc1ccc2cc([C@H](C)C(=O)O)ccc2c1",
    "diclofenac": "O=C(O)Cc1ccccc1Nc1c(Cl)cccc1Cl",
    "celecoxib": "Cc1ccc(-c2cc(C(F)(F)F)nn2-c2ccc(S(N)(=O)=O)cc2)cc1",
    "morphine": "CN1CC[C@]23c4c5ccc(O)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5",
    "codeine": "CN1CC[C@]23c4c5ccc(OC)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5",
    # -- central nervous system ------------------------------------------
    "fluoxetine": "CNCCC(Oc1ccc(C(F)(F)F)cc1)c1ccccc1",
    "sertraline": "CN[C@H]1CC[C@@H](c2ccc(Cl)c(Cl)c2)c2ccccc21",
    "amitriptyline": "CN(C)CCC=C1c2ccccc2CCc2ccccc21",
    "diazepam": "CN1c2ccc(Cl)cc2C(c2ccccc2)=NCC1=O",
    "zolpidem": "Cc1ccc(-c2c(CC(=O)N(C)C)nc3ccc(C)cn23)cc1",
    "olanzapine": "Cc1cc2c(s1)Nc1ccccc1N=C2N1CCN(C)CC1",
    "quetiapine": "OCCOCCN1CCN(C2=Nc3ccccc3Sc3ccccc32)CC1",
    "risperidone": (
        "CC1=C(CCN2CCC(CC2)C2=NOC3=C2C=CC(F)=C3)C(=O)N2CCCCC2=N1"
    ),
    "haloperidol": "O=C(CCCN1CCC(O)(c2ccc(Cl)cc2)CC1)c1ccc(F)cc1",
    "donepezil": "COc1cc2c(cc1OC)C(=O)C(CC1CCN(Cc3ccccc3)CC1)C2",
    "carbamazepine": "NC(=O)N1c2ccccc2C=Cc2ccccc21",
    "lamotrigine": "Nc1nnc(-c2cccc(Cl)c2Cl)c(N)n1",
    "gabapentin": "NCC1(CC(=O)O)CCCCC1",
    "levetiracetam": "CCC(C(N)=O)N1CCCC1=O",
    "nicotine": "CN1CCC[C@H]1c1cccnc1",
    "caffeine": "Cn1c(=O)c2c(ncn2C)n(C)c1=O",
    "theophylline": "Cn1c(=O)c2[nH]cnc2n(C)c1=O",
    # -- cardiovascular and metabolic ------------------------------------
    "atorvastatin": (
        "CC(C)c1c(C(=O)Nc2ccccc2)c(-c2ccccc2)c(-c2ccc(F)cc2)"
        "n1CC[C@@H](O)C[C@@H](O)CC(=O)O"
    ),
    "simvastatin": (
        "CCC(C)(C)C(=O)O[C@H]1C[C@H](C)C=C2C=C[C@H](C)[C@H]"
        "(CC[C@@H]3C[C@@H](O)CC(=O)O3)[C@H]12"
    ),
    "amlodipine": "CCOC(=O)C1=C(COCCN)NC(C)=C(C(=O)OC)[C@@H]1c1ccccc1Cl",
    "losartan": "CCCCc1nc(Cl)c(CO)n1Cc1ccc(-c2ccccc2-c2nn[nH]n2)cc1",
    "valsartan": "CCCCC(=O)N(Cc1ccc(-c2ccccc2-c2nn[nH]n2)cc1)[C@@H](C(=O)O)C(C)C",
    "metoprolol": "COCCc1ccc(OCC(O)CNC(C)C)cc1",
    "propranolol": "CC(C)NCC(O)COc1cccc2ccccc12",
    "verapamil": "COc1ccc(CCN(C)CCCC(C#N)(C(C)C)c2ccc(OC)c(OC)c2)cc1OC",
    "diltiazem": "COc1ccc([C@@H]2Sc3ccccc3N(CCN(C)C)C(=O)[C@@H]2OC(C)=O)cc1",
    "clopidogrel": "COC(=O)[C@H](c1ccccc1Cl)N1CCc2sccc2C1",
    "warfarin": "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O",
    "furosemide": "NS(=O)(=O)c1cc(C(=O)O)c(NCc2ccco2)cc1Cl",
    "hydrochlorothiazide": "NS(=O)(=O)c1cc2c(cc1Cl)NCNS2(=O)=O",
    "montelukast": (
        "CC(C)(O)c1ccccc1CC[C@@H](SCC1(CC(=O)O)CC1)"
        "c1cccc(/C=C/c2ccc3ccc(Cl)cc3n2)c1"
    ),
    "sildenafil": (
        "CCCc1nn(C)c2c(=O)[nH]c(-c3cc(S(=O)(=O)N4CCN(C)CC4)ccc3OCC)nc12"
    ),
    "metformin": "CN(C)C(=N)NC(N)=N",
    "glipizide": "Cc1cnc(C(=O)NCCc2ccc(S(=O)(=O)NC(=O)NC3CCCCC3)cc2)cn1",
    "sitagliptin": "N[C@@H](Cc1cc(F)c(F)cc1F)CC(=O)N1CCn2c(nnc2C(F)(F)F)C1",
    "rosiglitazone": "CN(CCOc1ccc(CC2SC(=O)NC2=O)cc1)c1ccccn1",
    "levothyroxine": "N[C@@H](Cc1cc(I)c(Oc2cc(I)c(O)c(I)c2)c(I)c1)C(=O)O",
    "allopurinol": "O=c1[nH]cnc2[nH]ncc12",
    "salbutamol": "CC(C)(C)NC[C@H](O)c1ccc(O)c(CO)c1",
    # -- oncology ---------------------------------------------------------
    "imatinib": "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1",
    "gefitinib": "COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1",
    "erlotinib": "C#Cc1cccc(Nc2ncnc3cc(OCCOC)c(OCCOC)cc23)c1",
    "lapatinib": (
        "CS(=O)(=O)CCNCc1ccc(-c2ccc3ncnc(Nc4ccc(OCc5cccc(F)c5)c(Cl)c4)c3c2)o1"
    ),
    "sorafenib": (
        "CNC(=O)c1cc(Oc2ccc(NC(=O)Nc3ccc(Cl)c(C(F)(F)F)c3)cc2)ccn1"
    ),
    "sunitinib": (
        "CCN(CC)CCNC(=O)c1c(C)[nH]c(/C=C2\\C(=O)Nc3ccc(F)cc32)c1C"
    ),
    "ibrutinib": (
        "C=CC(=O)N1CCC[C@@H](n2nc(-c3ccc(Oc4ccccc4)cc3)c3c(N)ncnc32)C1"
    ),
    "palbociclib": (
        "CC(=O)c1c(C)c2cnc(Nc3ccc(N4CCNCC4)cn3)nc2n(C2CCCC2)c1=O"
    ),
    "olaparib": "O=C(c1ccc(CC2=NNC(=O)c3ccccc32)cc1F)N1CCN(C(=O)C2CC2)CC1",
    "methotrexate": (
        "CN(Cc1cnc2nc(N)nc(N)c2n1)c1ccc(C(=O)N[C@@H](CCC(=O)O)C(=O)O)cc1"
    ),
    "tamoxifen": r"CC/C(=C(\c1ccccc1)c1ccc(OCCN(C)C)cc1)c1ccccc1",
    "anastrozole": "CC(C)(C#N)c1cc(Cn2cncn2)cc(C(C)(C)C#N)c1",
    "capecitabine": (
        "CCCCCOC(=O)Nc1nc(=O)n([C@@H]2O[C@@H](C)[C@H](O)[C@H]2O)cc1F"
    ),
    # -- anti-infectives ---------------------------------------------------
    "amoxicillin": (
        "CC1(C)S[C@@H]2[C@H](NC(=O)[C@H](N)c3ccc(O)cc3)C(=O)N2[C@H]1C(=O)O"
    ),
    "ciprofloxacin": "O=C(O)c1cn(C2CC2)c2cc(N3CCNCC3)c(F)cc2c1=O",
    "levofloxacin": "C[C@H]1COc2c(N3CCN(C)CC3)c(F)cc3c(=O)c(C(=O)O)cn1c23",
    "trimethoprim": "COc1cc(Cc2cnc(N)nc2N)cc(OC)c1OC",
    "metronidazole": "Cc1ncc([N+](=O)[O-])n1CCO",
    "isoniazid": "NNC(=O)c1ccncc1",
    "linezolid": "CC(=O)NC[C@H]1CN(c2ccc(N3CCOCC3)c(F)c2)C(=O)O1",
    "fluconazole": "OC(Cn1cncn1)(Cn1cncn1)c1ccc(F)cc1F",
    "efavirenz": "O=C1O[C@@](C#CC2CC2)(C(F)(F)F)c2cc(Cl)ccc2N1",
    "oseltamivir": "CCOC(=O)C1=C[C@@H](OC(CC)CC)[C@H](NC(C)=O)[C@@H](N)C1",
    # -- gastrointestinal, allergy and endocrine ---------------------------
    "omeprazole": "COc1ccc2[nH]c(S(=O)Cc3ncc(C)c(OC)c3C)nc2c1",
    "ranitidine": "CNC(=C[N+](=O)[O-])NCCSCc1ccc(CN(C)C)o1",
    "famotidine": "NC(N)=Nc1nc(CSCCC(N)=NS(N)(=O)=O)cs1",
    "loratadine": "CCOC(=O)N1CCC(=C2c3ccc(Cl)cc3CCc3cccnc32)CC1",
    "cetirizine": "O=C(O)COCCN1CCN(C(c2ccccc2)c2ccc(Cl)cc2)CC1",
    "dexamethasone": (
        "C[C@@H]1C[C@H]2[C@@H]3CCC4=CC(=O)C=C[C@]4(C)[C@@]3(F)[C@@H](O)C"
        "[C@]2(C)[C@@]1(O)C(=O)CO"
    ),
    "prednisolone": (
        "C[C@]12CC(O)[C@H]3[C@@H](CCC4=CC(=O)C=C[C@]43C)[C@@H]1CC[C@]2(O)C(=O)CO"
    ),
}

#: The subset whose physicochemistry is knowingly outside a conventional oral
#: window.  A default that rejects these is making a scope decision and should
#: say so; a default that rejects anything *else* in the panel is a defect.
#: Kept separate from the panel so a test can state which of the two it means.
OUT_OF_WINDOW: frozenset[str] = frozenset(
    {
        # MW 777 with four iodines; a thyroid hormone, not a screening lead.
        "levothyroxine",
        # MW 586, cLogP ~8.8 -- famously beyond-Ro5 and famously hard to dose.
        "montelukast",
        # MW 581; the large end of the kinase inhibitors.
        "lapatinib",
        # MW 129 and MW 136: below the mass where a docking pose means much.
        "metformin",
        "allopurinol",
        # MW 137, and the hydrazide is a genuine reactive-chemistry alert.
        "isoniazid",
    }
)

#: Everything a screening cascade aimed at oral small molecules should keep.
IN_WINDOW: tuple[str, ...] = tuple(
    name for name in APPROVED_ORAL_DRUGS if name not in OUT_OF_WINDOW
)


__all__ = ["APPROVED_ORAL_DRUGS", "IN_WINDOW", "OUT_OF_WINDOW"]
