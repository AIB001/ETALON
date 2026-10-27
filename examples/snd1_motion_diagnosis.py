"""Reanalyze four saved QOV trajectories in an isolated output directory.

Usage: python snd1_motion_diagnosis.py CONFIG.json
CONFIG names result, source_config, output. It never writes a project entry or
starts MD. The host may publish the resulting evidence through its own journal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

from etalon.runtime.files import file_identity, _save
from etalon.runtime.reuse import execute_or_reuse, request_identity
from etalon.rescore import contacts, motion
from etalon.active.schema import digest
from etalon.research import result_semantics


METHOD = {
    "schema": "snd1-saved-motion-protocol/2",
    "reference": "first saved frame of each trajectory; no choice by later occupancy",
    "alignment": "fixed protein CA within 15 A of reference ligand heavy centroid",
    "ligand_rigid": "proper fit of existing rooted scaffold core atoms",
    "ligand_internal": "all coordinate departures from this rigid fit; not assumed torsion",
    "protein_reset": "all supplied non-ligand atom coordinates; not restricted to contact atoms",
    "contacts": "all existing chemically typed triples for ASN281 and ARG255",
    "distance_A": 3., "angle_deg": 150., "late_start_ps": 50.,
    "factor_order": list(motion.FACTORS), "alignment_radius_A": 15.,
    "legacy_factor_labels": {"protein_contact_atoms": "all supplied non-ligand atoms"},
    "frame_statistics": "descriptive counts, correlated frames, keep seeds and modes separate",
    "scope": "coordinate counterfactuals; no force, affinity or realizable-intervention claim",
    "follow_through": "compare torsion-only reset to full internal residual using the same saved coordinates",
}


def selection_identity(indices):
    """Compact binding to actual indices; complete selections remain in NPZ."""
    values = np.asarray(indices, dtype=int).tolist()
    return dict(count=len(values), sha256=digest(values))


def operation_summary(operation):
    return {key: selection_identity(value) if key.endswith("_indices") else value
            for key, value in operation.items()}


def result_profile(method, *, successor=False):
    """A domain profile, using the same generic descriptor as other producers."""
    if successor:
        metrics = {
            "residual_from_full_internal_A": dict(unit="angstrom",
                definition="Mean of per-frame unweighted RMSD over every supplied ligand atom, including explicit H, between torsion-reset coordinates and reference ligand at current core rigid transform"),
            "valid_by_reset": dict(unit="frame_count",
                definition="Count of selected frames satisfying every stated shape, stereo, strain and actual mixed-environment overlap condition for each reset mask"),
            "valid_dual_by_reset": dict(unit="frame_count",
                definition="Count satisfying the same QC plus at least one typed contact for each of ASN281 and ARG255 in the same candidate"),
            "mean_strain_by_reset": dict(unit="kcal/mol",
                definition="Mean MMFF94s ligand energy minus energy of current ligand in that frame, by reset mask; includes all selected frames"),
            "min_vdw_ratio_by_reset": dict(unit="dimensionless",
                definition="Minimum over selected frames and ligand-heavy/environment-heavy atom pairs of separation divided by sum of vdW radii; environment is the actual mixed coordinate array after that mask's reset"),
        }
    else:
        metrics = {
            "core_translation_A": dict(unit="angstrom", definition="Mean over selected late frames of displacement of core centroid after protein alignment"),
            "core_rotation_deg": dict(unit="degree", definition="Mean over selected late frames of proper reference-core/current-core rigid rotation angle"),
            "internal_rmsd_A": dict(unit="angstrom", definition="Mean over selected late frames of per-frame unweighted RMSD over every supplied ligand atom, including explicit H, from reference ligand at current core rigid transform"),
            "alignment_rmsd_A": dict(unit="angstrom", definition="Mean over selected late frames of protein-alignment atom RMSD from first-frame reference"),
            "late_torsion_abs_delta_deg": dict(unit="degree", definition="Mean over selected late frames of absolute wrapped dihedral departure from first saved frame, by rooted rotor"),
        }
    metrics["contact_summary"] = dict(unit="frame_count",
        definition="Descriptive correlated-frame counts by endpoint and time window, with minimal successful reset masks; alternatives and full-reset failures retained; not causal fractions")
    return result_semantics.build_result_profile(
        profile_id="snd1-linker-reset/2" if successor else "snd1-motion/2",
        method=method, metrics=metrics,
        conditions={"selection": "Actual zero-based atom selections in the saved local coordinate array; counts and SHA256 of canonical integer lists bind arrays stored in NPZ",
                    "frame_window": "Actual selected frame indices and times; frames remain correlated",
                    "qc_environment": "Actual environment selection and source used by each mask, or not_performed"})


def successor_candidate(aligned, reference, ligand, core, torsion_reset, mask,
                        environment_indices, ligand_heavy, environment_heavy,
                        ligand_radii, environment_radii, *, core_transform=None):
    """Construct the actual candidate and evaluate overlap against that array.

    Source metadata is derived from the indices assigned and used in separation,
    so whole-environment reset cannot be mislabeled as current-environment QC.
    """
    ligand, core = np.asarray(ligand, int), np.asarray(core, int)
    environment_indices = np.asarray(environment_indices, int)
    environment_heavy = np.asarray(environment_heavy, int)
    if mask not in range(8):
        raise ValueError("Reset mask must be between zero and seven")
    rotation, shift = (motion.rigid_fit(reference[core], aligned[core])
                       if core_transform is None else core_transform)
    mixed = aligned.copy()
    pose = torsion_reset if mask & 2 else aligned[ligand]
    if mask & 1:
        pose = (pose-shift) @ rotation.T
    mixed[ligand] = pose
    reset = environment_indices if mask & 4 else np.array([], dtype=int)
    mixed[reset] = reference[reset]
    # Use precisely this array for both the numerical QC and its source binding.
    qc_environment = mixed[environment_heavy]
    separation = np.linalg.norm(pose[np.asarray(ligand_heavy), None]-qc_environment, axis=-1)
    radius_sum = np.asarray(ligand_radii)[np.asarray(ligand_heavy), None]+np.asarray(environment_radii)[environment_heavy]
    ratio = float(np.min(separation/radius_sum))
    from_reference = np.isin(environment_heavy, reset)
    operation = dict(mask=int(mask),
        ligand_transform="remove_core_rigid_transform" if mask & 1 else "current_core_transform",
        ligand_internal="rooted_torsion_reset" if mask & 2 else "current",
        ligand_target_indices=ligand.tolist(), environment_reset_indices=reset.tolist(),
        qc_environment_indices=environment_heavy.tolist(),
        qc_reference_indices=environment_heavy[from_reference].tolist(),
        qc_current_indices=environment_heavy[~from_reference].tolist())
    return mixed, ratio, operation


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def freeze(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError("Frozen output differs: " + str(path))
    else:
        _save(path, value)


def dependencies():
    root = Path(__file__).parents[1]
    files = [Path(__file__), Path(motion.__file__), Path(contacts.__file__),
             Path(result_semantics.__file__), root / "src/etalon/active/schema.py",
             root / "src/etalon/runtime/reuse.py", root / "src/etalon/runtime/files.py"]
    files += [Path(__file__).parent/name for name in (
        "snd1_feedback_experiment.py", "snd1_anchor_intervention.py",
        "snd1_contact_program.py", "snd1_feedback_search.py",
        "snd1_paired_relaxation.py", "snd1_task_learning.py")]
    return {str(path): file_identity(path) for path in files}


def dataset(config):
    prior = read(config["source_config"])
    pair_paths = list((Path(prior["case_root"])/"pairs").glob("*/pair.json"))
    if len(pair_paths) != 1:
        raise ValueError("Expected one frozen mapped case")
    pair_path = pair_paths[0]
    pair = read(pair_path)
    native = Path(prior["case_root"])/"primary"/pair["run_id"]/"input"
    files = [Path(config["result"]), Path(config["source_config"]), pair_path,
             pair_path.parent/"exact-input.sdf", pair_path.parent/"frame.gro",
             native/"ligand.itp", native/"topol.top"]
    rows = read(config["result"])["rows"]
    successful = [row for row in rows if row["status"] == "succeeded"]
    for row in successful:
        folder = Path(row["handle"]).parent
        files += [folder/"em.gro", Path(row["handle"])/"md.xtc", folder/"CONTACTS.json"]
    return prior, pair, successful, files


def summaries(hit, labels, times):
    result = {}
    for index, label in enumerate([*labels, "ASN281_and_ARG255"]):
        values = hit[:, :, index] if index < len(labels) else hit.all(axis=2)
        result[label] = {
            "all": motion.minimal_reset_summary(values),
            "early": motion.minimal_reset_summary(values, times <= 5),
            "late": motion.minimal_reset_summary(values, times >= METHOD["late_start_ps"]),
        }
    return result


def run(config, *, interrupt_after_result=False):
    import mdtraj as md
    import rdkit
    from rdkit import RDConfig
    import snd1_feedback_experiment as experiment
    import snd1_anchor_intervention as anchor

    output = Path(config["output"])
    if not output.is_absolute():
        raise ValueError("Absolute output directory required")
    output.mkdir(parents=True, exist_ok=True)
    prior, pair, original_rows, files = dataset(config)
    files.append(Path(RDConfig.RDDataDir)/"BaseFeatures.fdef")
    freeze(output/"METHOD.json", METHOD)
    profile = result_profile(METHOD)
    freeze(output/"RESULT_PROFILE.json", profile)
    environment = {"python": sys.executable, "numpy": np.__version__,
                   "numpy_path": np.__file__, "mdtraj": md.__version__,
                   "mdtraj_path": md.__file__, "rdkit": rdkit.__version__,
                   "rdkit_path": rdkit.__file__, "motion_path": motion.__file__}
    request = request_identity(operation="snd1.saved-motion-factorial",
        implementation=dependencies(), environment=environment,
        inputs={str(path): file_identity(path) for path in files}, parameters=METHOD)
    result_path = output/"RESULT.json"

    def produce():
        start, cpu = time.perf_counter(), time.process_time()
        ctx, *_ = experiment.context(prior)
        ligand = np.arange(ctx["offset"], ctx["offset"]+len(ctx["ligand"]))
        core = ctx["offset"]+np.asarray(ctx["chemical"]["core"])
        names = np.asarray(ctx["case"]["triple_residues"])
        keep = np.isin(names, ["ASN281", "ARG255"])
        triples, names = ctx["case"]["triples"][keep], names[keep]
        protein, _ = experiment.chemistry.topology_sections(
            Path(prior["case_root"])/"primary"/pair["run_id"]/"input"/"topol.top")
        from rdkit import Chem
        protein_radii = np.array([Chem.GetPeriodicTable().GetRvdw(a.element.symbol)
            for a in ctx["case"]["trajectory"].topology.atoms if a.index < ctx["offset"]])
        rows, artifacts = [], [output/"METHOD.json", output/"RESULT_PROFILE.json"]
        for old in original_rows:
            folder = Path(old["handle"]).parent
            trajectory = md.load(str(Path(old["handle"])/"md.xtc"), top=str(folder/"em.gro"))
            xyz = trajectory.xyz[:, :int(ligand[-1])+1].astype(float)*10
            boxes = trajectory.unitcell_vectors.astype(float)*10
            center = xyz[0, ctx["offset"]+np.asarray(ctx["chemical"]["heavy"])].mean(0)
            ca = np.array([a.index for a in trajectory.topology.atoms
                           if a.index < ctx["offset"] and a.name == "CA"])
            distance = np.linalg.norm(motion.minimum_image(xyz[0, ca]-center, boxes[0]), axis=1)
            alignment = ca[distance <= METHOD["alignment_radius_A"]]
            diagnostic = motion.factorial_contacts(xyz, boxes, ligand, core,
                                                    alignment, triples, names)
            saved = read(folder/"CONTACTS.json")
            if len(saved) != len(xyz):
                raise ValueError("Saved contact count differs")
            for index, name in enumerate(diagnostic["labels"]):
                if diagnostic["hit"][:, 0, index].tolist() != [r[name]["primary"] for r in saved]:
                    raise ValueError("Native contact endpoint changed")
            aligned, reference = diagnostic["aligned"], diagnostic["reference"]
            bonds = ctx["chemical"]["bonds"]
            bond_lengths = np.linalg.norm(aligned[:, ligand[bonds[:, 0]]]-
                                           aligned[:, ligand[bonds[:, 1]]], axis=-1)
            if bond_lengths.max() > 2.2:
                raise ValueError("Local ligand image has broken bonds")
            torsions = np.array([anchor.dihedral(aligned[:, ligand], r["dihedral"])
                                 for r in ctx["chemical"]["rotors"]]).T
            torsion_delta = anchor.wrap(torsions-torsions[0])
            late = trajectory.time >= METHOD["late_start_ps"]
            environment_indices = np.setdiff1d(np.arange(xyz.shape[1]), ligand)
            heavy_indices = ligand[np.asarray(ctx["chemical"]["heavy"], dtype=int)]
            hydrogen_indices = np.setdiff1d(ligand, heavy_indices)
            prefix = old["mode"]+"-"+str(old["seed"])
            data_path = output/(prefix+".npz")
            np.savez_compressed(data_path, time_ps=trajectory.time, reference=reference,
                aligned=aligned, ligand_indices=ligand, core_indices=core,
                alignment_indices=alignment, triples=triples, triple_labels=names,
                environment_indices=environment_indices, heavy_indices=heavy_indices,
                hydrogen_indices=hydrogen_indices,
                labels=np.asarray(diagnostic["labels"]), hit=diagnostic["hit"],
                distance_A=diagnostic["distance"], angle_deg=diagnostic["angle"],
                torsion_deg=torsions, protein_radii=protein_radii,
                **diagnostic["metrics"])
            artifacts.append(data_path)
            rows.append(dict(mode=old["mode"], seed=old["seed"], frames=len(xyz),
                reference_time_ps=float(trajectory.time[0]), data=str(data_path),
                summary=summaries(diagnostic["hit"], diagnostic["labels"], trajectory.time),
                late_mean={k: float(v[late].mean()) if late.any() else None for k, v in diagnostic["metrics"].items()},
                late_torsion_abs_delta_deg=np.abs(torsion_delta[late]).mean(0).tolist() if late.any() else None,
                rotors=ctx["chemical"]["rotors"], alignment_indices=alignment.tolist(),
                core_indices=core.tolist(), triples=len(triples),
                native_max_error=diagnostic["max_native_error"].tolist(),
                original_endpoint_equal=True, max_ligand_bond_A=float(bond_lengths.max())))
            row = rows[-1]
            row["scientific_result"] = result_semantics.bind_scientific_result(profile,
                subjects=[dict(mode=old["mode"], seed=old["seed"], input=file_identity(Path(old["handle"])/"md.xtc"))],
                operation=dict(resets=[operation_summary(op) for op in diagnostic["operations"]],
                               coordinate_array="all trajectory atoms through last ligand index"),
                conditions=dict(selection={key: selection_identity(value) for key, value in
                    dict(ligand=ligand, ligand_heavy=heavy_indices, ligand_hydrogen=hydrogen_indices,
                         core=core, alignment=alignment, environment=environment_indices).items()},
                    frame_window=dict(reference_time_ps=float(trajectory.time[0]), late_start_ps=METHOD["late_start_ps"],
                                      late_frames=int(late.sum()), selected_indices=selection_identity(np.flatnonzero(late))),
                    qc_environment=dict(status="not_performed", scope="native endpoint and ligand bond checks only")),
                observations={**row["late_mean"], "late_torsion_abs_delta_deg": row["late_torsion_abs_delta_deg"],
                              "contact_summary": row["summary"]},
                assessment=dict(original_endpoint_equal=True, native_max_error=row["native_max_error"],
                                max_ligand_bond_A=row["max_ligand_bond_A"]))
        result = dict(schema="snd1-saved-motion-result/2", rows=rows, semantic_profile=profile,
            request_digest=request["request_digest"], environment=environment,
            method=METHOD, new_MD=0, original_rows=len(original_rows),
            cost={"wall_seconds": time.perf_counter()-start, "cpu_seconds": time.process_time()-cpu},
            full_host_cost=None, claims="Descriptive geometry; reset counts are not causal fractions",
            artifacts=[str(path) for path in artifacts])
        freeze(result_path, result)
        if interrupt_after_result:
            raise SystemExit(75)
        return {"result": result, "artifacts": [result_path, *artifacts]}

    def recover():
        result = read(result_path)
        if result["request_digest"] != request["request_digest"]:
            raise ValueError("Saved result belongs to another request")
        return {"result": result, "artifacts": [result_path, *map(Path, result["artifacts"])]}

    execution = execute_or_reuse(output/"execution", request, produce, recover=recover)
    if execution["receipt"]["status"] != "complete":
        raise RuntimeError(execution["receipt"]["error"])
    result = execution["receipt"]["result"]
    return dict(result=str(result_path), receipt=execution["receipt_path"],
                reused=execution["reused"], call_cost=execution["call_cost"],
                science_cost=result["cost"], new_MD=0)


def successor(config):
    """Test rooted linker rotations after the broad internal-reset diagnosis.

    Linker rotations construct poses, but no force-relaxed intervention is
    performed. Geometry and QC are reported separately for every reset mask.
    """
    import snd1_feedback_experiment as experiment
    import snd1_anchor_intervention as anchor
    import mdtraj as md
    import rdkit

    start, cpu = time.perf_counter(), time.process_time()
    prior_result = read(config["motion_result"])
    prior, pair, _, original_files = dataset(config)
    output = Path(config["output"])
    output.mkdir(parents=True, exist_ok=True)
    method = dict(schema="snd1-linker-reset-successor/2",
        question="Do three rooted linker torsion resets reproduce full internal reset rescue?",
        motivation="Motion factorial found almost all late dual losses require coupled resets",
        reference="same first saved frame; no angle fit to later contact outcomes",
        frames="all saved frames at or after 50 ps",
        internal_change="existing three rooted linker rotors set to reference dihedrals",
        other_factors=list(motion.FACTORS), distance_A=3., angle_deg=150.,
        environment_reset="all supplied protein atoms, including atoms outside contact triples",
        qc_environment="protein heavy coordinates from actual mixed array; reference after environment reset, current otherwise",
        legacy_factor_labels={"protein_contact_atoms": "all supplied protein atoms"},
        quality=dict(stereo_preserved=True, max_bond_change_A=1e-7,
                     max_bond_angle_change_deg=1e-6, min_protein_vdw_ratio=.65,
                     max_MMFF94s_strain_change_from_current_kcal_mol=10.),
        scope="constructed, unrelaxed coordinate candidates; no dynamics or affinity evidence",
        frozen_after_motion_digest=prior_result["request_digest"])
    freeze(output/"METHOD.json", method)
    profile = result_profile(method, successor=True)
    freeze(output/"RESULT_PROFILE.json", profile)
    files = [Path(config["motion_result"]), *original_files]
    files += [Path(row["data"]) for row in prior_result["rows"]]
    request = request_identity(operation="snd1.restricted-linker-reset-successor",
        implementation=dependencies(), environment={"python": sys.executable,
            "numpy": np.__version__, "numpy_path": np.__file__,
            "mdtraj": md.__version__, "mdtraj_path": md.__file__,
            "rdkit": rdkit.__version__, "rdkit_path": rdkit.__file__,
            "motion_path": motion.__file__},
        inputs={str(path): file_identity(path) for path in files}, parameters=method)
    result_path = output/"RESULT.json"

    def produce():
        begin, process = time.perf_counter(), time.process_time()
        ctx, *_ = experiment.context(prior)
        chemical = ctx["chemical"]
        native = Path(prior["case_root"])/"primary"/pair["run_id"]/"input"
        protein, _ = experiment.chemistry.topology_sections(native/"topol.top")
        protein_heavy = np.array([a["index"] for a in protein if a["mass"] >= 2])
        heavy = np.asarray(chemical["heavy"])
        rows, artifacts = [], [output/"METHOD.json", output/"RESULT_PROFILE.json"]
        for old in prior_result["rows"]:
            data = np.load(old["data"], allow_pickle=False)
            ligand, core = data["ligand_indices"], data["core_indices"]
            triples, names, labels = data["triples"], data["triple_labels"], data["labels"].tolist()
            selected = np.flatnonzero(data["time_ps"] >= 50)
            reference = data["reference"]
            targets = [anchor.dihedral(reference[ligand], r["dihedral"]) for r in chemical["rotors"]]
            hit = np.empty((len(selected), 8, len(labels)), bool)
            quality = np.empty((len(selected), 8), bool)
            strain = np.empty_like(quality, dtype=float)
            ratio = np.empty_like(strain)
            residual = np.empty(len(selected))
            restoration_error = []
            operations = []
            environment_indices = np.arange(ctx["offset"])
            heavy_indices = ligand[heavy]
            hydrogen_indices = np.setdiff1d(ligand, heavy_indices)
            for fi, original_index in enumerate(selected):
                aligned = data["aligned"][original_index]
                current = aligned[ligand]
                torsion_reset = anchor.set_torsions(current, chemical["rotors"], targets)
                restoration_error.extend(np.abs(anchor.wrap(np.asarray([
                    anchor.dihedral(torsion_reset, r["dihedral"]) for r in chemical["rotors"]])-targets)))
                rotation, shift = motion.rigid_fit(reference[core], aligned[core])
                residual[fi] = np.sqrt(np.mean(np.sum((torsion_reset-
                    (reference[ligand] @ rotation+shift))**2, axis=1)))
                initial_energy = chemical["forcefield"].CalcEnergy(current.ravel().tolist())
                for mask in range(8):
                    mixed, ratio[fi, mask], operation = successor_candidate(
                        aligned, reference, ligand, core, torsion_reset, mask,
                        environment_indices, heavy, protein_heavy,
                        chemical["radii"], data["protein_radii"], core_transform=(rotation, shift))
                    if fi == 0:
                        operations.append(operation_summary(operation))
                    pose = mixed[ligand]
                    distance, angle = motion.euclidean_geometry(mixed, triples)
                    active = (distance <= 3.) & (angle >= 150.)
                    hit[fi, mask] = [active[names == label].any() for label in labels]
                    if mask in (0, 1, 4, 5):
                        if not np.array_equal(hit[fi, mask], data["hit"][original_index, mask]):
                            raise ValueError("Non-internal reset changed between analyses")
                    bond, bond_angle, stereo = anchor.shape_check(pose, current, chemical)
                    strain[fi, mask] = chemical["forcefield"].CalcEnergy(pose.ravel().tolist())-initial_energy
                    quality[fi, mask] = (stereo and bond <= 1e-7 and bond_angle <= 1e-6 and
                                         strain[fi, mask] <= 10 and ratio[fi, mask] >= .65)
            filename = output/(old["mode"]+"-"+str(old["seed"])+".npz")
            np.savez_compressed(filename, time_ps=data["time_ps"][selected], hit=hit,
                quality=quality, strain_change_kcal_mol=strain, min_vdw_ratio=ratio,
                residual_from_full_internal_A=residual, ligand_indices=ligand,
                core_indices=core, heavy_indices=heavy_indices, hydrogen_indices=hydrogen_indices,
                environment_indices=environment_indices, qc_environment_indices=protein_heavy)
            artifacts.append(filename)
            rows.append(dict(mode=old["mode"], seed=old["seed"], frames=len(selected),
                data=str(filename), summary=summaries(hit, labels, data["time_ps"][selected]),
                valid_by_reset={str(m): int(quality[:, m].sum()) for m in range(8)},
                valid_dual_by_reset={str(m): int((quality[:, m] & hit[:, m].all(axis=1)).sum()) for m in range(8)},
                dual_by_full_internal_reset=old["summary"]["ASN281_and_ARG255"]["late"]["contact_by_reset"],
                mean_residual_from_full_internal_A=float(residual.mean()) if len(selected) else None,
                max_torsion_restoration_error_deg=float(max(restoration_error)) if restoration_error else None,
                mean_strain_by_reset={str(m): float(strain[:, m].mean()) if len(selected) else None for m in range(8)},
                min_vdw_ratio_by_reset={str(m): float(ratio[:, m].min()) if len(selected) else None for m in range(8)}))
            row = rows[-1]
            row["scientific_result"] = result_semantics.bind_scientific_result(profile,
                subjects=[dict(mode=old["mode"], seed=old["seed"], input=file_identity(Path(old["data"])))],
                operation=dict(resets=operations, coordinates="constructed_unrelaxed", new_MD=0),
                conditions=dict(selection={key: selection_identity(value) for key, value in
                    dict(ligand=ligand, ligand_heavy=heavy_indices, ligand_hydrogen=hydrogen_indices,
                         core=core, environment=environment_indices).items()},
                    frame_window=dict(late_start_ps=50., frames=len(selected), selected_indices=selection_identity(selected)),
                    qc_environment=dict(selection=selection_identity(protein_heavy),
                        sources_by_reset=[dict(mask=op["mask"], reference=op["qc_reference_indices"],
                                               current=op["qc_current_indices"]) for op in operations])),
                observations={key: row[key] for key in ("valid_by_reset", "valid_dual_by_reset", "mean_strain_by_reset", "min_vdw_ratio_by_reset")} |
                    dict(contact_summary=row["summary"], residual_from_full_internal_A=row["mean_residual_from_full_internal_A"]),
                assessment=dict(max_torsion_restoration_error_deg=row["max_torsion_restoration_error_deg"],
                                scope="constructed geometry and specified QC only"))
        result = dict(schema="snd1-linker-reset-successor-result/2", method=method, rows=rows,
            semantic_profile=profile,
            request_digest=request["request_digest"], motion_result=config["motion_result"],
            cost={"wall_seconds": time.perf_counter()-begin, "cpu_seconds": time.process_time()-process},
            new_MD=0, full_host_cost=None, artifacts=[str(p) for p in artifacts])
        freeze(result_path, result)
        return {"result": result, "artifacts": [result_path, *artifacts]}

    def recover():
        result = read(result_path)
        if result["request_digest"] != request["request_digest"]:
            raise ValueError("Saved successor belongs to another request")
        return {"result": result, "artifacts": [result_path, *map(Path, result["artifacts"])]}

    execution = execute_or_reuse(output/"execution", request, produce, recover=recover)
    if execution["receipt"]["status"] != "complete":
        raise RuntimeError(execution["receipt"]["error"])
    return dict(result=str(result_path), receipt=execution["receipt_path"], reused=execution["reused"],
                call_cost=execution["call_cost"], science_cost=execution["receipt"]["result"]["cost"],
                total_invocation_cost={"wall_seconds":time.perf_counter()-start,
                                       "cpu_seconds":time.process_time()-cpu})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--interrupt-after-result", action="store_true")
    parser.add_argument("--successor", action="store_true")
    args = parser.parse_args()
    print(json.dumps(successor(read(args.config)) if args.successor else
                     run(read(args.config), interrupt_after_result=args.interrupt_after_result)))
