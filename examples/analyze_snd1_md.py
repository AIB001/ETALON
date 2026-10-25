"""Versioned retrospective analysis of the six frozen SND1 monomer controls.

This entry point never runs MD and never writes under its input tree.  It reuses
the supplied PRISM source snapshot for residue contacts; ligand displacement is
measured after a protein fit, without fitting the ligand a second time.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import importlib
import itertools
import json
import math
import os
from pathlib import Path
import re
try:
    import resource
except ImportError:  # Pure readout tests also run in the Windows editing environment.
    resource = None
import subprocess
import sys
import time
import traceback

import numpy as np

SCHEMA = "snd1-md-retrospective-analysis/1"
SYSTEMS = ("7KNW-QOY", "7KNX-QOV")
SEEDS = (202610241, 202610242, 202610243)
SETTINGS = {
    "analysis_role": "post_MD_retrospective_descriptive_analysis",
    "scope": "Two crystal-derived monomer controls, three velocity seeds each; exploratory stability only",
    "not_established": ["affinity", "equilibrium convergence", "new chemical validation", "F_vs_T0_MD_comparison", "physiological pocket fidelity"],
    "native_QC": "completed receipt and exact original hashes; final checkpoint 10000000 steps/20000ps; native gmx check; all original coordinates finite on exact 0..20000ps by10ps grid; Finished mdrun and no LINCS WARNING/Fatal error",
    "reference": "unminimized controls/<system>/complex.gro: repaired crystal-derived protein and deposited ligand in common frame; finite GRO precision; not an independent experimentally refined full model",
    "fit": "unweighted proper Kabsch on deposited observed protein CA, excluding modeled missing-loop residues; secondary local fit on those CA within0.8nm of any reference ligand heavy atom",
    "ligand_RMSD": "fixed indexed heavy atoms in reference frame after protein fit; no ligand refit and no symmetry permutation; separate ligand-self-fit RMSD reports internal deformation",
    "PBC": "native gmx trjconv -pbc whole extracts solute; move the whole ligand by the nearest lattice vector to the instantaneous reference-pocket CA centroid; distances use periodic minimum image on unrotated boxes",
    "contacts": "PRISM ContactAnalyzer.calculate_contact_proportions; any protein-residue/ligand heavy atom pair strictly below0.4nm in a frame; retain every residue; sensitivity0.35/0.45nm uses the same minimum-distance series",
    "polar_proximity": "any N/O/S pair <=0.35nm, geometric proximity only; no donor/acceptor or hydrogen-bond claim",
    "native_contacts": "reference residue minimum heavy distance <0.4nm; retention is fraction of this fixed set contacted per frame; no binding/stability pass threshold",
    "torsions": "all SDF single, nonring heavy-heavy bonds whose ends each have another heavy neighbor; deterministic smallest-index heavy flank at each end; fixed atom map to native ITP/GRO; circular statistics and12 bins of30deg",
    "torsion_descriptive_shift_deg": 60.0,
    "windows_ns": [[0.0, 5.0], [5.0, 10.0], [10.0, 15.0], [15.0, 20.0]],
    "window_boundaries": "left inclusive, right exclusive except final20ns is included; full-run includes all2001 frames",
    "uncertainty": "three velocity seeds share prepared structures and forcefield; report per-run values and replicate range; frames are autocorrelated, no frame-level significance tests or independent sample claim",
    "hbond_capability_not_used": "PRISM HBondAnalyzer treats all N/O as acceptors and infers donors by distance; chemistry-aware H-bond typing was not validated for these inputs",
    "RMSD_capability_not_used": "PRISM RMSDAnalyzer fits calculate_selection again via rms.RMSD/md.rmsd, removing ligand translation; explicit tested protein fit is required",
    "TPR_parser_limit": "MDAnalysis2.10 does not support TPR138; use native GROMACS TPR extraction plus GRO topology and exact indexed ligand mapping, without upgrading the shared environment",
    "resources": {"GPU_use": False, "threads": 1, "shared_environment_installs": False, "rerun_MD": False},
    "r4b": "paused",
}


def pin(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return {"sha256": h.hexdigest(), "bytes": path.stat().st_size}


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def aligned_coordinates(xyz, reference, fit_indices):
    """Apply a proper, unweighted protein-only row-vector Kabsch fit."""
    xyz = np.asarray(xyz, dtype=float)
    reference = np.asarray(reference, dtype=float)
    indices = np.asarray(fit_indices, dtype=int)
    if xyz.shape != reference.shape or xyz.ndim != 2 or xyz.shape[1] != 3 or len(indices) < 3:
        raise ValueError("matching Nx3 coordinates and at least three fit atoms required")
    if not np.isfinite(xyz).all() or not np.isfinite(reference).all():
        raise ValueError("nonfinite fit coordinates")
    mobile_center = xyz[indices].mean(axis=0)
    fixed_center = reference[indices].mean(axis=0)
    mobile = xyz[indices] - mobile_center
    fixed = reference[indices] - fixed_center
    if np.linalg.matrix_rank(mobile, tol=1e-10) < 2 or np.linalg.matrix_rank(fixed, tol=1e-10) < 2:
        raise ValueError("collinear fit selection")
    u, _, vt = np.linalg.svd(mobile.T @ fixed)
    u[:, -1] *= np.linalg.det(u @ vt)
    rotation = u @ vt
    return (xyz - mobile_center) @ rotation + fixed_center


def rmsd_fixed(xyz, reference, indices):
    indices = np.asarray(indices, dtype=int)
    if not len(indices):
        raise ValueError("empty RMSD selection")
    delta = np.asarray(xyz)[..., indices, :] - np.asarray(reference)[indices]
    return np.sqrt(np.mean(np.sum(delta * delta, axis=-1), axis=-1))


def nearest_image_shift(displacement, box):
    """Nearest lattice vector for the reduced triclinic GROMACS boxes used here."""
    displacement = np.asarray(displacement, dtype=float)
    box = np.asarray(box, dtype=float)
    if box.shape != (3, 3) or not np.isfinite(box).all() or np.linalg.det(box) <= 0:
        raise ValueError("invalid periodic box")
    center = np.rint(displacement @ np.linalg.inv(box)).astype(int)
    lattice = center + np.asarray(list(itertools.product((-1, 0, 1), repeat=3)))
    shifts = lattice @ box
    return shifts[np.argmin(np.sum((displacement - shifts) ** 2, axis=1))]


def circular_summary(degrees):
    degrees = np.asarray(degrees, dtype=float)
    radians = np.deg2rad(degrees)
    vector = np.mean(np.exp(1j * radians))
    return {"mean_deg": float(np.rad2deg(np.angle(vector))), "resultant_length": float(abs(vector)),
            "circular_std_deg": float(np.rad2deg(np.sqrt(max(0.0, -2 * np.log(max(abs(vector), 1e-15)))))),
            "histogram_30deg": np.histogram((degrees + 180) % 360 - 180, np.linspace(-180, 180, 13))[0].tolist()}


def principal_angle_delta(degrees, reference):
    return (np.asarray(degrees) - reference + 180.0) % 360.0 - 180.0


def window_masks(time_ps):
    t = np.asarray(time_ps) / 1000.0
    result = {"full": np.ones(len(t), dtype=bool)}
    for left, right in SETTINGS["windows_ns"]:
        result[f"{left:g}_{right:g}ns"] = (t >= left) & ((t <= right) if right == 20 else (t < right))
    return result


def indexed_solute_binding(reference, native):
    """Preserve indexed chemical identity, allowing native MOL residue renumbering."""
    if len(reference) != len(native) or any(a[:2] != b[:2] for a, b in zip(reference, native)):
        raise ValueError("solute indexed atom/residue name mismatch")
    if any(a[2] != b[2] for a, b in zip(reference, native) if a[1] != "MOL"):
        raise ValueError("protein residue numbering mismatch")
    ligand_reference = {a[2] for a in reference if a[1] == "MOL"}
    ligand_native = {a[2] for a in native if a[1] == "MOL"}
    if len(ligand_reference) != 1 or len(ligand_native) != 1:
        raise ValueError("exactly one ligand residue required")
    return {"indexed_atom_and_residue_names_match": True, "protein_residue_numbers_match": True,
            "reference_ligand_resid": next(iter(ligand_reference)), "native_ligand_resid": next(iter(ligand_native)),
            "note": "native GROMACS renumbers the unique ligand residue; fixed atom order/name map preserved"}


def statistics(values):
    v = np.asarray(values, dtype=float)
    if not v.size or not np.isfinite(v).all():
        raise ValueError("empty/nonfinite readout")
    return {"mean": float(v.mean()), "std_population": float(v.std()), "median": float(np.median(v)),
            "min": float(v.min()), "max": float(v.max()), "p95": float(np.percentile(v, 95)), "last": float(v[-1])}


def tsv_or_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def capabilities(prism_source):
    os.environ.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
    sys.path.insert(0, str(prism_source))
    with contextlib.redirect_stdout(sys.stderr):
        import mdtraj as md
        import MDAnalysis as mda
        from rdkit import Chem
        from prism.analysis.calc.contacts import ContactAnalyzer
        from prism.analysis.core.config import AnalysisConfig
    if not Path(importlib.import_module("prism").__file__).resolve().is_relative_to(Path(prism_source).resolve()):
        raise RuntimeError("wrong PRISM source imported")
    return md, mda, Chem, ContactAnalyzer, AnalysisConfig


def module_evidence():
    names = [n for n in sys.modules if n.startswith("prism.") and getattr(sys.modules[n], "__file__", None)]
    names += ["prism", "numpy", "numpy.linalg._umath_linalg", "mdtraj", "mdtraj.geometry._geometry", "MDAnalysis", "rdkit"]
    evidence = {}
    for name in sorted(set(names)):
        module = importlib.import_module(name)
        path = module.__file__
        evidence[name] = {"path": path, "version": getattr(module, "__version__", None), **pin(path)}
    return evidence


def ligand_map(p4, system, md, Chem):
    reference = md.load(str(p4 / "controls" / system / "complex.gro"))
    reference.unitcell_vectors = None  # This structural reference is not a periodic simulation box.
    molecules = [r for r in reference.topology.residues if r.name == "MOL"]
    if len(molecules) != 1:
        raise ValueError("one indexed MOL ligand required")
    ligand = list(molecules[0].atoms)
    sdf = p4 / "inputs" / system.split("-")[1] / "ligand.sdf"
    molecule = Chem.SDMolSupplier(str(sdf), removeHs=False)[0]
    if molecule is None or len(ligand) != molecule.GetNumAtoms():
        raise ValueError("SDF/GRO ligand atom mismatch")
    records, section = [], None
    for line in (p4 / "controls" / system / "ligand.itp").read_text("utf-8").splitlines():
        line = line.split(";", 1)[0].strip()
        if line.startswith("["):
            section = line.strip("[] ")
        elif line and section == "atoms":
            records.append(line.split())
    if len(records) != len(ligand):
        raise ValueError("ITP/GRO atom count mismatch")
    element_corrections = []
    for idx, (atom, record, original) in enumerate(zip(ligand, records, molecule.GetAtoms())):
        if atom.name != record[4] or int(record[0]) != idx + 1:
            raise ValueError("native indexed atom name mismatch")
        symbol = original.GetSymbol()
        # GRO has no element column; e.g. CL1 is incorrectly inferred as carbon.
        if atom.element.symbol != symbol:
            element_corrections.append({"atom": atom.name, "inferred": atom.element.symbol, "SDF_element": symbol})
            atom.element = md.element.get_by_symbol(symbol)
        if abs(float(record[7]) - original.GetMass()) > 0.2:
            raise ValueError("SDF element/native ITP mass mismatch")
    input_nm = molecule.GetConformer().GetPositions() / 10.0
    error = np.linalg.norm(reference.xyz[0, [a.index for a in ligand]] - input_nm, axis=1).max()
    if error > 0.0009:
        raise ValueError("indexed reference/SDF coordinates mismatch")
    torsions = []
    for bond in molecule.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        ends = (molecule.GetAtomWithIdx(a), molecule.GetAtomWithIdx(b))
        if bond.GetBondType() != Chem.BondType.SINGLE or bond.IsInRing() or any(x.GetAtomicNum() <= 1 for x in ends):
            continue
        left = sorted(n.GetIdx() for n in ends[0].GetNeighbors() if n.GetAtomicNum() > 1 and n.GetIdx() != b)
        right = sorted(n.GetIdx() for n in ends[1].GetNeighbors() if n.GetAtomicNum() > 1 and n.GetIdx() != a)
        if left and right:
            indices = [left[0], a, b, right[0]]
            torsions.append({"id": "-".join(ligand[i].name for i in indices), "SDF_indices0": indices,
                             "solute_indices0": [ligand[i].index for i in indices]})
    modeled = [int(r["id"]) for block in read(p4 / "protein-preparation" / system.split("-")[0] / "repair-audit.json")["deposited_internal_missing_residues"] for r in block["residues"]]
    protein_ca = [a.index for a in reference.topology.atoms if a.name == "CA" and a.residue.is_protein and a.residue.resSeq not in modeled]
    heavy = [a.index for a in ligand if a.element.symbol != "H"]
    pocket_ca = [i for i in protein_ca if np.linalg.norm(reference.xyz[0, heavy] - reference.xyz[0, i], axis=1).min() < 0.8]
    if len(pocket_ca) < 3:
        raise ValueError("insufficient fixed reference-pocket CA")
    return reference, {"solute_atoms": reference.n_atoms, "ligand_indices0": [a.index for a in ligand],
                       "ligand_heavy_indices0": heavy, "ligand_names": [a.name for a in ligand],
                       "ligand_elements": [a.element.symbol for a in ligand], "element_corrections": element_corrections,
                       "SDF_GRO_max_indexed_coordinate_error_nm": float(error), "torsions": torsions,
                       "modeled_loop_resids": modeled, "fit_CA_indices0": protein_ca, "pocket_CA_indices0": pocket_ca,
                       "pocket_CA_resids": [reference.topology.atom(i).residue.resSeq for i in pocket_ca]}


def freeze(args):
    start, cpu = time.perf_counter(), time.process_time()
    output, p4 = args.output.absolute(), args.p4.absolute()
    if output == p4 or output.is_relative_to(p4):
        raise ValueError("analysis outputs must be outside the original input tree")
    if (output / "analysis-freeze.json").exists():
        raise FileExistsError("frozen analysis already exists; preserve it and select a new version")
    output.mkdir(parents=True, exist_ok=True)
    md, _, Chem, _, _ = capabilities(args.prism_source)
    value = {"schema": SCHEMA, "created_utc": now(), "settings": SETTINGS, "p4": str(p4), "prism_source": str(args.prism_source),
             "source": {"path": str(Path(__file__).absolute()), **pin(__file__)}, "gmx": {"path": str(args.gmx), **pin(args.gmx)},
             "python": sys.executable, "python_version": sys.version, "modules": module_evidence(), "inputs": {}, "systems": {}, "runs": [],
             "already_completed_MD": read(p4 / "md-wave-v2/completion.json"), "MD_protocol": read(p4 / "md-wave-v2/PROTOCOL.json"),
             "analysis_gates_are_not_prospective_MD_thresholds": True, "affinity": sorted(os.sched_getaffinity(0))}
    required = [p4 / "md-wave-v2/PROTOCOL.json", p4 / "md-wave-v2/completion.json", p4 / "md_wave_v2.py", p4 / "control-build-freeze.json", p4 / "parameter-certification-v2.json"]
    for system in SYSTEMS:
        _, mapping = ligand_map(p4, system, md, Chem)
        value["systems"][system] = mapping
        required += [p4 / "controls" / system / name for name in ("complex.gro", "topol.top", "ligand.itp", "ligand-atomtypes.itp", "cpu-minimization/em.gro")]
        required += [p4 / "inputs" / system.split("-")[1] / "ligand.sdf", p4 / "protein-preparation" / system.split("-")[0] / "repair-audit.json"]
        for seed in SEEDS:
            run_id = f"{system}-seed-{seed}"
            folder = p4 / "md-wave-v2/runs" / run_id
            stage_values = {}
            for stage in ("nvt", "npt", "md"):
                root = folder / stage
                completion = read(root / "completion.json")
                if completion["status"] != "completed_verified" or completion.get("native_exit_code") != 0:
                    raise ValueError("not a completed, known-zero-exit native stage: " + str(root))
                for name, identity in completion["artifacts"].items():
                    if pin(root / name) != identity:
                        raise ValueError("original completed artifact changed: " + str(root / name))
                    required.append(root / name)
                required += [root / "completion.json", root / "request.json", root / "input.tpr", root / "effective.mdp"]
                attempts = []
                for attempt in sorted(root.glob("attempt-*")):
                    receipt = read(attempt / "receipt.json") if (attempt / "receipt.json").exists() else None
                    attempts.append({"path": str(attempt), "receipt": receipt})
                    required += list(attempt.glob("*.json"))
                stage_values[stage] = {"completion": completion, "attempts": attempts}
            value["runs"].append({"id": run_id, "system": system, "seed": seed, "stages": stage_values})
    value["inputs"] = {str(p): pin(p) for p in sorted(set(required))}
    value["freeze_cost"] = {"wall_second": time.perf_counter() - start, "cpu_second": time.process_time() - cpu, "GPU_second": 0,
                            "scope": "hash verification, module inspection, and reference-only analysis freeze; does not rerun MD"}
    write(output / "analysis-freeze.json", value)
    print(json.dumps({"output": str(output), "freeze": pin(output / "analysis-freeze.json"), "runs": len(value["runs"])}))


def native(folder, label, gmx, arguments, stdin=""):
    args = [str(gmx), *arguments]
    start, usage = time.perf_counter(), resource.getrusage(resource.RUSAGE_CHILDREN)
    write(folder / (label + ".request.json"), {"args": args, "stdin": stdin, "cwd": str(folder), "created_utc": now()})
    with (folder / (label + ".stdout")).open("w", encoding="utf-8") as out, (folder / (label + ".stderr")).open("w", encoding="utf-8") as err:
        result = subprocess.run(args, input=stdin, text=True, stdout=out, stderr=err, cwd=folder, timeout=900)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = {"exit_code": result.returncode, "wall_second": time.perf_counter() - start,
               "children_cpu_second": after.ru_utime + after.ru_stime - usage.ru_utime - usage.ru_stime}
    write(folder / (label + ".receipt.json"), receipt)
    if result.returncode:
        raise RuntimeError("native analysis command failed: " + label)
    return (folder / (label + ".stdout")).read_text("utf-8")


def exact_grid_and_coordinates(md, trajectory, topology):
    times, cells = [], []
    for chunk in md.iterload(str(trajectory), top=topology, chunk=64):
        if chunk.n_atoms != topology.n_atoms or not np.isfinite(chunk.xyz).all():
            raise ValueError("truncated or nonfinite full original trajectory")
        if chunk.unitcell_vectors is None or not np.isfinite(chunk.unitcell_vectors).all() or np.any(chunk.unitcell_volumes <= 0):
            raise ValueError("invalid original periodic cells")
        times.extend(chunk.time.tolist())
        cells.extend(chunk.unitcell_volumes.tolist())
    expected = np.arange(0, 20000 + 1, 10, dtype=float)
    if len(times) != len(expected) or not np.allclose(times, expected, atol=1e-4, rtol=0):
        raise ValueError("original trajectory time grid mismatch")
    return {"frames": len(times), "atoms": topology.n_atoms, "first_time_ps": times[0], "last_time_ps": times[-1],
            "all_coordinates_finite": True, "exact_time_grid": True, "cell_volume_nm3": statistics(cells)}


def energy_data(folder, gmx, original):
    native(folder, "energy-check", gmx, ["check", "-e", str(original / "md.edr")])
    native(folder, "energy-extract", gmx, ["energy", "-f", str(original / "md.edr"), "-o", "energy.xvg", "-dp"],
           "Potential\nTotal-Energy\nTemperature\nPressure\nDensity\n0\n")
    headers, data = {}, []
    for line in (folder / "energy.xvg").read_text("utf-8").splitlines():
        match = re.match(r'@\s+s(\d+)\s+legend\s+"([^"]+)"', line)
        if match:
            headers[int(match.group(1))] = match.group(2)
        elif line.strip() and not line.startswith(("#", "@")):
            data.append([float(x) for x in line.split()])
    matrix = np.asarray(data)
    if matrix.ndim != 2 or not np.isfinite(matrix).all() or matrix[0, 0] != 0 or abs(matrix[-1, 0] - 20000) > 1e-4:
        raise ValueError("invalid/truncated energy series")
    if len(headers) != 5 or matrix.shape[1] != 6:
        raise ValueError("missing requested energy observables")
    result = {"frames": len(matrix), "time_ps_start": float(matrix[0, 0]), "time_ps_end": float(matrix[-1, 0]), "all_finite": True, "series": {}}
    masks = window_masks(matrix[:, 0])
    for index, label in sorted(headers.items()):
        result["series"][label] = {name: statistics(matrix[mask, index + 1]) for name, mask in masks.items()}
    tsv_or_csv(folder / "energy.csv", ["time_ps", *[headers[i] for i in sorted(headers)]],
               [dict(zip(["time_ps", *[headers[i] for i in sorted(headers)]], row)) for row in matrix])
    return result


def run(args):
    output = args.output.absolute()
    frozen = read(output / "analysis-freeze.json")
    if frozen["settings"] != SETTINGS or frozen["source"] != {"path": str(Path(__file__).absolute()), **pin(__file__)}:
        raise ValueError("analysis source/settings changed after freeze; use a new version")
    if sys.executable != frozen["python"] or pin(args.gmx) != {k: frozen["gmx"][k] for k in ("sha256", "bytes")}:
        raise ValueError("interpreter/native binary mismatch")
    selected = next(row for row in frozen["runs"] if row["id"] == args.run_id)
    parent = output / "runs" / args.run_id
    previous = sorted(parent.glob("attempt-*"))
    if previous and (previous[-1] / "result.json").exists() and read(previous[-1] / "result.json")["status"] == "analyzed":
        raise FileExistsError("successful analysis already exists; preserve result")
    folder = parent / f"attempt-{len(previous) + 1:03d}"
    folder.mkdir(parents=True, exist_ok=False)
    start, cpu, children = time.perf_counter(), time.process_time(), resource.getrusage(resource.RUSAGE_CHILDREN)
    result = {"schema": SCHEMA, "status": "failed", "run_id": args.run_id, "system": selected["system"], "seed": selected["seed"],
              "created_utc": now(), "freeze": pin(output / "analysis-freeze.json"), "unknowns": [], "failures": [], "cost": None}
    try:
        md, mda, Chem, ContactAnalyzer, AnalysisConfig = capabilities(args.prism_source)
        modules = module_evidence()
        if modules != frozen["modules"]:
            raise ValueError("scientific module paths/hashes changed")
        write(folder / "runtime.json", {"python": sys.executable, "version": sys.version, "modules": modules,
              "gmx_version": native(folder, "gmx-version", args.gmx, ["--version"]), "affinity": sorted(os.sched_getaffinity(0)),
              "threads": 1, "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"), "source": frozen["source"]})
        p4 = Path(frozen["p4"])
        original = p4 / "md-wave-v2/runs" / args.run_id / "md"
        for path, identity in frozen["inputs"].items():
            target = Path(path)
            if str(original.parent) in str(target) or "/controls/" in str(target) or "/inputs/" in str(target) or "/protein-preparation/" in str(target) or target.name == "PROTOCOL.json":
                if pin(target) != identity:
                    raise ValueError("frozen original input changed: " + path)
        log = (original / "md.log").read_text("utf-8")
        if "Finished mdrun" not in log or re.search("LINCS WARNING|Fatal error", log, re.I):
            raise ValueError("native MD log QC failed")
        native(folder, "trajectory-check", args.gmx, ["check", "-f", str(original / "md.xtc")])
        dump = native(folder, "checkpoint-check", args.gmx, ["dump", "-cp", str(original / "md.cpt")])
        step = int(re.search(r"^\s*step\s*=\s*(\d+)", dump, re.M).group(1))
        time_ps = float(re.search(r"^\s*t\s*=\s*(\S+)", dump, re.M).group(1))
        if step != 10000000 or abs(time_ps - 20000) > 1e-4:
            raise ValueError("checkpoint target mismatch")
        # Native readers support this TPR; the older Python parser failure is preserved.
        try:
            mda.Universe(str(original / "input.tpr"))
        except ValueError as exc:
            result["unknowns"].append({"capability": "MDAnalysis_TPR_parser", "status": "unsupported_with_validated_native_fallback", "error": str(exc)})
        top = md.load(str(original / "md.gro"))
        qc = exact_grid_and_coordinates(md, original / "md.xtc", top.topology)
        qc.update(native_exit_code=selected["stages"]["md"]["completion"]["native_exit_code"], final_checkpoint_step=step,
                  final_checkpoint_ps=time_ps, original_artifacts_hashes_verified=True, Finished_mdrun=True, LINCS_WARNING=False, Fatal_error=False)
        result["QC"] = qc
        result["energy"] = energy_data(folder, args.gmx, original)
        reference, mapping = ligand_map(p4, selected["system"], md, Chem)
        if mapping != frozen["systems"][selected["system"]]:
            raise ValueError("indexed ligand/reference mapping changed")
        n_solute = mapping["solute_atoms"]
        result["topology_binding"] = indexed_solute_binding(
            [(a.name, a.residue.name, a.residue.resSeq) for a in reference.topology.atoms],
            [(a.name, a.residue.name, a.residue.resSeq) for a in top.topology.atoms if a.index < n_solute])
        indices = np.arange(n_solute)
        (folder / "analysis.ndx").write_text("[ Solute ]\n" + "\n".join(" ".join(str(i + 1) for i in indices[j:j + 15]) for j in range(0, n_solute, 15)) + "\n", encoding="utf-8")
        native(folder, "extract-whole", args.gmx, ["trjconv", "-s", str(original / "input.tpr"), "-f", str(original / "md.xtc"), "-n", str(folder / "analysis.ndx"), "-o", "solute-whole.xtc", "-pbc", "whole"], "Solute\n")
        native(folder, "extract-native-topology", args.gmx, ["trjconv", "-s", str(original / "input.tpr"), "-f", str(original / "md.xtc"), "-n", str(folder / "analysis.ndx"), "-o", "native-solute-frame0.pdb", "-dump", "0", "-pbc", "whole"], "Solute\n")
        native_top = md.load(str(folder / "native-solute-frame0.pdb"))
        if native_top.n_atoms != n_solute or [a.name for a in native_top.topology.atoms] != [a.name for a in reference.topology.atoms]:
            raise ValueError("native TPR extraction/GRO atom names differ")
        traj = md.load(str(folder / "solute-whole.xtc"), top=reference.topology)
        if traj.n_atoms != n_solute or len(traj) != 2001 or not np.isfinite(traj.xyz).all() or not np.allclose(traj.time, np.arange(2001) * 10, atol=1e-4, rtol=0):
            raise ValueError("extracted solute QC failed")
        # The source has native chemical identity; box vectors remain unrotated for contacts.
        contact_result = ContactAnalyzer(AnalysisConfig(ligand_resname="MOL", contact_cutoff_nm=0.4, cache_dir=str(folder / "prism-cache"), parallel_workers=1)).calculate_contact_proportions(traj)
        reference_contacts = ContactAnalyzer(AnalysisConfig(ligand_resname="MOL", contact_cutoff_nm=0.4, cache_dir=str(folder / "prism-reference-cache"), parallel_workers=1)).calculate_contact_proportions(reference)
        labels = list(contact_result["residue_proportions"])
        minimum = np.asarray([contact_result["residue_min_distances_nm"][k] for k in labels], dtype=np.float32).T
        initial = np.asarray([reference_contacts["residue_proportions"][k] > 0 for k in labels])
        if not initial.any():
            raise ValueError("empty reference contact set")
        masks = window_masks(traj.time)
        contact_rows = []
        for j, label in enumerate(labels):
            residue = traj.topology.residue(contact_result["residue_indices"][label])
            for window, mask in masks.items():
                row = {"residue": label, "resid": residue.resSeq, "modeled_loop": residue.resSeq in mapping["modeled_loop_resids"],
                       "reference_contact_4A": bool(initial[j]), "window": window, "frames": int(mask.sum()), "minimum_distance_mean_A": float(minimum[mask, j].mean() * 10)}
                for cutoff in (0.35, 0.4, 0.45):
                    row[f"occupancy_lt_{cutoff * 10:g}A"] = float((minimum[mask, j] < cutoff).mean())
                contact_rows.append(row)
        tsv_or_csv(folder / "contacts.csv", list(contact_rows[0]), contact_rows)
        lig = np.asarray(mapping["ligand_indices0"])
        heavy = np.asarray(mapping["ligand_heavy_indices0"])
        ca, pocket = np.asarray(mapping["fit_CA_indices0"]), np.asarray(mapping["pocket_CA_indices0"])
        imaged = traj.xyz.copy()
        shifts = []
        for i in range(len(traj)):
            shift = nearest_image_shift(imaged[i, heavy].mean(axis=0) - imaged[i, pocket].mean(axis=0), traj.unitcell_vectors[i])
            imaged[i, lig] -= shift
            shifts.append(shift)
        aligned = np.asarray([aligned_coordinates(frame, reference.xyz[0], ca) for frame in imaged], dtype=np.float32)
        local = np.asarray([aligned_coordinates(frame, reference.xyz[0], pocket) for frame in imaged], dtype=np.float32)
        own_start = np.asarray([aligned_coordinates(frame, imaged[0], ca) for frame in imaged], dtype=np.float32)
        ligand_self = np.asarray([aligned_coordinates(frame[heavy], reference.xyz[0, heavy], np.arange(len(heavy))) for frame in imaged])
        values = {
            "protein_CA_RMSD_A": rmsd_fixed(aligned, reference.xyz[0], ca) * 10,
            "pocket_CA_RMSD_A_globalfit": rmsd_fixed(aligned, reference.xyz[0], pocket) * 10,
            "pocket_CA_RMSD_A_localfit": rmsd_fixed(local, reference.xyz[0], pocket) * 10,
            "ligand_RMSD_A_globalfit": rmsd_fixed(aligned, reference.xyz[0], heavy) * 10,
            "ligand_RMSD_A_pocketfit": rmsd_fixed(local, reference.xyz[0], heavy) * 10,
            "ligand_centroid_drift_A_globalfit": np.linalg.norm(aligned[:, heavy].mean(axis=1) - reference.xyz[0, heavy].mean(axis=0), axis=1) * 10,
            "ligand_RMSD_A_own_production_start": rmsd_fixed(own_start, imaged[0], heavy) * 10,
            "ligand_internal_self_fit_RMSD_A": rmsd_fixed(ligand_self, reference.xyz[0, heavy], np.arange(len(heavy))) * 10,
            "reference_contact_retention_fraction": (minimum[:, initial] < 0.4).mean(axis=1),
            "contacted_residue_count_4A": (minimum < 0.4).sum(axis=1),
            "protein_ligand_minimum_heavy_distance_A": minimum.min(axis=1) * 10,
        }
        # Independent library superposition verifies the explicit fit on actual frames.
        comparison = md.Trajectory(imaged[::200], traj.topology).superpose(reference, atom_indices=ca, parallel=False)
        discrepancy = float(np.max(np.abs(comparison.xyz[:, heavy] - aligned[::200, heavy])) * 10)
        if discrepancy > 0.003:
            raise ValueError("independent MDTraj/protein Kabsch fit disagreement")
        result["independent_fit_check"] = {"frames": 11, "max_ligand_coordinate_difference_A": discrepancy, "tolerance_A": 0.003, "passed": True}
        # Geometric N/O/S proximity is explicitly not a chemically typed H bond.
        polar_lig = [i for i in lig if traj.topology.atom(i).element.symbol in {"N", "O", "S"}]
        polar_protein = [a.index for a in traj.topology.atoms if a.residue.index != traj.topology.atom(lig[0]).residue.index and ContactAnalyzer.is_protein_residue(a.residue) and a.element.symbol in {"N", "O", "S"}]
        polar_pairs = np.asarray(list(itertools.product(polar_lig, polar_protein)))
        polar_distances = md.compute_distances(traj, polar_pairs, periodic=True)
        polar_resids = sorted(set(traj.topology.atom(i).residue.index for i in polar_protein))
        polar_rows = []
        for resid in polar_resids:
            subset = [j for j, pair in enumerate(polar_pairs) if traj.topology.atom(int(pair[1])).residue.index == resid]
            distances = polar_distances[:, subset].min(axis=1)
            if np.any(distances <= 0.35):
                residue = traj.topology.residue(resid)
                polar_rows.append({"residue": f"{residue.name}{residue.resSeq}", **{f"occupancy_{w}": float((distances[mask] <= 0.35).mean()) for w, mask in masks.items()}})
        polar_rows.sort(key=lambda x: -x["occupancy_full"])
        write(folder / "polar-proximity.json", {"definition": SETTINGS["polar_proximity"], "rows": polar_rows})
        torsion_indices = np.asarray([t["solute_indices0"] for t in mapping["torsions"]])
        angles = np.rad2deg(md.compute_dihedrals(traj, torsion_indices, periodic=True))
        ref_angles = np.rad2deg(md.compute_dihedrals(reference, torsion_indices, periodic=False))[0]
        torsion_rows = []
        for j, torsion in enumerate(mapping["torsions"]):
            delta = principal_angle_delta(angles[:, j], ref_angles[j])
            for window, mask in masks.items():
                summary = circular_summary(angles[mask, j])
                torsion_rows.append({**torsion, "window": window, "frames": int(mask.sum()), "reference_deg": float(ref_angles[j]),
                                     **summary, "abs_reference_delta_median_deg": float(np.median(abs(delta[mask]))),
                                     "abs_reference_delta_gt60deg_fraction": float((abs(delta[mask]) > 60).mean())})
            values["torsion_deg_" + torsion["id"]] = angles[:, j]
        write(folder / "torsions.json", {"definition": SETTINGS["torsions"], "rows": torsion_rows})
        fields = ["time_ps", *values]
        tsv_or_csv(folder / "timeseries.csv", fields, [{"time_ps": float(traj.time[i]), **{key: float(value[i]) for key, value in values.items()}} for i in range(len(traj))])
        np.savez_compressed(folder / "frame-data.npz", time_ps=traj.time, residue_labels=np.asarray(labels), minimum_heavy_distances_nm=minimum,
                            ligand_aligned_nm=aligned[:, lig], ligand_names=np.asarray(mapping["ligand_names"]), torsion_angles_deg=angles, lattice_shifts_nm=np.asarray(shifts))
        md.Trajectory(aligned[[0, -1]], traj.topology).save_pdb(str(folder / "aligned-first-last.pdb"))
        result.update(status="analyzed", analyzed_frames=len(traj), mapping=mapping,
                      readouts={key: {name: statistics(value[mask]) for name, mask in masks.items()} for key, value in values.items() if not key.startswith("torsion_deg_")},
                      reference_contacts=[k for j, k in enumerate(labels) if initial[j]],
                      contacts=sorted([r for r in contact_rows if r["window"] == "full" and r["occupancy_lt_4A"] > 0], key=lambda r: -r["occupancy_lt_4A"]),
                      largest_contact_changes=sorted([{ "residue": label, "early_0_5ns": float((minimum[masks["0_5ns"], j] < 0.4).mean()),
                                                     "late_15_20ns": float((minimum[masks["15_20ns"], j] < 0.4).mean()),
                                                     "late_minus_early": float((minimum[masks["15_20ns"], j] < 0.4).mean() - (minimum[masks["0_5ns"], j] < 0.4).mean())}
                                                     for j, label in enumerate(labels)], key=lambda r: -abs(r["late_minus_early"]))[:20],
                      torsions=[r for r in torsion_rows if r["window"] == "full"], polar_proximity=polar_rows,
                      source_originals_unchanged={name: pin(original / name) == identity for name, identity in selected["stages"]["md"]["completion"]["artifacts"].items()})
        if not all(result["source_originals_unchanged"].values()):
            raise ValueError("original MD artifact changed during analysis")
        # Structural descriptors are deliberately not converted into an MD success label.
        result["scientific_disposition"] = "descriptive_exploratory_stability_only_no_affinity_or_convergence_inference"
        result["unknowns"].append({"capability": "chemically_typed_hydrogen_bonds", "status": "not_analyzed", "reason": SETTINGS["hbond_capability_not_used"]})
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append({"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result["cost"] = {"wall_second": time.perf_counter() - start, "controller_cpu_second": time.process_time() - cpu,
                      "children_cpu_second": after.ru_utime + after.ru_stime - children.ru_utime - children.ru_stime, "GPU_second": 0,
                      "scope": "new retrospective analysis including hashing, original coordinate QC and native analysis commands; no new MD"}
    result["artifacts"] = {str(p.relative_to(folder)): pin(p) for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "result.json"}
    write(folder / "result.json", result)
    print(json.dumps({"run_id": args.run_id, "status": result["status"], "path": str(folder / "result.json"), "cost": result["cost"], "failures": result["failures"]}))
    if result["status"] != "analyzed":
        raise SystemExit(1)


def aggregate(args):
    start, cpu = time.perf_counter(), time.process_time()
    output = args.output.absolute()
    frozen = read(output / "analysis-freeze.json")
    runs, failures, incomplete = [], [], []
    for request in frozen["runs"]:
        attempts = sorted((output / "runs" / request["id"]).glob("attempt-*"))
        successes = []
        for folder in attempts:
            if not (folder / "result.json").exists():
                incomplete.append(str(folder));continue
            row = read(folder / "result.json")
            if row["status"] == "analyzed":
                successes.append({"path": str(folder / "result.json"), **row})
            else:
                failures.append({"path": str(folder / "result.json"), **row})
        if len(successes) == 1:
            runs.append(successes[0])
        elif len(successes) > 1:
            raise ValueError("multiple successful analyses for one frozen request")
        else:
            incomplete.append(request["id"])
    by_system = {}
    for system in SYSTEMS:
        rows = [r for r in runs if r["system"] == system]
        descriptors = {}
        if rows:
            for key in rows[0]["readouts"]:
                descriptors[key] = {}
                for window in rows[0]["readouts"][key]:
                    means = [r["readouts"][key][window]["mean"] for r in rows]
                    descriptors[key][window] = {"seed_means": means, "mean_of_seed_means": float(np.mean(means)), "min_seed_mean": min(means), "max_seed_mean": max(means)}
        by_system[system] = {"replicates": len(rows), "seeds": [r["seed"] for r in rows], "descriptors": descriptors}
    all_costs = [r["cost"] for r in runs + failures]
    summary = {"schema": SCHEMA, "created_utc": now(), "status": "all_six_analyzed" if len(runs) == 6 and not incomplete else "incomplete_preserved",
               "analysis_freeze": {"path": str(output / "analysis-freeze.json"), **pin(output / "analysis-freeze.json")},
               "settings": frozen["settings"], "completed_runs": len(runs), "failed_analysis_attempts": len(failures), "incomplete": incomplete,
               "systems": by_system, "runs": runs, "failed_attempts": failures, "MD_cost": frozen["already_completed_MD"],
               "analysis_cost": {"freeze_wall_second": frozen["freeze_cost"]["wall_second"], "freeze_cpu_second": frozen["freeze_cost"]["cpu_second"],
                                 "run_attempt_wall_second_sum": sum(c["wall_second"] for c in all_costs),
                                 "run_attempt_cpu_second_sum": sum(c["controller_cpu_second"] + c["children_cpu_second"] for c in all_costs),
                                 "GPU_second": 0, "setup_transfer_and_human_time_second": None, "scope": "sum of nonoverlapping sequential analysis attempt intervals; setup/import probes and local transfers excluded and separately retained"},
               "scope_disposition": "do_not_promote_method_ranking_affinity_or_convergence_from_control_stability",
               "r4b": "paused"}
    summary["analysis_cost"]["aggregation_wall_second_before_serialization"] = time.perf_counter() - start
    summary["analysis_cost"]["aggregation_cpu_second_before_serialization"] = time.process_time() - cpu
    write(output / "summary.json", summary)
    manifest = {"schema": "snd1-md-analysis-manifest/1", "created_utc": now(), "root": str(output),
                "files": {str(p.relative_to(output)): pin(p) for p in sorted(output.rglob("*")) if p.is_file() and p.name != "manifest.json"},
                "source_originals": frozen["inputs"], "originals_not_modified": all(all(r["source_originals_unchanged"].values()) for r in runs)}
    write(output / "manifest.json", manifest)
    print(json.dumps({"status": summary["status"], "completed_runs": len(runs), "failed_analysis_attempts": len(failures), "incomplete": incomplete}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze", "run", "aggregate"))
    parser.add_argument("--p4", type=Path, default=Path("/home/zx/etalon/runs/snd1-scale-20261024/p4"))
    parser.add_argument("--prism-source", type=Path, default=Path("/home/zx/etalon/runs/snd1-scale-20261024/p4/source"))
    parser.add_argument("--gmx", type=Path, default=Path("/opt/gromacs2026/bin/gmx"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    {"freeze": freeze, "run": run, "aggregate": aggregate}[args.operation](args)


if __name__ == "__main__":
    main()
