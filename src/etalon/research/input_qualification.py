"""Optional, source-bound SDF qualification; never a pose verifier or a gate.

The ordinary scientific-input envelope supplies lineage and visibility. Exact
evidence refs bind its file identities to released Artifacts (or an explicit
JSON location inside one). Results remain ordinary Artifacts in the caller's H.
Only V2000 tetrahedral/double-bond information is interpreted by this adapter.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence import _collect, _references, _visible
from .evidence_refs import (EvidenceError, EvidenceResolver, load_workspace,
                            make_ref, resolve_artifact, validate_ref)
from .read_scope import bounded_read
from .schema import Artifact
from .scientific_input import identity, validate_input

SCHEMA = "etalon-input-qualification/1"
POLICY = "strict_full_identity/1"
MAX_BYTES = 8 * 1024 * 1024
MAX_MAPPINGS = 1024


def _pin(value):
    return {k: value[k] for k in ("sha256", "bytes")}


def _pointer(document, pointer):
    if not isinstance(pointer, list) or len(pointer) > 24:
        raise ValueError("identity_pointer must be a bounded list of JSON keys/indices")
    for key in pointer:
        if isinstance(document, list) and type(key) is int and 0 <= key < len(document):
            document = document[key]
        elif isinstance(document, dict) and isinstance(key, str) and key in document:
            document = document[key]
        else:
            raise ValueError("identity_pointer does not select source identity")
    return document


def _payload_values(value, key):
    if isinstance(value, dict):
        own = {value[key]} if isinstance(value.get(key), str) else set()
        return own | set().union(*(_payload_values(v, key) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_payload_values(v, key) for v in value))
    return set()


def _payload_roles(value):
    roles = _payload_values(value, "role")
    if not _visible(value):
        roles.add("sealed_holdout")
    return roles


def _bundle_access(bundle, purpose):
    # The report embeds the full request, including unbound labels and notes.
    # Binding validation alone cannot establish permission to return that data.
    roles = _payload_roles(bundle)
    if "sealed_holdout" in roles:
        raise ValueError("sealed source payload is unavailable")
    if purpose == "conditioning" and roles - {"conditioning"}:
        raise ValueError("non-conditioning bundle content requested for conditioning")


def _report_policy(report):
    restricted = any(p["authoritative_roles"] != ["diagnostic"]
                     for p in report["source_verification"])
    restricted |= bool(_payload_roles(report["request"]["bundle"]) - {"conditioning"})
    kinds = set().union(*(set(p["authoritative_source_kinds"])
                          for p in report["source_verification"]))
    return ("independent_validation" if restricted else "diagnostic",
            "fixture" if "fixture" in kinds else "existing_real_artifact")


def _sources(bundle, workspace, purpose):
    """Check exact refs and transitive roles, not producer-supplied role labels."""
    ws = load_workspace(workspace)
    refs = [validate_ref(row["evidence_ref"]) for row in bundle["sources"]]
    rows, _, _ = _collect(ws, sorted({ref["source_id"] for ref in refs}))
    checked, active = {}, set()

    def visit(ref):
        ref = validate_ref(ref)
        key = (ref["source_id"], ref["record_id"])
        row = rows.get(key[0], {}).get(key[1])
        if row is None or digest(row["record"]) != ref["record_digest"]:
            raise EvidenceError("source ref unavailable or record digest mismatch")
        if key in active:
            raise EvidenceError("cyclic source qualification lineage")
        if key in checked:
            return checked[key]
        active.add(key)
        body = row["record"]
        roles = _payload_roles(body) | _payload_roles({"visibility": row.get("visibility")})
        kinds = _payload_values(body, "source_kind")
        references = _references(body)
        if row.get("record_type") == "artifact":
            path = resolve_artifact(body, resolver=EvidenceResolver._from_validated(ws, key[0]))
            if body["bytes"] > MAX_BYTES:
                raise ValueError("source lineage artifact exceeds qualification size bound")
            data = path.read_bytes()
            if {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} != _pin(body):
                raise ValueError("source lineage artifact changed during read")
            # Follow explicit JSON payload refs too, even without content projection.
            # Binary/SDF artifacts supply no JSON role assertion. No refs are guessed.
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                payload = None
            if payload is not None:
                roles |= _payload_roles(payload)
                kinds |= _payload_values(payload, "source_kind")
                references += _references(payload)
        # Ordinary refs/parents remain visible to the existing evidence reader.
        for parent in references:
            if isinstance(parent, str):
                candidate = rows.get(key[0], {}).get(parent)
                if candidate is None:
                    raise EvidenceError("unresolved source lineage")
                parent = make_ref(key[0], parent, candidate["record"])
            _, parent_roles, parent_kinds = visit(parent)
            roles |= parent_roles
            kinds |= parent_kinds
        active.remove(key)
        checked[key] = (body, roles, kinds)
        return body, roles, kinds

    output = []
    for source, ref in zip(bundle["sources"], refs):
        if "member" in ref or "selector" in ref:
            raise ValueError("qualification requires an exact whole Artifact ref")
        body, roles, kinds = visit(ref)
        if not ref["record_id"].startswith("artifact-"):
            raise ValueError("source anchor must be an Artifact")
        anchor = resolve_artifact(body, resolver=EvidenceResolver._from_validated(ws, ref["source_id"]))
        if "sha256" in ref and _pin(ref) != _pin(body):
            raise ValueError("ref artifact identity mismatch")
        if "identity_pointer" in source:
            if body["bytes"] > MAX_BYTES:
                raise ValueError("source anchor exceeds qualification size bound")
            document = json.loads(anchor.read_text(encoding="utf-8-sig"))
            selected = _pointer(document, source["identity_pointer"])
            # Pointer selection cannot discard the container's visibility or lineage.
            roles = roles | _payload_roles(document)
            for parent in _references(document):
                if isinstance(parent, str):
                    candidate = rows.get(ref["source_id"], {}).get(parent)
                    if candidate is None:
                        raise EvidenceError("unresolved source payload lineage")
                    parent = make_ref(ref["source_id"], parent, candidate["record"])
                _, parent_roles, parent_kinds = visit(parent)
                roles |= parent_roles
                kinds |= parent_kinds
        else:
            selected = body
        restricted = bool(roles & {"independent_validation", "evaluation_only", "context", "sealed_holdout"})
        if "sealed_holdout" in roles:
            raise ValueError("sealed source payload is unavailable")
        if restricted and source["role"] == "conditioning":
            raise ValueError("non-conditioning evidence cannot be relabelled conditioning")
        if purpose == "conditioning" and (restricted or source["role"] != "conditioning"):
            raise ValueError("non-conditioning evidence requested for conditioning")
        if not isinstance(selected, dict) or _pin(selected) != _pin(source):
            raise ValueError("source identity differs from authoritative Artifact")
        if identity(anchor) != _pin(body):
            raise ValueError("source anchor changed during read")
        output.append({"id": source["id"], "evidence_ref": ref,
                       "authoritative_roles": sorted(str(r) for r in roles if r),
                       "authoritative_source_kinds": sorted(kinds),
                       "source_kind": body.get("source_kind"),
                       "identity_pointer": source.get("identity_pointer"),
                       "anchor_identity": _pin(body)})
    return output


def _raw(block):
    # Same field convention as examples/diagnose_pose_stereochemistry.py.
    lines = block.splitlines()
    if len(lines) < 4 or "V2000" not in lines[3]:
        raise ValueError("qualification adapter requires V2000; no silent format conversion")
    na, nb = int(lines[3][:3]), int(lines[3][3:6])
    if len(lines) < 4 + na + nb:
        raise ValueError("truncated V2000 record")
    return {"header": lines[:3], "counts": lines[3],
            "atoms": [{"index": i, "line": i + 5, "raw": line,
                       "parity": int(line[39:42].strip() or "0")}
                      for i, line in enumerate(lines[4:4 + na])],
            "bonds": [{"index": i, "line": 5 + na + i, "raw": line,
                       "begin": int(line[:3]) - 1, "end": int(line[3:6]) - 1,
                       "type": int(line[6:9]), "stereo": int(line[9:12].strip() or "0")}
                      for i, line in enumerate(lines[4 + na:4 + na + nb])],
            "m_records": [line for line in lines[4 + na + nb:] if line.startswith("M  ")]}


def _neutral(mol):
    from rdkit import Chem
    result = Chem.Mol(mol)
    for atom in result.GetAtoms():
        if atom.GetAtomicNum() == 1 and atom.GetFormalCharge() != 0:
            raise ValueError("charged hydrogen is unsupported by qualification H normalization")
        atom.SetIntProp("_qualification_index", atom.GetIdx())
        atom.SetAtomMapNum(0)
    Chem.RemoveStereochemistry(result)
    # Preserve isotope-labelled/protected H, charge, radicals and total H state.
    return Chem.RemoveHs(result)


def _configuration(mol, kind, index):
    if kind == "tetrahedral":
        atom = mol.GetAtomWithIdx(index)
        return atom.GetProp("_CIPCode") if atom.HasProp("_CIPCode") else None
    stereo = str(mol.GetBondWithIdx(index).GetStereo())
    return stereo.removeprefix("STEREO") if stereo in {"STEREOE", "STEREOZ"} else None


def _inventory(mol, raw, origin):
    from rdkit import Chem
    Chem.AssignStereochemistry(mol, cleanIt=False, force=True)
    potential = {(str(s.type), s.centeredOn): s for s in Chem.FindPotentialStereo(mol)}
    geometry = Chem.Mol(mol)
    Chem.RemoveStereochemistry(geometry)
    if mol.GetConformer().Is3D():
        Chem.AssignStereochemistryFrom3D(geometry, replaceExistingTags=True)
        Chem.AssignStereochemistry(geometry, cleanIt=False, force=True)
    rows = []
    candidates = [("double_bond", b.GetIdx(), [b.GetBeginAtomIdx(), b.GetEndAtomIdx()], "Bond_Double")
                  for b in mol.GetBonds() if b.GetBondType() == Chem.BondType.DOUBLE]
    candidates += [("tetrahedral", a.GetIdx(), [a.GetIdx()], "Atom_Tetrahedral") for a in mol.GetAtoms()
                   if a.GetAtomicNum() != 1 and (a.GetTotalDegree() == 4 or ("Atom_Tetrahedral", a.GetIdx()) in potential)]
    for kind, index, atoms, ptype in candidates:
        feature = potential.get((ptype, index))
        applicable = feature is not None
        raw_field = raw["bonds" if kind == "double_bond" else "atoms"][index]
        explicit_unknown = ((kind == "double_bond" and raw_field["stereo"] == 3)
                            or (kind == "tetrahedral" and raw_field["parity"] == 3)
                            or (feature is not None and str(feature.specified) == "Unknown"))
        value = _configuration(mol, kind, index) if applicable else None
        state = None if not applicable else "explicit_unknown" if explicit_unknown else "resolved" if value else "unspecified"
        if state == "resolved":
            if origin != "source":
                basis = "generated_coordinate_perception" if mol.GetConformer().Is3D() else "generated_depiction"
            elif mol.GetConformer().Is3D():
                basis = "source_coordinate_perception"
            else:
                basis = "source_depiction"
        else:
            basis = "explicit_raw_unknown" if explicit_unknown else "no_configuration_assertion"
        rows.append({"kind": kind, "index": index, "atoms": atoms,
                     "applicability": "potential_stereogenic" if applicable else "nonstereogenic",
                     "raw_assertion": {"state": "explicit_unknown" if explicit_unknown else
                                       "resolved" if kind == "tetrahedral" and raw_field["parity"] in {1, 2}
                                       else "unspecified",
                                       "basis": "molfile_field; parity_requires_atom_order_and_parser_interpretation"},
                     "information": {"state": state, "configuration": value if state == "resolved" else None,
                                     "basis": basis},
                     "coordinate_perception": {"configuration": _configuration(geometry, kind, index) if applicable else None,
                                               "basis": "existing_coordinates_only_no_H_added",
                                               "is_3d": mol.GetConformer().Is3D()},
                     "raw_field": raw_field})
    unsupported = [str(s.type) for s in potential.values() if str(s.type) not in {"Bond_Double", "Atom_Tetrahedral"}]
    return rows, sorted(set(unsupported))


def _stage(source, stage):
    from rdkit import Chem
    path = Path(source["path"])
    if source["bytes"] > MAX_BYTES:
        raise ValueError("SDF exceeds qualification size bound")
    data = path.read_bytes()
    if {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} != _pin(source):
        raise ValueError("source bytes changed")
    text = data.decode("utf-8-sig").replace("\r\n", "\n")
    blocks = [p.removeprefix("\n") if i else p for i, p in enumerate(text.split("$$$$")) if p.strip()]
    index = stage.get("record_index", 0)
    if type(index) is not int or not 0 <= index < len(blocks):
        raise ValueError("explicit SDF record index outside source")
    block = blocks[index]
    raw = _raw(block)
    mol = Chem.MolFromMolBlock(block, removeHs=False, sanitize=True, strictParsing=True)
    if mol is None:
        raise ValueError("unreadable SDF record")
    features, unsupported = _inventory(mol, raw, stage["origin"])
    graph = _neutral(mol)
    atoms = [{"index": a.GetIdx(), "element": a.GetSymbol(), "isotope": a.GetIsotope(),
              "formal_charge": a.GetFormalCharge(), "radicals": a.GetNumRadicalElectrons(),
              "aromatic": a.GetIsAromatic(), "atom_map": a.GetAtomMapNum(),
              "total_h": a.GetTotalNumHs(includeNeighbors=True),
              "coordinates_A": list(mol.GetConformer().GetAtomPosition(a.GetIdx()))}
             for a in mol.GetAtoms()]
    default_h = Chem.RemoveHs(mol)
    # Locate retained H by tagging an in-memory copy; output indices refer to the original record.
    tagged = Chem.Mol(mol)
    for a in tagged.GetAtoms():
        a.SetIntProp("_qualification_index", a.GetIdx())
    retained_h = []
    for atom in Chem.RemoveHs(tagged).GetAtoms():
        if atom.GetAtomicNum() != 1:
            continue
        i = atom.GetIntProp("_qualification_index")
        defines_stereo = any(i in list(b.GetStereoAtoms()) for b in mol.GetBonds())
        retained_h.append({"index": i, "isotope": atom.GetIsotope(),
                           "reason": "isotopic_hydrogen" if atom.GetIsotope() else
                                     "defines_double_bond_stereo" if defines_stereo else "other_RDKit_protected_H"})
    return {"name": stage["name"], "source_id": source["id"], "source_identity": _pin(source),
            "origin": stage["origin"], "record_index": index, "record_count": len(blocks),
            "record_text_sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
            "graph_chemical_state": {"canonical_smiles_without_stereo": Chem.MolToSmiles(graph, isomericSmiles=True),
                                     "policy": "stereo_and_map_neutral; isotope_charge_radical_total_H_preserved"},
            "strict_representation_signature": Chem.MolToSmiles(default_h, isomericSmiles=True),
            "default_remove_hs_retained": retained_h, "atoms": atoms,
            "raw_molfile": raw, "features": features, "unsupported_stereo_types": unsupported}, mol


def _feature_changes(left, right, mapping):
    changes = []
    visited = set()
    lookup = {(f["kind"], tuple(sorted(f["atoms"]))): f for f in right["features"]}
    for feature in left["features"]:
        if not all(i in mapping for i in feature["atoms"]):
            continue
        other = lookup.get((feature["kind"], tuple(sorted(mapping[i] for i in feature["atoms"]))))
        if other is None:
            changes.append({"left_atoms": feature["atoms"], "event": "feature_not_corresponding"})
            continue
        visited.add((other["kind"], tuple(sorted(other["atoms"]))))
        a, b = feature["information"], other["information"]
        if feature["applicability"] != other["applicability"]:
            event = "feature_applicability_changed"
        elif a["state"] == b["state"] == "resolved" and a["configuration"] != b["configuration"]:
            event = "resolved_configuration_change"
        elif a["state"] != b["state"]:
            event = "stereo_information_transition"
        else:
            event = "no_information_change"
        ga, gb = feature["coordinate_perception"]["configuration"], other["coordinate_perception"]["configuration"]
        changes.append({"kind": feature["kind"], "left_atoms": feature["atoms"], "right_atoms": other["atoms"],
                        "before": a, "after": b, "event": event,
                        "coordinate_configuration_change": ga != gb if ga and gb else None})
    for key, feature in lookup.items():
        if key not in visited:
            changes.append({"right_atoms": feature["atoms"], "event": "feature_not_corresponding"})
    return changes


def _transition(left, right, left_mol, right_mol):
    from rdkit import Chem
    result = {"producer": left["name"], "consumer": right["name"],
              "graph_state_compatible": left["graph_chemical_state"] == right["graph_chemical_state"],
              "strict_representation_equal": left["strict_representation_signature"] == right["strict_representation_signature"]}
    if not result["graph_state_compatible"]:
        return {**result, "atom_mapping": None, "feature_transitions": [], "stereo_mapping_consistent": None}
    a, b = _neutral(left_mol), _neutral(right_mol)
    matches = b.GetSubstructMatches(a, uniquify=False, useChirality=False, maxMatches=MAX_MAPPINGS)
    if not matches:
        raise ValueError("graph identity without atom correspondence")
    comparisons = []
    mappings = []
    for match in sorted(matches):
        mapping = {a.GetAtomWithIdx(i).GetIntProp("_qualification_index"):
                   b.GetAtomWithIdx(j).GetIntProp("_qualification_index") for i, j in enumerate(match)}
        mappings.append(mapping)
        comparisons.append(_feature_changes(left, right, mapping))
    signatures = {canonical([{k: r.get(k) for k in ("left_atoms", "before", "after", "event", "coordinate_configuration_change")}
                             for r in rows]) for rows in comparisons}
    return {**result, "atom_mapping": sorted(mappings[0].items()), "mapping_count": len(matches),
            "mapping_limit_reached": len(matches) == MAX_MAPPINGS,
            "mapping_choice": "lexicographically_first; all_found_mappings_checked_for_stereo_consistency",
            "stereo_mapping_consistent": len(signatures) == 1 and len(matches) < MAX_MAPPINGS,
            "feature_transitions": comparisons[0]}


def _eligibility(endpoint, reference, prediction, transition):
    unresolved = [f for f in reference["features"] if f["applicability"] == "potential_stereogenic"
                  and (f["information"]["state"] != "resolved" or reference["origin"] != "source")]
    if not transition["graph_state_compatible"]:
        status = "graph_chemical_state_mismatch"
    elif unresolved:
        status = "unresolved_reference_comparability"
    elif reference["unsupported_stereo_types"] or prediction["unsupported_stereo_types"]:
        status = "unsupported_stereo_scope"
    elif not transition["stereo_mapping_consistent"]:
        status = "ambiguous_atom_correspondence"
    elif any(r["event"] in {"feature_not_corresponding", "feature_applicability_changed"}
             for r in transition["feature_transitions"]):
        status = "unresolved_feature_applicability"
    elif any(r["event"] == "resolved_configuration_change" for r in transition["feature_transitions"]):
        status = "resolved_stereochemistry_mismatch"
    elif any(f["applicability"] == "potential_stereogenic" and f["information"]["state"] != "resolved"
             for f in prediction["features"]):
        status = "unresolved_prediction_configuration"
    else:
        status = "identity_comparable_under_declared_policy"
    return {"endpoint": endpoint["name"], "policy": POLICY, "status": status,
            "identity_comparable": status == "identity_comparable_under_declared_policy",
            "unresolved_reference_features": [{"kind": f["kind"], "atoms": f["atoms"], "information": f["information"]}
                                              for f in unresolved],
            "reference_comparison": transition, "endpoint_evaluated": False, "endpoint_value": None,
            "reason": "Qualification is identity/comparability evidence only; existing endpoint decisions are not replaced."}


@bounded_read
def build_input_qualification(bundle, *, stages, endpoint, workspace, purpose="research"):
    """Read pinned originals; return an optional scientific-input qualification.

    stages: [{name, source_id, origin: source|generated|native_output, record_index}].
    endpoint: {name, policy: strict_full_identity/1, reference, prediction}.
    Source rows extend scientific_input/1 with evidence_ref and, when the Artifact
    is a manifest/diagnostic, identity_pointer (JSON keys/indices selecting a pin).
    """
    if purpose not in {"research", "conditioning", "evaluation"}:
        raise ValueError("unsupported qualification purpose")
    bundle, stages, endpoint = copy.deepcopy((bundle, stages, endpoint))
    validate_input(bundle)
    if not 1 <= len(stages) <= 64 or endpoint.get("policy") != POLICY or not endpoint.get("name"):
        raise ValueError("bounded stages and explicit strict endpoint policy required")
    sources = {s["id"]: s for s in bundle["sources"]}
    names = [s.get("name") for s in stages]
    if len(set(names)) != len(names) or not all(isinstance(n, str) and n for n in names):
        raise ValueError("unique stage names required")
    if endpoint.get("reference") not in names or endpoint.get("prediction") not in names:
        raise ValueError("endpoint must identify reference and prediction stages")
    for stage in stages:
        if stage.get("origin") not in {"source", "generated", "native_output"} or stage.get("source_id") not in sources:
            raise ValueError("stage needs source and explicit coordinate origin")
    _bundle_access(bundle, purpose)
    provenance = _sources(bundle, workspace, purpose)
    from rdkit import Chem, rdBase
    decoded = [_stage(sources[s["source_id"]], s) for s in stages]
    rows = [r for r, _ in decoded]
    transitions = [_transition(*[decoded[i - 1][0], decoded[i][0], decoded[i - 1][1], decoded[i][1]])
                   for i in range(1, len(decoded))]
    ri, pi = names.index(endpoint["reference"]), names.index(endpoint["prediction"])
    eligibility = _eligibility(endpoint, rows[ri], rows[pi],
                               _transition(rows[ri], rows[pi], decoded[ri][1], decoded[pi][1]))
    validate_input(bundle)  # catch mutation during the read; original bytes never rewritten
    return {"schema": SCHEMA, "task_id": bundle["task_id"], "scope": bundle["scope"], "purpose": purpose,
            "input_sha256": digest(bundle), "evidence_refs": [p["evidence_ref"] for p in provenance],
            "source_verification": provenance, "stages": rows, "transitions": transitions,
            "endpoint_eligibility": eligibility,
            "parser": {"name": "RDKit", "version": rdBase.rdkitVersion, "module_path": Chem.__file__,
                       "removeHs": False, "sanitize": True, "strictParsing": True,
                       "default_remove_defining_bond_stereo": Chem.RemoveHsParameters().removeDefiningBondStereo,
                       "H_policy": "parse_all_H; default_RemoveHs_signature_reported_separately; no_H_generation",
                       "index_convention": "zero_based; raw_lines_one_based"},
            "request": {"bundle": bundle, "stages": stages, "endpoint": endpoint, "purpose": purpose},
            "limitations": ["local reanalysis, not independent validation or native execution",
                            "coordinate origin is caller-declared; parser cannot authenticate experimental provenance",
                            "V2000 tetrahedral and double-bond adapter; no energy, biological-state or kinetic assertion",
                            "graph compatibility never repairs missing reference configuration"],
            "originals_unchanged": True}


def write_input_qualification(path, bundle, *, stages, endpoint, workspace, purpose="research"):
    """Write a derived file and return an ordinary Artifact; never create a store.

    The caller may record the returned Artifact in an existing ResearchJournal.
    Reusing identical bytes is idempotent; different prior output is preserved.
    """
    result = build_input_qualification(bundle, stages=stages, endpoint=endpoint, workspace=workspace, purpose=purpose)
    path = Path(path).absolute()
    data = (canonical(result) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("preserve existing qualification output")
    role, source_kind = _report_policy(result)
    return Artifact(str(path), **identity(path), source_kind=source_kind,
                    role=role,
                    bindings={"schema": SCHEMA, "task_id": bundle["task_id"], "scope": bundle["scope"],
                              "evidence_refs": result["evidence_refs"], "input_sha256": result["input_sha256"]})


@bounded_read
def read_input_qualification(artifact, *, workspace, purpose="research", resolver=None):
    """Verify output bytes and recompute source-bound claims; reject edited verdicts.

    Requalification is tied to the current parser version. A different version
    requires a new derived Artifact, never replacement of the historical report.
    """
    if purpose not in {"research", "conditioning", "evaluation"}:
        raise ValueError("unsupported qualification purpose")
    body = artifact.as_dict() if isinstance(artifact, Artifact) else artifact
    path = resolve_artifact(body, resolver=resolver)
    if body["bytes"] > 32 * MAX_BYTES:
        raise ValueError("qualification exceeds read bound")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != SCHEMA:
        raise ValueError("unsupported input qualification schema")
    request = report["request"]
    checked = build_input_qualification(**request, workspace=workspace)
    if canonical(report) != canonical(checked):
        raise ValueError("qualification claims differ from pinned sources/current parser")
    expected_bindings = {"schema": SCHEMA, "task_id": report["task_id"], "scope": report["scope"],
                         "evidence_refs": report["evidence_refs"], "input_sha256": report["input_sha256"]}
    if body.get("bindings") != expected_bindings:
        raise ValueError("qualification Artifact bindings differ from source lineage")
    expected_role, expected_source_kind = _report_policy(report)
    if body.get("role") != expected_role:
        raise ValueError("qualification Artifact role differs from source lineage")
    if body.get("source_kind") != expected_source_kind:
        raise ValueError("qualification Artifact source kind differs from source lineage")
    if purpose != request["purpose"]:
        _bundle_access(request["bundle"], purpose)
        _sources(request["bundle"], workspace, purpose)
    if identity(path) != _pin(body):
        raise ValueError("qualification changed during read")
    return report
