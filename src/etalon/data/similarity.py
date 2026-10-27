"""Pairwise structural neighborhood of an explicit molecular catalog.

Local fingerprints support candidate diversity decisions, not chemical identity,
binding affinity, assay equivalence or automatic selection. Original row IDs stay
separate even when fingerprints match.
"""
from __future__ import annotations


def catalog_neighbors(rows, *, id_field="id", smiles_field="smiles", radius=2,
                      bits=2048, include_chirality=True, top_k=3):
    """Parse/fingerprint each row once, then compute its exact top-k neighbors.

    Invalid SMILES remain explicit exclusions. No canonical-state standardization
    is inferred here. Same-score ties use original row order in both directions.
    """
    from rdkit import Chem, DataStructs
    from rdkit.Chem import rdFingerprintGenerator
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be positive")
    if type(radius) is not int or radius < 0 or type(bits) is not int or bits < 1:
        raise ValueError("explicit fingerprint radius and bit count required")
    rows = list(rows)
    ids = [row[id_field] for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("catalog row IDs must be nonempty and unique")
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=bits,
                                                          includeChirality=include_chirality)
    valid, excluded = [], []
    for i, row in enumerate(rows):
        smiles = row.get(smiles_field)
        molecule = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) and smiles else None
        if molecule is None:
            excluded.append({"id": ids[i], "index": i, "reason": "invalid_or_missing_smiles"})
        else:
            valid.append((i, generator.GetFingerprint(molecule)))
    fingerprints = [fp for _, fp in valid]
    results = []
    for j, (i, fp) in enumerate(valid):
        scores = DataStructs.BulkTanimotoSimilarity(fp, fingerprints)
        order = sorted((k for k in range(len(valid)) if k != j),
                       key=lambda k: (-scores[k], valid[k][0]))[:top_k]
        results.append({"id": ids[i], "neighbors": [
            {"id": ids[valid[k][0]], "similarity": float(scores[k])} for k in order]})
    return {"rows": results, "excluded": excluded, "input_count": len(rows),
            "valid_count": len(valid), "definition": {"kind": "Morgan-bit-Tanimoto",
            "radius": radius, "bits": bits, "include_chirality": include_chirality,
            "top_k": top_k, "identity_policy": "source_smiles_without_standardization"}}
