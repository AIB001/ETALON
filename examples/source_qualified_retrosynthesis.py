"""Explicit catalog-supported terminal stocks for ordinary route searches.

This example validates supplied evidence and writes AiZynthFinder's existing
InChIKey text-stock format. It does not look up vendors, infer procurement,
modify a baseline stock, validate reactions, or execute a global workflow.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rdkit import Chem


def write_catalog_stock(entries: list[dict], destination: str | Path) -> dict:
    """Materialize only exact identities supported by retained vendor bytes.

    Each entry contains smiles, inchikey, catalog_listing_supported, and a
    source with kind, url, path, sha256, product_id and identity_token. The
    supplied evidence token must be the full InChIKey. This narrow example
    intentionally refuses name-only evidence or implicit identity guesses.
    """
    if not entries:
        raise ValueError("an empty catalog overlay cannot qualify a route")
    rows = []
    for entry in entries:
        if entry.get("catalog_listing_supported") is not True:
            raise ValueError("catalog listing is not supported")
        mol = Chem.MolFromSmiles(entry["smiles"])
        if mol is None:
            raise ValueError("invalid molecular identity")
        key = Chem.MolToInchiKey(mol)
        source = dict(entry["source"])
        if key != entry["inchikey"] or source.get("identity_token") != key:
            raise ValueError("molecule and vendor identity disagree")
        if source.get("kind") != "manufacturer_catalog" or not source.get("product_id"):
            raise ValueError("a primary catalog product is required")
        if not str(source.get("url", "")).startswith("https://"):
            raise ValueError("a source URL is required")
        payload = Path(source["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise ValueError("retained supplier bytes changed")
        if key.encode() not in payload or str(source["product_id"]).encode() not in payload:
            raise ValueError("retained supplier bytes do not contain the asserted identity/product")
        rows.append({"smiles": Chem.MolToSmiles(mol), "inchikey": key, "source": source})
    keys = sorted({row["inchikey"] for row in rows})
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(keys) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(payload)
    return {
        "schema": "catalog-terminal-stock/1", "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(), "molecule_count": len(keys),
        "entries": rows, "availability_scope": "catalog_listing",
        "live_inventory_confirmed": False, "procurement_confirmed": False,
        "reaction_viability_validated": False, "baseline_stock_modified": False,
    }


def summarize_native_record(record: dict) -> dict:
    """Expose a few ordinary action observables without dropping native trees."""
    solved = record.get("is_solved")
    if not isinstance(solved, bool):
        raise ValueError("native is_solved must be a JSON boolean; missing/invalid is unknown")
    trees = record.get("trees", [])
    top = trees[0] if trees else None
    unresolved = []
    if top:
        stack = [top]
        while stack:
            node = stack.pop()
            children = node.get("children", [])
            if node.get("type") == "mol" and not children and not node.get("in_stock"):
                unresolved.append(node["smiles"])
            stack.extend(reversed(children))
    return {
        "stock_closed": solved,
        "unresolved_count": len(unresolved) if top else None,
        "unresolved_leaf_smiles": unresolved,
        "unresolved_scope": "top_ranked_exported_native_tree",
        "search_seconds": record.get("search_time"),
        "iteration_count": record.get("profiling", {}).get("iterations"),
        "exported_tree_count": len(trees),
        "reaction_viability_unknown": True, "lab_synthesis_validated": False,
    }
