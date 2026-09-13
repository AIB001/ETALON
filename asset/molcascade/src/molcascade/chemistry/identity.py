"""Deterministic RDKit parent standardisation and identity registration."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from rdkit import Chem, rdBase
from rdkit.Chem import RegistrationHash, rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize

from molcascade.chemistry.policies import (
    AtomLabelPolicy,
    AtomMapPolicy,
    ChargePolicy,
    FragmentPolicy,
    IdentityPolicy,
    IsotopePolicy,
    MetalDisconnectionPolicy,
    RegistrationHashVersion,
    StereoPolicy,
    TautomerPolicy,
    UndefinedStereoPolicy,
)
from molcascade.config.canonical import canonical_json_bytes, canonical_sha256

PARENT_HASH_SCHEME = "molcascade.parent.sha256"
PARENT_HASH_SCHEME_VERSION = 2
CANONICAL_PARENT_HASH_SCHEME = "molcascade.canonical-parent.sha256"
CANONICAL_PARENT_HASH_SCHEME_VERSION = 1
REGISTRATION_IMPLEMENTATION = "rdkit.Chem.RegistrationHash"


@dataclass(frozen=True, slots=True)
class StandardizationNotice:
    """An auditable, non-fatal transformation or chemistry warning."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class RegisteredParent:
    """Toolkit-neutral values emitted for one successfully registered parent."""

    parent_id: str
    identity_policy_id: str
    parent_smiles: str
    registration_key: str
    stereo_key: str
    formula: str
    registration_scheme: str
    registration_layers: Mapping[str, str]
    notices: tuple[StandardizationNotice, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "registration_layers",
            MappingProxyType(dict(self.registration_layers)),
        )


