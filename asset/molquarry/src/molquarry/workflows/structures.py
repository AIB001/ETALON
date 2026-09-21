"""Experimental structures and target-specific, residue-mapped rigid-body alignment."""

import hashlib
import json
import string
from itertools import islice
from pathlib import Path

from ..errors import MolQuarryError


def kabsch(mobile, reference):
    import numpy as np

    a, b = np.asarray(mobile, dtype=float), np.asarray(reference, dtype=float)
    if a.shape != b.shape or len(a) < 3 or a.shape[1] != 3:
        raise ValueError("At least three paired coordinates are required")
    ac, bc = a.mean(axis=0), b.mean(axis=0)
    if np.linalg.matrix_rank(a - ac) < 2 or np.linalg.matrix_rank(b - bc) < 2:
        raise ValueError("Collinear coordinates cannot establish an alignment")
    u, _, vt = np.linalg.svd((a - ac).T @ (b - bc))
    correction = np.eye(3)
    correction[-1, -1] = np.linalg.det(u @ vt)
    rotation = u @ correction @ vt
    translation = bc - ac @ rotation
    rmsd = float(np.sqrt(np.mean(np.sum((a @ rotation + translation - b) ** 2, axis=1))))
    return rotation, translation, rmsd


def pdb_export(structure, path):
    """Reject unrepresentable legacy PDB fields instead of truncating atoms or identifiers."""
    chain_names = sorted({chain.name for model in structure for chain in model})
    if len(chain_names) > 62:
        raise ValueError("Legacy PDB supports at most 62 single-character chains")
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
    mapping = {name: name for name in chain_names if len(name) == 1 and name in alphabet}
    available = iter(c for c in alphabet if c not in mapping.values())
    for name in chain_names:
        if name not in mapping:
            mapping[name] = next(available)
    copied = structure.clone()
    for model in copied:
        if model.count_atom_sites() > 99999:
            raise ValueError("Legacy PDB atom serial limit exceeded")
        for chain in model:
            chain.name = mapping[chain.name]
            for residue in chain:
                if len(residue.name) > 3 or not -999 <= residue.seqid.num <= 9999:
                    raise ValueError("Residue name or number exceeds legacy PDB field limits")
                for atom in residue:
                    coords = [atom.pos.x, atom.pos.y, atom.pos.z]
                    if any(not -999.999 <= x <= 9999.999 for x in coords):
                        raise ValueError("Coordinates exceed legacy PDB field limits")
                    if atom.b_iso > 999.99:
                        raise ValueError("B-factor exceeds legacy PDB field limits")
    with Path(path).open("x", encoding="utf-8") as f:
        f.write(copied.make_pdb_string())
    return mapping


def sifts_regions(entity, accession):
    return [
        region
        for alignment in entity.get("rcsb_polymer_entity_align", [])
        if alignment.get("reference_database_accession") == accession
        and alignment.get("reference_database_name") == "UniProt"
        for region in alignment.get("aligned_regions", [])
    ]


def sequence_residue_map(entity, accession, sequence):
    """Correct coarse SIFTS ranges for construct deletions and ambiguous gap placement."""
    from Bio.Align import PairwiseAligner, substitution_matrices

    regions = sifts_regions(entity, accession)
    if not regions:
        raise ValueError("No SIFTS mapping to the requested accession")
    entity_sequence = "".join(
        entity.get("entity_poly", {}).get("pdbx_seq_one_letter_code_can", "").split()
    )
    if not entity_sequence or not sequence:
        raise ValueError("Canonical target and entity sequences are required for alignment")
    allowed = {
        i
        for region in regions
        for i in range(region["entity_beg_seq_id"], region["entity_beg_seq_id"] + region["length"])
    }
    start, end = min(allowed) - 1, max(allowed)
    if end > len(entity_sequence):
        raise ValueError("SIFTS entity range exceeds the source sequence")
    fragment = entity_sequence[start:end]
    aligner = PairwiseAligner()
    aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
    aligner.open_gap_score = -10
    aligner.extend_gap_score = -0.5
    aligner.end_gap_score = 0
    alternatives = list(islice(aligner.align(sequence, fragment), 33))
    if not alternatives or len(alternatives) > 32:
        raise ValueError("Sequence mapping is excessively ambiguous (>32 optimal alignments)")
    mappings = []
    for alignment in alternatives:
        mappings.append(
            {
                int(j) + start + 1: int(i) + 1
                for i, j in zip(*alignment.indices, strict=True)
                if i >= 0 and j >= 0 and int(j) + start + 1 in allowed
            }
        )
    # Only mappings shared by every equally optimal alignment are used in the fit.
    mapping = {
        key: value
        for key, value in mappings[0].items()
        if all(other.get(key) == value for other in mappings[1:])
    }
    if not mapping:
        raise ValueError("Sequence alignment has no unambiguous residue mapping")
    identity = sum(entity_sequence[j - 1] == sequence[i - 1] for j, i in mapping.items()) / len(
        mapping
    )
    if identity < 0.9:
        raise ValueError(f"Entity-to-target sequence identity too low ({identity:.1%})")
    return mapping, {
        "method": "SIFTS-linked, sequence-validated residue correspondence",
        "sequence_identity": identity,
        "optimal_alignments": len(alternatives),
        "ambiguous_positions_excluded": len(set().union(*mappings)) - len(mapping),
        "entity_to_uniprot": mapping,
        "algorithm": "BLOSUM62; gap open -10, extend -0.5; free end gaps; consensus mapping",
    }


