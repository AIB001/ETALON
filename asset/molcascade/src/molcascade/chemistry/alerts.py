"""The depiction substructure alert catalogues were actually authored against.

A substructure alert is a SMARTS pattern written by a chemist looking at a
drawing.  Whether it matches depends not only on what the molecule *is* but on
how it has been written down, and MolCascade writes molecules down twice: once
as the user supplied them, and once after standardization, which is the form
every downstream stage sees.  When those two writings disagree about a
functional group, an alert catalogue can reject a molecule for a notation
choice.  Exactly one such disagreement exists, it is not hypothetical, and it
costs a whole functional class:

RDKit's ``Normalizer`` -- which MolCascade runs, and which ChEMBL's own pipeline
runs for the same reasons -- rewrites the neutral sulfoxide ``S(=O)`` into the
charge-separated ``[S+][O-]``.  That is a deliberate and defensible convention
for a hypervalent S(IV) centre.  RDKit's *own* Brenk catalogue then matches the
result against ``charged_oxygen_or_sulfur_atoms``, and medchem's Glaxo set
matches it against ``R18 Quaternary C, Cl, I, P or S``.  Two independent
catalogues, neither of which flags the same molecule as the user wrote it.

Measured, the cost is every sulfoxide there is: omeprazole and the rest of the
proton-pump inhibitors, sulindac, modafinil, oxfendazole, and any generated
molecule carrying the group.  With ``brenk_action: reject`` this deleted them
silently, in a tier whose report said only that a structural alert had matched.

The fix is not to stop standardizing -- the charge-separated form is correct,
and the parent identity written into the run record must not change to suit a
SMARTS file.  The fix is to hand the catalogues the depiction they were written
for, and only for the duration of the match.  So this module builds a *view*:
the molecule as a catalogue author would have drawn it.  Nothing here reaches
the parent record, the identity key, or any artifact.

Scope is deliberately narrow.  Probing every RDKit normalization transform
shows only two create charge separation from a neutral input, and both are the
same S(IV)-oxide rewrite (sulfoxide and sulfinamide).  Nitro groups, azides,
amine N-oxides and pyridine N-oxides pass through unchanged, because they have
no neutral form to be rewritten *from* -- a catalogue flagging those is making
a real chemical claim, and this module leaves it alone.
"""

from __future__ import annotations

from typing import Any

from rdkit import Chem

#: A sulfur(IV) oxide left charge-separated by normalization.  ``X3`` on the
#: sulfur and ``X1`` on the oxygen together exclude sulfonium salts, sulfonates
#: and anything else whose charges are real rather than notational.
_SULFUR_IV_OXIDE = Chem.MolFromSmarts("[S+;X3]-[O-;X1]")


def catalog_view(molecule: Any) -> Any:
    """Return ``molecule`` as substructure alert catalogues expect to see it.

    Returns the input unchanged when there is nothing to rewrite, which is the
    overwhelmingly common case and costs one substructure match.  The result is
    a copy; the caller's molecule is never modified.
    """

    matches = molecule.GetSubstructMatches(_SULFUR_IV_OXIDE)
    if not matches:
        return molecule
    editable = Chem.RWMol(molecule)
    for sulfur_index, oxygen_index in matches:
        bond = editable.GetBondBetweenAtoms(sulfur_index, oxygen_index)
        if bond is None:  # pragma: no cover - the SMARTS guarantees the bond
            continue
        editable.GetAtomWithIdx(sulfur_index).SetFormalCharge(0)
        editable.GetAtomWithIdx(oxygen_index).SetFormalCharge(0)
        bond.SetBondType(Chem.BondType.DOUBLE)
    rewritten = editable.GetMol()
    try:
        Chem.SanitizeMol(rewritten)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        # A rewrite that will not sanitize is one this module was wrong about.
        # Matching the standardized form is a false positive; matching an
        # unsanitized molecule is undefined, so the former is the safe failure.
        return molecule
    return rewritten


def parse_for_alert_matching(smiles: object) -> Any:
    """Parse ``smiles`` into the depiction a catalogue should be matched against.

    Returns ``None`` for anything unparseable, leaving the caller to raise with
    whatever context it has -- the adapters know the parent id, this does not.
    """

    if not isinstance(smiles, str):
        return None
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return None
    return catalog_view(molecule)


__all__ = ["catalog_view", "parse_for_alert_matching"]
