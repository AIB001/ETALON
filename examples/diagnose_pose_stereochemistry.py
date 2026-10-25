"""Read-only, explicitly scoped SDF representation diagnostics.

This optional example reports differences, never accepts a pose or computes a
scientific endpoint. Stereo-erased copies and molfile flag counterfactuals are
diagnostic controls only; originals and their evaluation contracts stay intact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def identity(path):
    data = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def records(path):
    """Retain the three-line molfile header, including intentionally blank lines."""
    text = Path(path).read_text(encoding="utf-8")
    parts = text.split("$$$$")
    return [part.removeprefix("\n") if i else part for i, part in enumerate(parts)
            if part.strip()]


def molfile_fields(block):
    lines = block.splitlines()
    if len(lines) < 4 or "V2000" not in lines[3]:
        raise ValueError("this diagnostic's raw-field inspection requires V2000")
    na, nb = int(lines[3][:3]), int(lines[3][3:6])
    return {"header": lines[:3], "counts": lines[3],
            "atoms": [{"index": i, "line": 5 + i, "raw": line,
                       "element": line[31:34].strip(), "isotope_mass_difference": line[34:36].strip(),
                       "charge_code": line[36:39].strip(), "parity": line[39:42].strip(),
                       "atom_map": line[60:63].strip()}
                      for i, line in enumerate(lines[4:4 + na])],
            "bonds": [{"index": i, "line": 5 + na + i, "raw": line,
                       "begin": int(line[:3]) - 1, "end": int(line[3:6]) - 1,
                       "type": int(line[6:9]), "stereo": int(line[9:12].strip() or "0")}
                      for i, line in enumerate(lines[4 + na:4 + na + nb])],
            "m_records": [s for s in lines[4 + na + nb:] if s.startswith("M  ")]}


def read_mol(block):
    from rdkit import Chem
    mol = Chem.MolFromMolBlock(block, removeHs=False, sanitize=True, strictParsing=True)
    if mol is None:
        raise ValueError("unreadable mol block")
    return mol


def signature(mol):
    from rdkit import Chem
    h = Chem.RemoveHs(mol)
    return {"isomeric_smiles_after_default_remove_hs": Chem.MolToSmiles(h, isomericSmiles=True),
            "remaining_h_indices": [a.GetIdx() for a in h.GetAtoms() if a.GetAtomicNum() == 1],
            "atoms_after_default_remove_hs": h.GetNumAtoms(),
            "double_bonds": [{"index": b.GetIdx(), "begin": b.GetBeginAtomIdx(),
                              "end": b.GetEndAtomIdx(), "stereo": str(b.GetStereo()),
                              "stereo_atoms": list(b.GetStereoAtoms())}
                             for b in mol.GetBonds() if b.GetBondType() == Chem.BondType.DOUBLE]}


def snapshot(mol, block):
    from rdkit import Chem
    return {"signature": signature(mol), "raw_molfile": molfile_fields(block),
            "is_3d": mol.GetConformer().Is3D(),
            "atoms": [{"index": a.GetIdx(), "element": a.GetSymbol(), "isotope": a.GetIsotope(),
                       "formal_charge": a.GetFormalCharge(), "map": a.GetAtomMapNum(),
                       "radicals": a.GetNumRadicalElectrons(), "aromatic": a.GetIsAromatic(),
                       "chiral_tag": str(a.GetChiralTag()),
                       "cip": a.GetProp("_CIPCode") if a.HasProp("_CIPCode") else None,
                       "implicit_h": a.GetNumImplicitHs(), "explicit_h_count": a.GetNumExplicitHs(),
                       "h_neighbors": [n.GetIdx() for n in a.GetNeighbors() if n.GetAtomicNum() == 1],
                       "total_h": a.GetTotalNumHs(includeNeighbors=True),
                       "coordinates_A": list(mol.GetConformer().GetAtomPosition(a.GetIdx()))}
                      for a in mol.GetAtoms()],
            "bonds": [{"index": b.GetIdx(), "begin": b.GetBeginAtomIdx(), "end": b.GetEndAtomIdx(),
                       "type": str(b.GetBondType()), "aromatic": b.GetIsAromatic(),
                       "stereo": str(b.GetStereo()), "stereo_atoms": list(b.GetStereoAtoms()),
                       "direction": str(b.GetBondDir())} for b in mol.GetBonds()],
            "tetrahedral_centers": Chem.FindMolChiralCenters(mol, includeUnassigned=True)}


def heavy_graph(mol):
    """Stereo-neutral copy for locating graph-corresponding atoms, never acceptance."""
    from rdkit import Chem
    copy = Chem.Mol(mol)
    for a in copy.GetAtoms():
        a.SetIntProp("_diagnostic_original_index", a.GetIdx())
        a.SetAtomMapNum(0)
    Chem.RemoveStereochemistry(copy)
    copy = Chem.RemoveHs(copy)
    if any(a.GetAtomicNum() == 1 for a in copy.GetAtoms()):
        raise ValueError("isotopic or otherwise protected H needs an explicit mapping contract")
    return copy


def correspondence(left, right):
    """Locate compatible graphs and report fields, not a stereo verdict.

    chiral_tag is a raw, neighbor-order-dependent RDKit field. Its difference
    alone cannot establish inversion: compare CIP or mapped neighbor parity
    before a chemical interpretation. The present imine diagnosis has no
    tetrahedral centers. Mapping intentionally removes stereo and map labels;
    graph equality here cannot establish complete stereochemical identity.
    """
    from rdkit import Chem
    a, b = heavy_graph(left), heavy_graph(right)
    if Chem.MolToSmiles(a) != Chem.MolToSmiles(b):
        return {"graph_equal_without_stereo": False, "atom_mapping": None}
    matches = b.GetSubstructMatches(a, uniquify=False, useChirality=False, maxMatches=100000)
    if not matches:
        raise ValueError("equal neutral graph but no atom correspondence")
    match = min(matches)
    pairs = [(a.GetAtomWithIdx(i).GetIntProp("_diagnostic_original_index"),
              b.GetAtomWithIdx(j).GetIntProp("_diagnostic_original_index")) for i, j in enumerate(match)]
    atom_fields = {"isotope": lambda x: x.GetIsotope(), "formal_charge": lambda x: x.GetFormalCharge(),
                   "atomic_number": lambda x: x.GetAtomicNum(), "aromatic": lambda x: x.GetIsAromatic(),
                   "radicals": lambda x: x.GetNumRadicalElectrons(),
                   "total_h": lambda x: x.GetTotalNumHs(includeNeighbors=True),
                   "atom_map": lambda x: x.GetAtomMapNum(), "chiral_tag": lambda x: str(x.GetChiralTag())}
    atom_diffs = [{"left_index": i, "right_index": j, "field": k, "left": f(left.GetAtomWithIdx(i)),
                   "right": f(right.GetAtomWithIdx(j))}
                  for i, j in pairs for k, f in atom_fields.items()
                  if f(left.GetAtomWithIdx(i)) != f(right.GetAtomWithIdx(j))]
    mapping = dict(pairs)
    bond_diffs = []
    for bond in left.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if i not in mapping or j not in mapping:
            continue
        other = right.GetBondBetweenAtoms(mapping[i], mapping[j])
        for name, f in {"type": lambda x: str(x.GetBondType()), "aromatic": lambda x: x.GetIsAromatic(),
                        "stereo": lambda x: str(x.GetStereo())}.items():
            if f(bond) != f(other):
                bond_diffs.append({"left_bond": bond.GetIdx(), "right_bond": other.GetIdx(),
                                   "left_atoms": [i, j], "right_atoms": [mapping[i], mapping[j]],
                                   "field": name, "left": f(bond), "right": f(other)})
    return {"graph_equal_without_stereo": True, "mapping_choice": "lexicographically first graph isomorphism; no coordinate fitting",
            "mapping_count": len(matches), "mapping_limit_reached": len(matches) == 100000,
            "atom_mapping": pairs, "atom_differences": atom_diffs, "bond_differences": bond_diffs}


def flag_counterfactual(block, bond_index, stereo):
    """Alter one raw V2000 bond flag in memory; all atomic coordinates stay exact."""
    from rdkit import Chem
    fields = molfile_fields(block)
    lines = block.splitlines(keepends=True)
    lineno = 4 + len(fields["atoms"]) + bond_index
    old = lines[lineno]
    lines[lineno] = old[:9] + f"{stereo:3d}" + old[12:]
    altered = "".join(lines)
    a, b = read_mol(block), read_mol(altered)
    if not (a.GetConformer().GetPositions() == b.GetConformer().GetPositions()).all():
        raise ValueError("counterfactual changed coordinates")
    return {"diagnostic_only": True, "bond_index": bond_index,
            "original_line": old.rstrip(), "altered_line": lines[lineno].rstrip(),
            "coordinates_unchanged": True, "signature": signature(b)}


def terminal_imine_geometry(mol):
    """Report explicit H-N=C-X dihedrals; an absent source H stays unavailable."""
    from rdkit.Chem import rdMolTransforms
    out = []
    for b in mol.GetBonds():
        if str(b.GetBondType()) != "DOUBLE":
            continue
        for n, c in ((b.GetBeginAtom(), b.GetEndAtom()), (b.GetEndAtom(), b.GetBeginAtom())):
            if n.GetAtomicNum() != 7 or c.GetAtomicNum() != 6:
                continue
            hs = [a.GetIdx() for a in n.GetNeighbors() if a.GetAtomicNum() == 1]
            other = [a for a in c.GetNeighbors() if a.GetIdx() != n.GetIdx()]
            out.append({"bond": b.GetIdx(), "nitrogen": n.GetIdx(), "carbon": c.GetIdx(),
                        "explicit_n_h": hs, "dihedrals": [
                            {"atoms": [h, n.GetIdx(), c.GetIdx(), x.GetIdx()], "terminal_element": x.GetSymbol(),
                             "degrees": rdMolTransforms.GetDihedralDeg(mol.GetConformer(), h, n.GetIdx(), c.GetIdx(), x.GetIdx())}
                            for h in hs for x in other]})
    return out


def analyze_stages(stages):
    """stages: ordered [{name, path, sha256, bytes}], all identities required."""
    from rdkit import Chem
    decoded, output = [], []
    for stage in stages:
        path = Path(stage["path"])
        pin = {key: stage[key] for key in ("sha256", "bytes")}
        if identity(path) != pin:
            raise ValueError("stage identity mismatch: " + stage["name"])
        blocks = records(path)
        mols = [read_mol(block) for block in blocks]
        rows = []
        for i, (mol, block) in enumerate(zip(mols, blocks)):
            item = snapshot(mol, block)
            item["record_index"] = i
            item["terminal_imine_geometry"] = terminal_imine_geometry(mol)
            item["flag_counterfactuals"] = [flag_counterfactual(block, b["index"], 0 if b["stereo"] == 3 else 3)
                for b in item["raw_molfile"]["bonds"] if b["type"] == 2
                and sorted([mol.GetAtomWithIdx(b[k]).GetSymbol() for k in ("begin", "end")]) == ["C", "N"]]
            neutral = Chem.Mol(mol)
            Chem.RemoveStereochemistry(neutral)
            item["stereo_erased_copy_signature_diagnostic_only"] = signature(neutral)
            rows.append(item)
        output.append({"name": stage["name"], "path": str(path), **pin, "records": rows})
        decoded.append(mols)
    comparisons = []
    for pos in range(1, len(stages)):
        left, right = decoded[pos - 1], decoded[pos]
        if len(left) != len(right) and len(left) != 1:
            raise ValueError("ambiguous record correspondence")
        for i, mol in enumerate(right):
            comparisons.append({"left_stage": stages[pos - 1]["name"], "right_stage": stages[pos]["name"],
                                "left_record": 0 if len(left) == 1 else i, "right_record": i,
                                **correspondence(left[0 if len(left) == 1 else i], mol)})
    if any(identity(s["path"]) != {k: s[k] for k in ("sha256", "bytes")} for s in stages):
        raise ValueError("source changed during diagnostics")
    return {"schema": "etalon-stereo-representation-diagnostic/1", "scope": "reanalysis; no pose acceptance, RMSD endpoint, repair, or native execution",
            "index_convention": "all atom/bond/record indices zero-based; raw molfile indices one-based",
            "stages": output, "comparisons": comparisons, "originals_unchanged": True}