def mapped_ca(chain, entity, accession, residue_map=None):
    regions = sifts_regions(entity, accession)
    if residue_map is None:
        residue_map = {
            region["entity_beg_seq_id"] + offset: region["ref_beg_seq_id"] + offset
            for region in regions
            for offset in range(region["length"])
        }
    positions = {}
    entity_id = entity["rcsb_polymer_entity_container_identifiers"]["entity_id"]
    for residue in chain:
        if residue.entity_id != entity_id or residue.label_seq is None:
            continue
        if residue.label_seq in residue_map:
            atoms = [a for a in residue if a.name == "CA" and a.element.name == "C"]
            if atoms:
                atom = max(atoms, key=lambda a: (a.occ, a.altloc in {"\x00", "A"}))
                positions[residue_map[residue.label_seq]] = [atom.pos.x, atom.pos.y, atom.pos.z]
    return positions


def export_structures(quarry, dossier, root, *, max_structures=None, max_total_bytes=1_000_000_000):
    import gemmi
    import numpy as np

    root = Path(root).resolve()
    source_dir, pdb_dir, aligned_dir = [root / p for p in ["source_mmcif", "pdb", "aligned"]]
    for directory in [source_dir, pdb_dir, aligned_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    metadata = {row["rcsb_id"]: row for row in dossier["structures"]}
    entities = {row["rcsb_id"]: row for row in dossier["polymer_entities"]}
    index, views, structures, queries = [], [], {}, []
    consumed = 0
    sequences, sequence_errors, mappings = {}, {}, {}
    for target in dossier["targets"]:
        accession = target["accession"]
        try:
            sequence = target.get("sequence")
            if not sequence:
                response = quarry.query("uniprot", "entry", accession=accession)
                queries.append(response.model_dump())
                sequence = response.records[0]["sequence"]["value"]
            if target.get("sequence_sha256") and (
                hashlib.sha256(sequence.encode()).hexdigest() != target["sequence_sha256"]
            ):
                raise ValueError("Target sequence changed since evidence collection")
            sequences[accession] = sequence
        except (MolQuarryError, ValueError, KeyError, IndexError) as exc:
            sequence_errors[accession] = str(exc)
    identifiers = sorted(dossier["structure_hits"])
    for number, pdb in enumerate(identifiers):
        item = {
            "pdb_id": pdb,
            "targets": dossier["structure_hits"][pdb],
            "resolution_angstrom": None,
            "method": [],
            "status": "pending",
        }
        index.append(item)
        if max_structures is not None and number >= max_structures:
            item.update(status="limited", reason="Explicit structure count limit")
            continue
        try:
            if pdb not in metadata:
                response = quarry.query("rcsb", "entry", pdb_id=pdb)
                queries.append(response.model_dump())
                metadata[pdb] = response.records[0]
            entry = metadata[pdb]
            item.update(
                resolution_angstrom=entry.get("rcsb_entry_info", {}).get("resolution_combined"),
                method=[x["method"] for x in entry.get("exptl", [])],
                title=entry.get("struct", {}).get("title"),
            )
            for eid in entry.get("rcsb_entry_container_identifiers", {}).get(
                "polymer_entity_ids", []
            ):
                key = f"{pdb}_{eid}"
                if key not in entities:
                    response = quarry.query("rcsb", "polymer_entity", pdb_id=pdb, entity_id=eid)
                    queries.append(response.model_dump())
                    entities[key] = response.records[0]
            if consumed >= max_total_bytes:
                item.update(status="limited", reason="Total download byte budget reached")
                continue
            plan = quarry.plan_download("rcsb", "structure", pdb_id=pdb)
            downloaded = quarry.download(
                plan, output_dir=source_dir, max_bytes=min(100_000_000, max_total_bytes - consumed)
            )
            consumed += downloaded.bytes
            structure = gemmi.read_structure(downloaded.path)
            structures[pdb] = structure
            item.update(
                source_file=str(Path(downloaded.path).relative_to(root)),
                model_count=len(structure),
                downloaded_bytes=downloaded.bytes,
            )
            try:
                item["chain_map"] = pdb_export(structure, pdb_dir / f"{pdb}.pdb")
                item.update(status="pdb_written", pdb_file=f"pdb/{pdb}.pdb")
            except ValueError as exc:
                item.update(status="mmcif_only", reason=str(exc))
            for target in dossier["targets"]:
                if target["accession"] not in sequences:
                    item.setdefault("alignment_errors", []).append(
                        {
                            "accession": target["accession"],
                            "reason": sequence_errors[target["accession"]],
                        }
                    )
                    continue
                matching = [
                    e
                    for k, e in entities.items()
                    if k.startswith(pdb + "_")
                    and target["accession"]
                    in e["rcsb_polymer_entity_container_identifiers"].get("uniprot_ids", [])
                ]
                valid_entities = []
                for entity in matching:
                    key = (entity["rcsb_id"], target["accession"])
                    try:
                        if key not in mappings:
                            mappings[key] = sequence_residue_map(
                                entity, target["accession"], sequences[target["accession"]]
                            )
                        valid_entities.append(entity)
                    except ValueError as exc:
                        item.setdefault("alignment_errors", []).append(
                            {
                                "entity": entity["rcsb_id"],
                                "accession": target["accession"],
                                "reason": str(exc),
                            }
                        )
                for model_number, model in enumerate(structure):
                    chains = []
                    for entity in valid_entities:
                        residue_map, mapping_info = mappings[
                            (entity["rcsb_id"], target["accession"])
                        ]
                        allowed = entity["rcsb_polymer_entity_container_identifiers"].get(
                            "auth_asym_ids", []
                        )
                        for chain in model:
                            if chain.name in allowed:
                                coordinates = mapped_ca(
                                    chain, entity, target["accession"], residue_map
                                )
                                if coordinates:
                                    chains.append(
                                        (chain.name, coordinates, mapping_info, entity["rcsb_id"])
                                    )
                    if not chains:
                        continue
                    chain_name, coords, mapping_info, entity_id = max(
                        chains, key=lambda x: len(x[1])
                    )
                    views.append(
                        {
                            "pdb_id": pdb,
                            "model_index": model_number,
                            "chain": chain_name,
                            "target": target["gene"],
                            "accession": target["accession"],
                            "coords": coords,
                            "entity": entity_id,
                            "sequence_mapping": mapping_info,
                        }
                    )
        except (MolQuarryError, ValueError, RuntimeError) as exc:
            item.update(status="error", reason=str(exc))

    alignments = []
    for target in dossier["targets"]:
        target_views = sorted(
            [v for v in views if v["accession"] == target["accession"]],
            key=lambda v: (-len(v["coords"]), v["pdb_id"], v["model_index"]),
        )
        anchors = []
        for view in target_views:
            overlaps = [
                (len(set(view["coords"]) & set(a["coords"])), n, a) for n, a in enumerate(anchors)
            ]
            overlap, group, reference = (
                max(overlaps, key=lambda x: x[0]) if overlaps else (0, 0, None)
            )
            if overlap < 10:
                anchors.append(view)
                group, reference = len(anchors) - 1, view
            common = sorted(set(view["coords"]) & set(reference["coords"]))
            record = {k: v for k, v in view.items() if k != "coords"}
            record.update(
                group=group + 1,
                reference_pdb=reference["pdb_id"],
                reference_chain=reference["chain"],
                reference_model_index=reference["model_index"],
                matched_ca=len(common),
                mapping="SIFTS-linked, sequence-validated residue correspondence",
                uniprot_positions=common,
                status="pending",
            )
            alignments.append(record)
            try:
                rotation, translation, rmsd = kabsch(
                    [view["coords"][i] for i in common], [reference["coords"][i] for i in common]
                )
                original = structures[view["pdb_id"]]
                aligned = gemmi.Structure()
                aligned.name = original.name
                aligned.add_model(original[view["model_index"]].clone())
                for chain in aligned[0]:
                    for residue in chain:
                        for atom in residue:
                            xyz = (
                                np.array([atom.pos.x, atom.pos.y, atom.pos.z]) @ rotation
                                + translation
                            )
                            atom.pos = gemmi.Position(*xyz)
                folder = aligned_dir / target["accession"] / f"group_{group + 1:02d}"
                folder.mkdir(parents=True, exist_ok=True)
                filename = f"{view['pdb_id']}_model{view['model_index'] + 1}.pdb"
                chain_map = pdb_export(aligned, folder / filename)
                record.update(
                    status="reference" if reference is view else "aligned",
                    rmsd_angstrom=rmsd,
                    high_rmsd=rmsd > 5,
                    rotation_row_vector=rotation.tolist(),
                    translation=translation.tolist(),
                    chain_map=chain_map,
                    file=str((folder / filename).relative_to(root)),
                )
            except (ValueError, RuntimeError) as exc:
                record.update(status="unalignable", reason=str(exc))
        if not target_views:
            alignments.append(
                {
                    "accession": target["accession"],
                    "target": target["gene"],
                    "status": "no_mapped_coordinates",
                }
            )
    report = {
        "structures": index,
        "alignments": alignments,
        "downloaded_bytes": consumed,
        "metadata_queries": queries,
        "policy": "Rigid C-alpha fit by sequence-validated canonical residue overlap. "
        "Construct indels are aligned; ambiguous sequence positions are excluded. "
        "Disjoint domains use separate "
        "reference groups. All original models are retained; one target chain per model "
        "defines each transformed complex view. High RMSD is reported, not hidden.",
    }
    (root / "index.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