class StandardizationFailure(ValueError):
    """Expected per-record chemistry rejection, not a fatal stage failure."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def identity_policy_id(policy: IdentityPolicy) -> str:
    """Return a content identity for every explicit identity-policy choice."""

    digest = canonical_sha256(policy.model_dump(mode="json"))
    return f"identity-policy:sha256:{digest}"


def registration_scheme(policy: IdentityPolicy) -> RegistrationHash.HashScheme:
    """Select the RDKit layer scheme after policy transformations."""

    if policy.tautomer_policy is TautomerPolicy.INSENSITIVE:
        return RegistrationHash.HashScheme.TAUTOMER_INSENSITIVE_LAYERS
    return RegistrationHash.HashScheme.ALL_LAYERS


def canonical_parent_key(
    *,
    parent_smiles: str,
    stereo_key: str,
    formula: str,
) -> str:
    """Hash the parseable canonical representative used in persisted parent rows.

    RDKit's generic tautomer hash can occasionally group structures for which
    ``TautomerEnumerator`` cannot produce one common parseable representative.
    This refinement prevents such a broad registration equivalence from giving
    one parent ID two incompatible persisted structures.
    """

    digest = canonical_sha256(
        {
            "hash_scheme": CANONICAL_PARENT_HASH_SCHEME,
            "hash_scheme_version": CANONICAL_PARENT_HASH_SCHEME_VERSION,
            "parent_smiles": parent_smiles,
            "stereo_key": stereo_key,
            "formula": formula,
        }
    )
    return f"canonical-parent:sha256:{digest}"


def parent_id_from_registration(
    registration_key: str,
    policy: IdentityPolicy,
    *,
    canonical_key: str,
    rdkit_version: str | None = None,
    scheme_name: str | None = None,
) -> str:
    """Namespace a registration key by all implementation-sensitive inputs."""

    selected_scheme = scheme_name or registration_scheme(policy).name
    payload = {
        "hash_scheme": PARENT_HASH_SCHEME,
        "hash_scheme_version": PARENT_HASH_SCHEME_VERSION,
        "identity_policy_id": identity_policy_id(policy),
        "rdkit_version": rdkit_version or rdBase.rdkitVersion,
        "registration": {
            "implementation": REGISTRATION_IMPLEMENTATION,
            "layer_scheme": selected_scheme,
            "tautomer_hash_version": policy.registration_hash_version.value,
            "key": registration_key,
        },
        "canonical_parent": {
            "hash_scheme": CANONICAL_PARENT_HASH_SCHEME,
            "hash_scheme_version": CANONICAL_PARENT_HASH_SCHEME_VERSION,
            "key": canonical_key,
        },
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"parent:sha256:{digest}"


def _parse_smiles(value: str) -> Chem.Mol:
    with rdBase.BlockLogs():
        try:
            molecule = Chem.MolFromSmiles(value, sanitize=False)
        except Exception as error:
            raise StandardizationFailure("PARSE_ERROR", f"SMILES parser failed: {error}") from error
    if molecule is None:
        raise StandardizationFailure("PARSE_ERROR", "SMILES parser returned no molecule")
    return _sanitize(molecule)


def _parse_molblock(value: str) -> Chem.Mol:
    with rdBase.BlockLogs():
        try:
            molecule = Chem.MolFromMolBlock(
                value,
                sanitize=False,
                removeHs=False,
                strictParsing=True,
            )
        except Exception as error:
            raise StandardizationFailure(
                "PARSE_ERROR",
                f"mol block parser failed: {error}",
            ) from error
    if molecule is None:
        raise StandardizationFailure("PARSE_ERROR", "mol block parser returned no molecule")
    return _sanitize(molecule)


def _parse_mol2(value: str) -> Chem.Mol:
    molecule_markers = value.count("@<TRIPOS>MOLECULE")
    if molecule_markers > 1:
        raise StandardizationFailure(
            "MOL2_MULTIPLE_MOLECULES",
            "one raw MOL2 structure contains multiple @<TRIPOS>MOLECULE sections",
        )
    with rdBase.BlockLogs():
        try:
            molecule = Chem.MolFromMol2Block(
                value,
                sanitize=False,
                removeHs=False,
                cleanupSubstructures=True,
            )
        except Exception as error:
            raise StandardizationFailure(
                "PARSE_ERROR",
                f"MOL2 parser failed: {error}",
            ) from error
    if molecule is None:
        raise StandardizationFailure("PARSE_ERROR", "MOL2 parser returned no molecule")
    return _sanitize(molecule)


def _sanitize(molecule: Chem.Mol) -> Chem.Mol:
    with rdBase.BlockLogs():
        try:
            Chem.SanitizeMol(molecule)
            Chem.AssignStereochemistry(molecule, cleanIt=True, force=True)
        except Exception as error:
            raise StandardizationFailure(
                "VALENCE_ERROR",
                f"RDKit sanitization failed: {error}",
            ) from error
    if molecule.GetNumAtoms() == 0:
        raise StandardizationFailure("EMPTY_OR_FRAGMENT_ONLY", "molecule has no atoms")
    query_atoms = [atom.GetIdx() for atom in molecule.GetAtoms() if atom.HasQuery()]
    query_bonds = [bond.GetIdx() for bond in molecule.GetBonds() if bond.HasQuery()]
    if query_atoms or query_bonds:
        raise StandardizationFailure(
            "QUERY_FEATURE_NOT_ALLOWED",
            "concrete molecule input contains query features at "
            f"atoms={query_atoms}, bonds={query_bonds}",
        )
    substance_group_count = len(Chem.GetMolSubstanceGroups(molecule))
    has_link_nodes = molecule.HasProp("_molLinkNodes")
    if substance_group_count or has_link_nodes:
        raise StandardizationFailure(
            "UNSUPPORTED_STRUCTURAL_METADATA",
            "small-molecule registration does not accept link nodes or substance groups "
            f"(link_nodes={has_link_nodes}, substance_groups={substance_group_count})",
        )
    return molecule


def _remove_all_stereochemistry(molecule: Chem.Mol) -> None:
    """Remove tetrahedral, bond, enhanced, and RDKit atropisomer stereo.

    RDKit 2026.03.1 ``RemoveStereochemistry`` intentionally leaves atropisomer
    bond assignments in place, so those must be cleared explicitly for a truly
    stereo-insensitive identity policy.
    """

    Chem.RemoveStereochemistry(molecule)
    atrop_assignments = {
        Chem.BondStereo.STEREOATROPCW,
        Chem.BondStereo.STEREOATROPCCW,
    }
    for bond in molecule.GetBonds():
        if bond.GetStereo() in atrop_assignments:
            bond.SetStereo(Chem.BondStereo.STEREONONE)


def _connectivity_smiles(molecule: Chem.Mol) -> str:
    """Return a stereo-free graph spelling which still retains isotope labels."""

    comparison = Chem.Mol(molecule)
    for atom in comparison.GetAtoms():
        atom.SetAtomMapNum(0)
    try:
        comparison = Chem.RemoveHs(comparison, sanitize=True)
    except Exception as error:
        raise StandardizationFailure(
            "VALENCE_ERROR",
            f"could not remove explicit hydrogens: {error}",
        ) from error
    _remove_all_stereochemistry(comparison)
    return Chem.MolToSmiles(comparison, canonical=True, isomericSmiles=True)


def _specified_stereo_signature(molecule: Chem.Mol) -> str | None:
    """Return a canonical signature only when at least one stereo item is assigned."""

    potential = Chem.FindPotentialStereo(molecule, cleanIt=True, flagPossible=True)
    if not any(item.specified == Chem.StereoSpecified.Specified for item in potential):
        return None
    comparison = Chem.Mol(molecule)
    Chem.CanonicalizeEnhancedStereo(comparison)
    layers = RegistrationHash.GetMolLayers(comparison)
    return layers[RegistrationHash.HashLayer.CANONICAL_SMILES]


def _apply_input_annotation_policy(
    molecules: tuple[Chem.Mol, ...],
    policy: IdentityPolicy,
) -> tuple[StandardizationNotice, ...]:
    """Apply annotation policy to every supplied representation before choosing one."""

    notices: list[StandardizationNotice] = []
    mapped_count = sum(
        1
        for molecule in molecules
        for atom in molecule.GetAtoms()
        if atom.GetAtomMapNum()
    )
    if mapped_count:
        if policy.atom_map_policy is AtomMapPolicy.REJECT:
            raise StandardizationFailure(
                "ATOM_MAP_NOT_ALLOWED",
                f"input representations contain {mapped_count} atom-map annotations",
            )
        for molecule in molecules:
            for atom in molecule.GetAtoms():
                atom.SetAtomMapNum(0)
        notices.append(
            StandardizationNotice(
                "ATOM_MAP_REMOVED",
                f"removed {mapped_count} atom-map annotations from input representations",
            )
        )

    labelled_atom_count = sum(
        1
        for molecule in molecules
        for atom in molecule.GetAtoms()
        if atom.HasProp("atomLabel")
    )
    if labelled_atom_count:
        if policy.atom_label_policy is AtomLabelPolicy.REJECT:
            raise StandardizationFailure(
                "ATOM_LABEL_NOT_ALLOWED",
                "input representations contain "
                f"{labelled_atom_count} CXSMILES atom labels",
            )
        for molecule in molecules:
            for atom in molecule.GetAtoms():
                if atom.HasProp("atomLabel"):
                    atom.ClearProp("atomLabel")
        notices.append(
            StandardizationNotice(
                "ATOM_LABEL_REMOVED",
                "removed "
                f"{labelled_atom_count} CXSMILES atom labels from input representations",
            )
        )

    labelled_count = sum(
        1
        for molecule in molecules
        for atom in molecule.GetAtoms()
        if atom.GetIsotope()
    )
    if labelled_count and policy.isotope_policy is IsotopePolicy.REJECT:
        raise StandardizationFailure(
            "ISOTOPE_NOT_ALLOWED",
            f"input representations contain {labelled_count} isotope-labelled atoms",
        )
    if labelled_count and policy.isotope_policy is IsotopePolicy.INSENSITIVE:
        for molecule in molecules:
            for atom in molecule.GetAtoms():
                atom.SetIsotope(0)
        notices.append(
            StandardizationNotice(
                "ISOTOPE_LABELS_REMOVED",
                f"removed isotope labels from {labelled_count} input atoms",
            )
        )
    return tuple(notices)


def _parse_structure(
    raw_smiles: str | None,
    raw_molblock: str | None,
    policy: IdentityPolicy,
) -> tuple[Chem.Mol, tuple[StandardizationNotice, ...]]:
    smiles = raw_smiles.strip() if raw_smiles and raw_smiles.strip() else None
    molblock = raw_molblock if raw_molblock and raw_molblock.strip() else None
    if smiles is None and molblock is None:
        raise StandardizationFailure(
            "EMPTY_OR_FRAGMENT_ONLY",
            "both raw_smiles and raw_molblock are empty",
        )

    smiles_molecule = _parse_smiles(smiles) if smiles is not None else None
    molblock_molecule = _parse_molblock(molblock) if molblock is not None else None
    parsed = tuple(
        molecule
        for molecule in (smiles_molecule, molblock_molecule)
        if molecule is not None
    )
    notices = _apply_input_annotation_policy(parsed, policy)
    if smiles_molecule is not None and molblock_molecule is not None:
        if _connectivity_smiles(smiles_molecule) != _connectivity_smiles(
            molblock_molecule
        ):
            raise StandardizationFailure(
                "CONFLICTING_STRUCTURE_FIELDS",
                "raw_smiles and raw_molblock encode different molecular connectivity",
            )
        if policy.stereo_policy is StereoPolicy.SENSITIVE:
            smiles_stereo = _specified_stereo_signature(smiles_molecule)
            molblock_stereo = _specified_stereo_signature(molblock_molecule)
            if (
                smiles_stereo is not None
                and molblock_stereo is not None
                and smiles_stereo != molblock_stereo
            ):
                raise StandardizationFailure(
                    "CONFLICTING_STRUCTURE_FIELDS",
                    "raw_smiles and raw_molblock assign incompatible stereochemistry",
                )
            if smiles_stereo is not None and molblock_stereo is None:
                return smiles_molecule, notices
        # Prefer the mol block after connectivity/stereo reconciliation because
        # it can carry coordinates and an explicit unknown-bond stereo marker.
        return molblock_molecule, notices
    if smiles_molecule is not None:
        return smiles_molecule, notices
    assert molblock_molecule is not None
    return molblock_molecule, notices


def _parse_formatted_structure(
    raw_format: str | None,
    raw_structure: str | None,
    policy: IdentityPolicy,
) -> tuple[Chem.Mol, tuple[StandardizationNotice, ...]]:
    """Parse one explicitly formatted raw payload without format guessing."""

    if not isinstance(raw_structure, str) or not raw_structure.strip():
        raise StandardizationFailure(
            "EMPTY_OR_FRAGMENT_ONLY",
            "raw_structure is empty",
        )
    if raw_format == "SMILES":
        molecule = _parse_smiles(raw_structure.strip())
    elif raw_format == "MOLBLOCK":
        molecule = _parse_molblock(raw_structure)
    elif raw_format == "MOL2":
        molecule = _parse_mol2(raw_structure)
    else:
        raise StandardizationFailure(
            "RAW_FORMAT_UNSUPPORTED",
            f"raw_format must be SMILES, MOLBLOCK, or MOL2; received {raw_format!r}",
        )
    notices = _apply_input_annotation_policy((molecule,), policy)
    return molecule, notices


def _candidate_fragments(
    molecule: Chem.Mol,
    policy: IdentityPolicy,
) -> tuple[tuple[Chem.Mol, ...], int]:
    """Return every largest candidate; identity-aware tie handling happens later."""

    try:
        fragments = list(Chem.GetMolFrags(molecule, asMols=True, sanitizeFrags=True))
    except Exception as error:
        raise StandardizationFailure(
            "VALENCE_ERROR",
            f"could not sanitize molecular fragments: {error}",
        ) from error
    if not fragments:
        raise StandardizationFailure(
            "EMPTY_OR_FRAGMENT_ONLY",
            "no sanitizable molecular fragment remains",
        )
    fragment_count = len(fragments)
    if fragment_count > 1 and policy.fragment_policy is FragmentPolicy.REJECT_MULTICOMPONENT:
        raise StandardizationFailure(
            "MULTICOMPONENT_NOT_ALLOWED",
            f"molecule contains {fragment_count} disconnected components",
        )

    candidates = fragments
    if policy.fragment_policy is FragmentPolicy.LARGEST_ORGANIC:
        candidates = [
            fragment
            for fragment in fragments
            if any(atom.GetAtomicNum() == 6 for atom in fragment.GetAtoms())
        ]
        if not candidates:
            raise StandardizationFailure(
                "EMPTY_OR_FRAGMENT_ONLY",
                "largest-organic policy found no carbon-containing fragment",
            )

    largest_size = max(fragment.GetNumHeavyAtoms() for fragment in candidates)
    largest = [
        fragment for fragment in candidates if fragment.GetNumHeavyAtoms() == largest_size
    ]
    return tuple(largest), fragment_count


def _undefined_stereo(molecule: Chem.Mol) -> tuple[str, ...]:
    inspected = Chem.Mol(molecule)
    counts: dict[str, int] = {}
    for stereo in Chem.FindPotentialStereo(inspected, cleanIt=True, flagPossible=True):
        if stereo.specified != Chem.StereoSpecified.Specified:
            stereo_type = str(stereo.type)
            counts[stereo_type] = counts.get(stereo_type, 0) + 1
    # RDKit atom/bond indices follow the input spelling and are not canonical.
    # Type counts retain useful diagnostics without making decision artifacts
    # depend on SMILES traversal order.
    return tuple(f"{stereo_type}:count={counts[stereo_type]}" for stereo_type in sorted(counts))


def _canonical_cx_smiles(molecule: Chem.Mol) -> str:
    """Serialize every identity-bearing CX layer used by RegistrationHash."""

    parameters = Chem.SmilesWriteParams()
    parameters.canonical = True
    # Keep this true even after stereo removal: RDKit also controls isotope
    # rendering with this flag.
    parameters.doIsomericSmiles = True
    return Chem.MolToCXSmiles(
        molecule,
        parameters,
        RegistrationHash.DEFAULT_CXFLAG,
    )


def _has_labile_isotopic_hydrogen(molecule: Chem.Mol) -> bool:
    return any(
        atom.GetAtomicNum() == 1
        and atom.GetIsotope() > 0
        and atom.GetDegree() == 1
        and next(iter(atom.GetNeighbors())).GetAtomicNum() not in {1, 6}
        for atom in molecule.GetAtoms()
    )


def _registration_layers(
    molecule: Chem.Mol,
    policy: IdentityPolicy,
) -> Mapping[RegistrationHash.HashLayer, str]:
    enable_v2 = policy.registration_hash_version is RegistrationHashVersion.V2
    try:
        return RegistrationHash.GetMolLayers(
            molecule,
            enable_tautomer_hash_v2=enable_v2,
        )
    except Exception as error:
        raise StandardizationFailure(
            "REGISTRATION_ERROR",
            f"RDKit RegistrationHash failed: {error}",
        ) from error


@dataclass(frozen=True, slots=True)
class _ProcessedCandidate:
    registration_molecule: Chem.Mol
    display_molecule: Chem.Mol
    registration_layers: Mapping[RegistrationHash.HashLayer, str]
    registration_key: str
    canonical_parent_key: str
    display_smiles: str
    stereo_key: str
    formula: str
    notices: tuple[StandardizationNotice, ...]


def _process_candidate(
    molecule: Chem.Mol,
    policy: IdentityPolicy,
    *,
    uncharger: Any | None,
    tautomer_enumerator: Any | None,
) -> _ProcessedCandidate:
    notices: list[StandardizationNotice] = []
    charged = Chem.Mol(molecule)
    if uncharger is not None:
        before_charge = Chem.GetFormalCharge(charged)
        charged = uncharger.uncharge(charged)
        after_charge = Chem.GetFormalCharge(charged)
        if before_charge != after_charge:
            notices.append(
                StandardizationNotice(
                    "CHARGE_NORMALIZED",
                    f"formal charge changed from {before_charge} to {after_charge}",
                )
            )

    registration_molecule = Chem.Mol(charged)
    display_molecule = Chem.Mol(charged)
    registration_matches_display = True
    if policy.tautomer_policy is not TautomerPolicy.PRESERVE:
        if tautomer_enumerator is None:
            raise StandardizationFailure(
                "TAUTOMER_CANONICALIZATION_ERROR",
                "tautomer policy requires an RDKit tautomer enumerator",
            )
        before_tautomer = _canonical_cx_smiles(display_molecule)
        try:
            # Enumeration walks candidate tautomers, and a candidate that turns
            # out not to be kekulizable makes RDKit log "Can't kekulize mol"
            # before discarding it itself.  That line reaching a screening
            # console names no molecule and no stage, and reads like a molecule
            # was damaged when nothing was: risperidone triggers it and comes
            # back byte-identical.  Blocking it matches every other RDKit call
            # in this module.  A canonicalization that genuinely fails still
            # raises, and is still reported below as
            # ``TAUTOMER_CANONICALIZATION_ERROR``.
            with rdBase.BlockLogs():
                display_molecule = tautomer_enumerator.Canonicalize(
                    Chem.Mol(display_molecule)
                )
            _sanitize(display_molecule)
        except Exception as error:
            if (
                policy.tautomer_policy is TautomerPolicy.INSENSITIVE
                and _has_labile_isotopic_hydrogen(charged)
            ):
                display_molecule = Chem.Mol(charged)
                notices.append(
                    StandardizationNotice(
                        "TAUTOMER_DISPLAY_FALLBACK",
                        "RDKit display tautomer failed validation; retained the "
                        "pre-tautomer graph for a labile isotopic hydrogen",
                    )
                )
            else:
                raise StandardizationFailure(
                    "TAUTOMER_CANONICALIZATION_ERROR",
                    f"RDKit tautomer canonicalization failed: {error}",
                ) from error
        else:
            after_tautomer = _canonical_cx_smiles(display_molecule)
            if before_tautomer != after_tautomer:
                registration_matches_display = False
                notices.append(
                    StandardizationNotice(
                        "TAUTOMER_CANONICALIZED",
                        f"canonical tautomer changed {before_tautomer} to {after_tautomer}",
                    )
                )
        if policy.tautomer_policy is TautomerPolicy.CANONICALIZE:
            registration_molecule = Chem.Mol(display_molecule)
            registration_matches_display = True

    undefined = sorted(
        set(_undefined_stereo(registration_molecule))
        | set(_undefined_stereo(display_molecule))
    )
    if undefined:
        detail = "undefined stereochemistry at " + ", ".join(undefined)
        if policy.undefined_stereo_policy is UndefinedStereoPolicy.REJECT:
            raise StandardizationFailure("UNDEFINED_STEREO", detail)
        if policy.undefined_stereo_policy is UndefinedStereoPolicy.WARN:
            notices.append(StandardizationNotice("UNDEFINED_STEREO", detail))

    if policy.stereo_policy is StereoPolicy.INSENSITIVE:
        had_stereo = any(
            item.specified == Chem.StereoSpecified.Specified
            for graph in (registration_molecule, display_molecule)
            for item in Chem.FindPotentialStereo(graph)
        )
        _remove_all_stereochemistry(registration_molecule)
        _remove_all_stereochemistry(display_molecule)
        if had_stereo:
            notices.append(
                StandardizationNotice(
                    "STEREO_REMOVED",
                    "specified stereochemistry was removed by identity policy",
                )
            )

    _sanitize(registration_molecule)
    _sanitize(display_molecule)
    try:
        Chem.CanonicalizeEnhancedStereo(registration_molecule)
        Chem.CanonicalizeEnhancedStereo(display_molecule)
    except Exception as error:
        raise StandardizationFailure(
            "STEREO_CANONICALIZATION_ERROR",
            f"RDKit enhanced-stereo canonicalization failed: {error}",
        ) from error

    layers = _registration_layers(registration_molecule, policy)
    key = RegistrationHash.GetMolHash(layers, registration_scheme(policy))
    display_smiles = _canonical_cx_smiles(display_molecule)
    display_layers = (
        layers
        if registration_matches_display
        else _registration_layers(display_molecule, policy)
    )
    display_stereo_key = RegistrationHash.GetMolHash(
        display_layers,
        RegistrationHash.HashScheme.ALL_LAYERS,
    )
    display_formula = rdMolDescriptors.CalcMolFormula(display_molecule)
    representative_key = canonical_parent_key(
        parent_smiles=display_smiles,
        stereo_key=display_stereo_key,
        formula=display_formula,
    )
    return _ProcessedCandidate(
        registration_molecule=registration_molecule,
        display_molecule=display_molecule,
        registration_layers=layers,
        registration_key=key,
        canonical_parent_key=representative_key,
        display_smiles=display_smiles,
        stereo_key=display_stereo_key,
        formula=display_formula,
        notices=tuple(notices),
    )


def _coalesce_notices(
    notices: list[StandardizationNotice],
) -> tuple[StandardizationNotice, ...]:
    """Guarantee one decision row per reason code for the decision contract key."""

    details: dict[str, set[str]] = {}
    for notice in notices:
        details.setdefault(notice.code, set()).add(notice.detail)
    return tuple(
        StandardizationNotice(code, "; ".join(sorted(details[code])))
        for code in sorted(details)
    )


def _prepared_graph(
    molecule: Chem.Mol,
    *,
    input_notices: tuple[StandardizationNotice, ...],
    metal_disconnector: Any | None,
    normalizer: Any | None,
    reionizer: Any | None,
) -> tuple[Chem.Mol, list[StandardizationNotice]]:
    """Everything done to the graph before it is cut into candidate fragments.

    Extracted from :func:`_standardize_graph` unchanged, because where the cut
    happens decides what "a component" means and a second implementation would
    drift from this one silently.  Measured: sodium valproate
    (``CCCC(CCC)C(=O)O[Na]``) and warfarin sodium parse as *one* fragment and
    become two only after metal disconnection -- so anything that splits on
    ``.`` before this prefix disagrees with what the run recorded, and would
    report a fragment split the run never made.
    """

    notices = list(input_notices)
    working = Chem.Mol(molecule)
    try:
        working = Chem.RemoveHs(working, sanitize=True)
        if metal_disconnector is not None:
            before_metal = _connectivity_smiles(working)
            working = metal_disconnector.Disconnect(working)
            after_metal = _connectivity_smiles(working)
            if before_metal != after_metal:
                notices.append(
                    StandardizationNotice(
                        "METAL_DISCONNECTED",
                        f"metal disconnection changed {before_metal} to {after_metal}",
                    )
                )
        if normalizer is not None:
            working = normalizer.normalize(working)
        if reionizer is not None:
            working = reionizer.reionize(working)
        _sanitize(working)
    except StandardizationFailure:
        raise
    except Exception as error:
        raise StandardizationFailure(
            "STANDARDIZATION_ERROR",
            f"RDKit standardization failed: {error}",
        ) from error
    return working, notices


def _standardize_graph(
    molecule: Chem.Mol,
    policy: IdentityPolicy,
    *,
    input_notices: tuple[StandardizationNotice, ...],
    metal_disconnector: Any | None,
    normalizer: Any | None,
    reionizer: Any | None,
    uncharger: Any | None,
    tautomer_enumerator: Any | None,
) -> tuple[_ProcessedCandidate, tuple[StandardizationNotice, ...]]:
    working, notices = _prepared_graph(
        molecule,
        input_notices=input_notices,
        metal_disconnector=metal_disconnector,
        normalizer=normalizer,
        reionizer=reionizer,
    )

    fragments, fragment_count = _candidate_fragments(working, policy)
    processed = tuple(
        _process_candidate(
            fragment,
            policy,
            uncharger=uncharger,
            tautomer_enumerator=tautomer_enumerator,
        )
        for fragment in fragments
    )
    distinct_keys = sorted(
        {
            (candidate.registration_key, candidate.canonical_parent_key)
            for candidate in processed
        }
    )
    if len(distinct_keys) > 1 and policy.reject_ambiguous_fragment_ties:
        representations = sorted(
            f"{candidate.display_smiles} "
            f"[{candidate.registration_key}; {candidate.canonical_parent_key}]"
            for candidate in processed
        )
        raise StandardizationFailure(
            "AMBIGUOUS_FRAGMENTS",
            "equally large candidates have different refined identity keys: "
            + ", ".join(representations),
        )
    selected = min(
        processed,
        key=lambda candidate: (
            candidate.registration_key,
            candidate.canonical_parent_key,
            candidate.display_smiles,
        ),
    )
    # Every equivalent fragment occurred in the raw record.  Coalescing their
    # audit notices avoids component-order-dependent decisions while retaining
    # transformations which applied to either spelling of the selected parent.
    for candidate in processed:
        if (
            candidate.registration_key == selected.registration_key
            and candidate.canonical_parent_key == selected.canonical_parent_key
        ):
            notices.extend(candidate.notices)
    if fragment_count > 1:
        notices.append(
            StandardizationNotice(
                "FRAGMENTS_REMOVED",
                f"selected one parent fragment from {fragment_count} components",
            )
        )
        if len(distinct_keys) > 1:
            notices.append(
                StandardizationNotice(
                    "AMBIGUOUS_FRAGMENT_TIE_RESOLVED",
                    "reject_ambiguous_fragment_ties=false selected the "
                    "lexicographically smallest refined identity key",
                )
            )
    return selected, _coalesce_notices(notices)


class ParentStandardizer:
    """Reusable standardizer whose RDKit rule catalogs are built once per policy."""

    def __init__(self, policy: IdentityPolicy | None = None) -> None:
        self.policy = policy or IdentityPolicy()
        self._metal_disconnector = (
            rdMolStandardize.MetalDisconnector()
            if self.policy.metal_disconnection_policy
            is MetalDisconnectionPolicy.DISCONNECT
            else None
        )
        self._normalizer = (
            rdMolStandardize.Normalizer()
            if self.policy.normalize_functional_groups
            else None
        )
        self._reionizer = (
            rdMolStandardize.Reionizer()
            if self.policy.charge_policy is not ChargePolicy.PRESERVE
            else None
        )
        self._uncharger = (
            rdMolStandardize.Uncharger()
            if self.policy.charge_policy is ChargePolicy.UNCHARGE_WHERE_POSSIBLE
            else None
        )
        self._tautomer_enumerator = (
            rdMolStandardize.TautomerEnumerator()
            if self.policy.tautomer_policy is not TautomerPolicy.PRESERVE
            else None
        )

    def components(
        self,
        *,
        raw_format: str | None = None,
        raw_structure: str | None = None,
        raw_smiles: str | None = None,
        raw_molblock: str | None = None,
    ) -> RecordComponents:
        """The components this record would be cut into, without choosing one.

        Shares :func:`_prepared_graph` with :meth:`standardize` rather than
        re-deriving the prefix, so the two can never disagree about where a
        record splits.
        """

        formatted_mode = raw_format is not None or raw_structure is not None
        legacy_mode = raw_smiles is not None or raw_molblock is not None
        if formatted_mode and legacy_mode:
            raise StandardizationFailure(
                "RAW_INPUT_MODE_CONFLICT",
                "formatted raw input cannot be mixed with legacy raw structure fields",
            )
        if formatted_mode:
            molecule, input_notices = _parse_formatted_structure(
                raw_format, raw_structure, self.policy
            )
        else:
            molecule, input_notices = _parse_structure(raw_smiles, raw_molblock, self.policy)
        working, _notices = _prepared_graph(
            molecule,
            input_notices=input_notices,
            metal_disconnector=self._metal_disconnector,
            normalizer=self._normalizer,
            reionizer=self._reionizer,
        )
        pieces = Chem.GetMolFrags(working, asMols=True, sanitizeFrags=False)
        return RecordComponents(
            smiles=tuple(Chem.MolToSmiles(piece) for piece in pieces),
            heavy_atoms=tuple(piece.GetNumHeavyAtoms() for piece in pieces),
        )

    def standardize(
        self,
        *,
        raw_format: str | None = None,
        raw_structure: str | None = None,
        raw_smiles: str | None = None,
        raw_molblock: str | None = None,
    ) -> RegisteredParent:
        """Parse, standardize, register, and identify one molecular parent."""

        formatted_mode = raw_format is not None or raw_structure is not None
        legacy_mode = raw_smiles is not None or raw_molblock is not None
        if formatted_mode and legacy_mode:
            raise StandardizationFailure(
                "RAW_INPUT_MODE_CONFLICT",
                "formatted raw input cannot be mixed with legacy raw structure fields",
            )
        if formatted_mode:
            molecule, input_notices = _parse_formatted_structure(
                raw_format,
                raw_structure,
                self.policy,
            )
        else:
            molecule, input_notices = _parse_structure(
                raw_smiles,
                raw_molblock,
                self.policy,
            )
        candidate, notices = _standardize_graph(
            molecule,
            self.policy,
            input_notices=input_notices,
            metal_disconnector=self._metal_disconnector,
            normalizer=self._normalizer,
            reionizer=self._reionizer,
            uncharger=self._uncharger,
            tautomer_enumerator=self._tautomer_enumerator,
        )
        parent_smiles = candidate.display_smiles
        if not parent_smiles:
            raise StandardizationFailure(
                "EMPTY_OR_FRAGMENT_ONLY",
                "canonical parent SMILES is empty",
            )

        scheme = registration_scheme(self.policy)
        registration_key = candidate.registration_key
        layer_values = {
            layer.name: value for layer, value in candidate.registration_layers.items()
        }
        return RegisteredParent(
            parent_id=parent_id_from_registration(
                registration_key,
                self.policy,
                canonical_key=candidate.canonical_parent_key,
                scheme_name=scheme.name,
            ),
            identity_policy_id=identity_policy_id(self.policy),
            parent_smiles=parent_smiles,
            registration_key=registration_key,
            stereo_key=candidate.stereo_key,
            formula=candidate.formula,
            registration_scheme=scheme.name,
            registration_layers=layer_values,
            notices=notices,
        )


@lru_cache(maxsize=32)
def _cached_standardizer(policy: IdentityPolicy) -> ParentStandardizer:
    """Reuse immutable RDKit rule catalogs for the convenience function."""

    return ParentStandardizer(policy)


@dataclass(frozen=True, slots=True)
class RecordComponents:
    """The disconnected components one library record has, as the run sees them.

    "As the run sees them" is the whole point.  A record is cut into components
    only after ``RemoveHs``, metal disconnection, normalisation and reionisation
    -- so sodium valproate, which parses as a single fragment, is two components
    by the time the choice is made, and anything that split on ``.`` beforehand
    would disagree with what the run recorded.

    Exposed because de-salting keeps exactly one of these and the run records
    only that it kept one: ``FRAGMENTS_REMOVED`` says "selected one parent
    fragment from N components" and never says which N.  Recomputing them is the
    only way a reader can answer "and what was thrown away".
    """

    smiles: tuple[str, ...]
    heavy_atoms: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.smiles)


def record_components(
    *,
    raw_format: str | None = None,
    raw_structure: str | None = None,
    raw_smiles: str | None = None,
    raw_molblock: str | None = None,
    policy: IdentityPolicy | None = None,
) -> RecordComponents:
    """Split one raw record the way registration would, without registering it.

    Reuses the standardizer cached per policy, so a caller replaying many
    records pays for the rule catalogues once.
    """

    return _cached_standardizer(policy or IdentityPolicy()).components(
        raw_format=raw_format,
        raw_structure=raw_structure,
        raw_smiles=raw_smiles,
        raw_molblock=raw_molblock,
    )


def standardize_parent(
    *,
    raw_format: str | None = None,
    raw_structure: str | None = None,
    raw_smiles: str | None = None,
    raw_molblock: str | None = None,
    policy: IdentityPolicy | None = None,
) -> RegisteredParent:
    """Convenience wrapper around a cached policy-specific standardizer."""

    selected_policy = policy or IdentityPolicy()
    return _cached_standardizer(selected_policy).standardize(
        raw_format=raw_format,
        raw_structure=raw_structure,
        raw_smiles=raw_smiles,
        raw_molblock=raw_molblock,
    )


def rdkit_identity_metadata(policy: IdentityPolicy) -> dict[str, Any]:
    """Return exact implementation identity suitable for artifact metadata."""

    return {
        "identity_policy_id": identity_policy_id(policy),
        "rdkit_version": rdBase.rdkitVersion,
        "parent_hash_scheme": PARENT_HASH_SCHEME,
        "parent_hash_scheme_version": PARENT_HASH_SCHEME_VERSION,
        "canonical_parent_hash_scheme": CANONICAL_PARENT_HASH_SCHEME,
        "canonical_parent_hash_scheme_version": CANONICAL_PARENT_HASH_SCHEME_VERSION,
        "registration_implementation": REGISTRATION_IMPLEMENTATION,
        "registration_layer_scheme": registration_scheme(policy).name,
        "registration_hash_version": policy.registration_hash_version.value,
    }


__all__ = [
    "CANONICAL_PARENT_HASH_SCHEME",
    "CANONICAL_PARENT_HASH_SCHEME_VERSION",
    "PARENT_HASH_SCHEME",
    "PARENT_HASH_SCHEME_VERSION",
    "REGISTRATION_IMPLEMENTATION",
    "ParentStandardizer",
    "RecordComponents",
    "RegisteredParent",
    "StandardizationFailure",
    "StandardizationNotice",
    "canonical_parent_key",
    "identity_policy_id",
    "parent_id_from_registration",
    "rdkit_identity_metadata",
    "record_components",
    "registration_scheme",
    "standardize_parent",
]
