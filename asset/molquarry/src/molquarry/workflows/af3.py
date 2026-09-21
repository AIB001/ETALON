"""Official AF3 job formats, local execution, and bounded import of actual result archives."""

import json
import math
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from ..models import utcnow
from .structures import pdb_export

SERVER_DOCS = "https://github.com/google-deepmind/alphafold/blob/main/server/README.md"
LOCAL_DOCS = "https://github.com/google-deepmind/alphafold3/blob/main/docs/input.md"


def coordinate_model(path):
    """A filename alone is not evidence of a completed prediction."""
    import gemmi

    structure = gemmi.read_structure(str(path))
    if not len(structure) or not structure[0].count_atom_sites():
        raise ValueError("Coordinate file contains no model atoms")
    for model in structure:
        for chain in model:
            for residue in chain:
                for atom in residue:
                    if not all(math.isfinite(x) for x in (atom.pos.x, atom.pos.y, atom.pos.z)):
                        raise ValueError("Coordinate file contains nonfinite atom positions")
    return structure


def prepare_af3_jobs(targets, output_dir):
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    jobs = [(t["gene"], [t]) for t in targets]
    if len(targets) > 1:
        jobs.append(("_".join(t["gene"] for t in targets) + "_complex", targets))
    server_jobs, local_files = [], []
    for name, members in jobs:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        for target in members:
            if not target.get("sequence") or set(target["sequence"]) - set("ACDEFGHIKLMNPQRSTVWY"):
                raise ValueError("AF3 job requires a verified sequence with standard amino acids")
        server_jobs.append(
            {
                "name": name,
                "modelSeeds": [],
                "dialect": "alphafoldserver",
                "version": 1,
                "sequences": [
                    {"proteinChain": {"sequence": t["sequence"], "count": 1}} for t in members
                ],
            }
        )
        local = {
            "name": name,
            "modelSeeds": [1],
            "dialect": "alphafold3",
            "version": 1,
            "sequences": [
                {"protein": {"id": chr(65 + i), "sequence": t["sequence"]}}
                for i, t in enumerate(members)
            ],
        }
        # Version 1 uses only the original supported protein fields, also accepted by newer AF3.
        filename = f"{name}_local.json"
        with (root / filename).open("x", encoding="utf-8") as f:
            json.dump(local, f, indent=2)
        local_files.append(filename)
    with (root / "alphafold_server_jobs.json").open("x", encoding="utf-8") as f:
        json.dump(server_jobs, f, indent=2)
    status = {
        "status": "prepared_not_submitted",
        "created_at": utcnow(),
        "models_returned": 0,
        "server_input": "alphafold_server_jobs.json",
        "local_inputs": local_files,
        "submission": "Official Server JSON upload in an authenticated browser, or a configured "
        "local AlphaFold 3 installation. No documented username/password REST "
        "submission endpoint is implemented; account credentials alone are not a job.",
        "server_docs": SERVER_DOCS,
        "local_docs": LOCAL_DOCS,
    }
    (root / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status


def import_af3_results(archive, output_dir, *, max_bytes=500_000_000):
    """Import a downloaded AF3 ZIP. No credentials or network requests are accepted here."""
    root = Path(output_dir).resolve()
    if root.exists():
        raise FileExistsError(root)
    source = Path(archive).resolve()
    if source.stat().st_size > max_bytes:
        raise ValueError("AF3 archive exceeds the import byte budget")
    allowed = {".cif", ".pdb", ".json", ".csv", ".md", ".txt", ".png"}
    try:
        archive_file = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid AF3 ZIP archive") from exc
    with archive_file as zf:
        entries = zf.infolist()
        if len(entries) > 2000 or sum(f.file_size for f in entries) > max_bytes:
            raise ValueError("AF3 archive exceeds the expanded-size or entry budget")
        names = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in entry.filename
                or ":" in entry.filename
            ):
                raise ValueError("Unsafe AF3 archive path")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Symlinks are not accepted in AF3 archives")
            normalized = str(path).casefold()
            if normalized in names:
                raise ValueError("Duplicate AF3 archive path")
            names.add(normalized)
        root.mkdir(parents=True)
        copied, model_files, confidence, errors = [], [], [], []
        for entry in entries:
            if entry.is_dir() or Path(entry.filename).suffix.lower() not in allowed:
                continue
            target = root / "original" / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            copied.append(str(target.relative_to(root)))
            if target.suffix.lower() in {".cif", ".pdb"}:
                try:
                    structure = coordinate_model(target)
                except (ValueError, RuntimeError) as exc:
                    errors.append({"file": str(target.relative_to(root)), "error": str(exc)})
                    continue
                record = {
                    target.suffix.lower()[1:]: str(target.relative_to(root)),
                    "experimental": False,
                    "coordinate_valid": True,
                }
                try:
                    # Preserve both coordinate formats when an archive contains model.cif
                    # and model.pdb; output names must not overwrite or collide.
                    relative = Path(entry.filename)
                    out = root / "pdb" / relative.with_name(relative.name + ".pdb")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    chain_map = pdb_export(structure, out)
                    record.update(
                        pdb=str(out.relative_to(root)),
                        chain_map=chain_map,
                    )
                except (ValueError, RuntimeError) as exc:
                    record["pdb_error"] = str(exc)
                model_files.append(record)
            if target.suffix == ".json" and "confidence" in target.name:
                try:
                    data = json.loads(target.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        confidence.append(
                            {
                                "file": str(target.relative_to(root)),
                                **{
                                    key: data[key]
                                    for key in ["ptm", "iptm", "ranking_score", "has_clash"]
                                    if key in data
                                },
                            }
                        )
                except (ValueError, UnicodeError):
                    pass
    result = {
        "status": "imported_predictions" if model_files else "no_models_found",
        "imported_at": utcnow(),
        "archive": source.name,
        "models": model_files,
        "model_errors": errors,
        "confidence": confidence,
        "files": copied,
        "note": "Imported user-supplied results, not proof that MolQuarry ran a prediction. "
        "Raw PAE/pLDDT and inputs remain in original/. Experimental resolution is not assigned.",
    }
    (root / "index.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_local_af3(
    job_json,
    output_dir,
    *,
    python_executable,
    run_script,
    model_dir,
    database_dir,
    timeout_seconds=86400,
):
    """Explicit local backend for user-requested computation on this AF3 installation."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    job = json.loads(Path(job_json).read_text(encoding="utf-8"))
    if job.get("dialect") != "alphafold3" or not job.get("sequences"):
        raise ValueError("Expected an AlphaFold 3 input job")
    job_path = root / "input.json"
    job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    args = [
        str(Path(python_executable).resolve()),
        str(Path(run_script).resolve()),
        f"--json_path={job_path}",
        f"--output_dir={root / 'models'}",
        f"--model_dir={Path(model_dir).resolve()}",
        f"--db_dir={Path(database_dir).resolve()}",
    ]
    status = {"status": "running", "started_at": utcnow(), "backend": "local_alphafold3"}
    (root / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    try:
        with (root / "runner.log").open("x", encoding="utf-8") as log:
            completed = subprocess.run(
                args,
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
        models, errors = [], []
        for path in sorted((root / "models").rglob("*_model.cif")):
            try:
                coordinate_model(path)
                models.append(str(path.relative_to(root)))
            except (ValueError, RuntimeError) as exc:
                errors.append({"file": str(path.relative_to(root)), "error": str(exc)})
        status.update(
            status="completed" if completed.returncode == 0 and models else "failed",
            returncode=completed.returncode,
            model_files=models,
            model_errors=errors,
        )
    except subprocess.TimeoutExpired:
        status.update(status="timed_out")
    except OSError as exc:
        status.update(status="launch_failed", error=str(exc))
    status["finished_at"] = utcnow()
    (root / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status
