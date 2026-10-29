"""Host-selected PRISM candidates to ordinary SDF inputs, retaining native poses."""
from __future__ import annotations
import json
import re
from pathlib import Path, PurePosixPath

from etalon.active.schema import digest
from etalon.runtime.files import _save, file_identity, _consumer_lock
from .feedback import candidate_rows


def _locate(path, locations):
    source = str(path).replace("\\", "/")
    for old, new in sorted(locations.items(), key=lambda item: -len(item[0])):
        prefix = old.replace("\\", "/").rstrip("/")
        if source == prefix or source.startswith(prefix + "/"):
            suffix = PurePosixPath(source[len(prefix):].lstrip("/"))
            if ".." in suffix.parts:
                raise ValueError("invalid relocated source path")
            return Path(new).joinpath(*suffix.parts).absolute()
    return Path(path).absolute()


def prepare_handoff(manifests, destination, *, operation, candidates=None, locations=None, include_scaffolds=False):
    """Prepare inputs for a chosen operation; no docking, generation or MD runs.

    operation is inspect_pose, redock or properties. Source coordinates are
    retained in all cases; redock explicitly permits the downstream method to
    replace them. Candidate identity is manifest hash/model/native candidate ID.
    Unknown stereo and native quality annotations are carried, never promoted.
    """
    if operation not in {"inspect_pose", "redock", "properties"}:
        raise ValueError("host must choose inspect_pose, redock or properties")
    from rdkit import Chem
    locations = locations or {}
    wanted = set(candidates) if candidates is not None else None
    if candidates is not None and (not candidates or len(wanted) != len(candidates)):
        raise ValueError("select distinct candidate keys, or omit selection for all delivered candidates")
    root = Path(destination).absolute()
    receipt = root / "handoff.json"
    input_key = digest({"handoff_layout": 4, "include_scaffolds": include_scaffolds,
        "manifests": sorted({file_identity(p)["sha256"] for p in manifests}),
        "operation": operation, "candidates": sorted(wanted) if wanted is not None else None})
    if receipt.exists():
        prior = json.loads(receipt.read_text(encoding="utf-8"))
        if prior.get("input_key") != input_key:
            raise ValueError("handoff destination belongs to another selection; choose a new directory")
        for pin in prior["outputs"].values():
            if file_identity(pin["path"]) != {k: pin[k] for k in ("sha256", "bytes")}:
                raise ValueError("completed handoff output changed")
        records = json.loads(Path(prior["outputs"]["candidates"]["path"]).read_text(encoding="utf-8"))
        for row in records["selection"]:
            path = _locate(row["native_path"], locations)
            if file_identity(path) != {k: row["source"][k] for k in ("sha256", "bytes")}:
                raise ValueError("native source changed; prepare a new handoff")
        return {**prior, "reused": True}
    selected, skipped, blocks, sources, seen = [], [], [], [], set()
    for pin, manifest, model, run in candidate_rows(manifests):
        if pin not in sources:
            sources.append(pin)
        for index, candidate in enumerate(run.get("candidates", [])):
            key = pin["sha256"] + "/" + model + "/" + candidate["candidate_id"]
            if key in seen or wanted is not None and key not in wanted:
                continue
            seen.add(key)
            source = _locate(candidate["path"], locations)
            text = source.read_text(encoding="utf-8")
            mol = Chem.MolFromMolBlock(text.split("$$$$")[0], removeHs=False, sanitize=True)
            if mol is None or not mol.GetNumConformers():
                skipped.append({"candidate": key, "reason": "unreadable molecular structure", "source": str(source)})
                continue
            actual_smiles = Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)
            expected = candidate.get("canonical_smiles")
            declared = Chem.MolFromSmiles(expected) if expected else None
            if declared is not None and Chem.MolToSmiles(declared, isomericSmiles=True) != actual_smiles:
                raise ValueError("molecular identity differs from native manifest: " + key)
            identity = {"path": str(source), **file_identity(source)}
            partition = {"model": model, "generation_request": manifest.get("request_hash"),
                         "run_manifest_sha256": pin["sha256"], "run_manifest_path": pin["path"],
                         "native_candidate_id": candidate["candidate_id"], "conditioning_ref": "/pocket"}
            if include_scaffolds:
                from rdkit.Chem.Scaffolds import MurckoScaffold
                try:
                    partition["murcko_smiles"] = MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=False)
                except (RuntimeError, ValueError) as error:
                    partition.update(murcko_smiles=None, scaffold_error=str(error)[:200])
            label = "generated-" + digest(key)[:24]
            lines = text.split("$$$$")[0].splitlines()
            lines[0] = label
            # SD property values require a blank line before the record delimiter.
            # Keep native property text; only replace the record's display name.
            # These tags go through MolCascade's existing SDF source and
            # parent_source_map, so deduplication keeps every occurrence's origin.
            tags = {"ETALON_OCCURRENCE": label,
                    "ETALON_PARTITION": json.dumps(partition, sort_keys=True),
                    "ETALON_NATIVE_QUALITY": candidate.get("audit_status", "unknown")}
            kept, skip = [], False
            for line in lines:
                match = re.match(r"^>\s*<([^>]+)>", line)
                if match:
                    skip = match.group(1) in tags
                if not skip:
                    kept.append(line)
                if not line.strip():
                    skip = False
            tag_text = "".join(f">  <{name}>\n{value}\n\n" for name, value in tags.items())
            blocks.append("\n".join(kept).rstrip("\r\n") + "\n\n" + tag_text + "$$$$\n")
            selected.append({"id": label, "candidate": key, "model": model, "partition": partition,
                "smiles": actual_smiles, "source": identity, "native_path": candidate["path"],
                "manifest_sha256": pin["sha256"], "request_hash": manifest.get("request_hash"),
                "coordinate_frame_ref": "/pocket",
                "quality_ref": f"/models/{model}/candidates/{index}/quality",
                "native_quality_status": candidate.get("audit_status"),
                "pose_policy": "preserved; downstream redocking requested" if operation == "redock" else "preserved",
                "chemical_state": "as_generated; no protomer/tautomer enumeration performed"})
    if wanted is not None and wanted - seen:
        raise ValueError("selected candidates absent from supplied manifests")
    if not selected:
        raise ValueError("no readable selected structures; inspect native failures")
    basis = {"operation": operation, "sources": sources, "selection": selected, "skipped": skipped}
    request_id = digest(basis)
    root.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(root / "handoff"):
        receipt = root / "handoff.json"
        if receipt.exists():
            prior = json.loads(receipt.read_text(encoding="utf-8"))
            if prior["request_id"] != request_id:
                raise ValueError("handoff destination belongs to another selection; choose a new directory")
            for pin in prior["outputs"].values():
                if file_identity(pin["path"]) != {k: pin[k] for k in ("sha256", "bytes")}:
                    raise ValueError("completed handoff output changed")
            return {**prior, "reused": True}
        sdf, mapping = root / "ligands.sdf", root / "candidates.json"
        sdf.write_text("".join(blocks), encoding="utf-8")
        _save(mapping, basis, replace=True)
        result = {"request_id": request_id, "input_key": input_key, "operation": operation, "candidate_count": len(selected),
            "skipped_count": len(skipped), "outputs": {name: {"path": str(p), **file_identity(p)}
                for name, p in (("sdf", sdf), ("candidates", mapping))},
            "scientific_admission": "not_inferred", "scientific_calls": 0,
            "screen_input": {"library": {"format": "sdf", "id_column": "ETALON_OCCURRENCE"},
                "ingest": {"id": "library", "backend": "source.sdf@0.1.0", "settings": {
                    "schema_version": 1, "candidate_id_property": "ETALON_OCCURRENCE",
                    "selected_properties": ["ETALON_PARTITION", "ETALON_NATIVE_QUALITY"]}}},
            "next": "Host selects a compatible pose consumer, screening config or property method.", "reused": False}
        _save(receipt, result)
        return result
