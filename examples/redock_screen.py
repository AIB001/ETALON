"""Run a bounded Uni-Dock → independent redock → original-pose handoff screen.

Requires ETALON's cascade extra, Meeko, PoseBusters and a working Uni-Dock GPU
executable. Supply a CSV with id,smiles, a prepared receptor PDB and its matching
PDBQT in the same coordinate frame. All box dimensions are in angstrom.
Use --plan-only to inspect the compiled protocol without docking.

The default RMSD < 2 A gate tests prediction consistency, not agreement with an
experimental structure. This example creates handoff evidence; PRISM preflight,
protonation review and spend authorization remain separate operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose


def configuration(args: argparse.Namespace) -> dict:
    common = {
        "receptor_path": str(args.receptor.resolve()),
        "receptor_pdbqt_path": str(args.receptor_pdbqt.resolve()),
        **dict(zip(("center_x", "center_y", "center_z"), args.center, strict=True)),
        **dict(zip(("size_x", "size_y", "size_z"), args.size, strict=True)),
        "seed": args.seed, "search_mode": args.search_mode, "keep_poses": True,
        "max_molecules": args.max_molecules, "timeout_per_molecule_seconds": args.timeout,
    }
    if args.executable:
        common["executable"] = str(args.executable.resolve())
    repeat = {**common, "source_seed": args.seed, "seed": args.seed % (2**31 - 2) + 1}
    gate = {"backend": "derived.numeric_evidence_gate@0.1.0", "settings": {
        "schema_version": 1, "metric_id": "dock_redock_rmsd", "expected_units": "ANGSTROM",
        "expected_direction": "LOWER_BETTER", "maximum": 2.0,
        "maximum_exclusive": True, "on_unscorable": "reject",
    }}
    return compose("dock-redock-handoff", [{
        "id": "screen", "title": "Pose consistency before simulation", "criteria": [
            component("conformers", "docking.rdkit_conformers@0.1.0", settings={"seed": args.seed}),
            component("dock", "docking.unidock@0.2.0", settings=common),
            component("redock", "docking.redock_consistency@0.1.0", settings=repeat,
                      evidence_from={"docking_score/v1": "dock"}, gate=gate),
        ],
    }])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("library", "receptor", "receptor-pdbqt", "workspace"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--executable", type=Path, help="Uni-Dock binary; otherwise use the configured engine path")
    parser.add_argument("--center", type=float, nargs=3, required=True)
    parser.add_argument("--size", type=float, nargs=3, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument("--search-mode", choices=("fast", "balance", "detail"), default="balance")
    parser.add_argument("--max-molecules", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--protonation-state-id", help="Actual input preparation provenance, when known")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    root = args.workspace.resolve()
    root.mkdir(parents=True, exist_ok=False)
    screen = Screen(root)
    authored = root / "screen.json"
    authored.write_text(json.dumps(configuration(args), indent=2), encoding="utf-8")
    complete = screen.with_handoff(
        authored, root / "handoff.yaml", protonation_state_id=args.protonation_state_id,
        evidence_from={"docking_score/v1": "dock"},
    )
    plan = screen.plan(complete, args.library.resolve())
    report = {"plan": plan.as_dict(), "infrastructure": screen.infra.provenance(),
              "interpretation": "prediction_consistency_not_crystal_pose_accuracy",
              "acceptance": "finite fixed-frame symmetry-corrected heavy-atom RMSD < 2.0 angstrom",
              "inputs_sha256": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (args.library, args.receptor, args.receptor_pdbqt)}}
    if not args.plan_only:
        result = screen.run(plan, run_id="redock-screen", workers=1, devices=(args.device,))
        report["run"] = result.as_dict()
        if result.status == "SUCCEEDED":
            def read_stage(stage_id, contract):
                stage = next(stage for stage in result.committed if stage.stage_id == stage_id)
                return screen.read(stage.artifact_id, contract_id=contract)

            metrics = read_stage("redock", "derived_metric/v1")
            poses = {row["parent_id"]: row for row in read_stage("dock", "docking_score/v1")
                     if row["pose_rank"] == 0}
            final = screen.artifact_carrying(result, "parent/v1")
            survivors = {row["parent_id"] for row in screen.read(final, contract_id="parent/v1")} if final else set()
            handoffs = [row for row in screen.handoff(result) if row["parent_id"] in survivors]
            retained = all(row["coordinate_source"] == "DOCKED_POSE"
                           and row["molblock"] == poses[row["parent_id"]]["pose_molblock"]
                           for row in handoffs)
            if not retained:
                raise RuntimeError("handoff coordinates differ from the original scored docking pose")
            (root / "redock-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            (root / "accepted-handoffs.json").write_text(json.dumps(handoffs, indent=2), encoding="utf-8")
            report.update(measured=len(metrics), accepted_handoffs=len(handoffs),
                          original_pose_preserved=retained if handoffs else None,
                          rmsd_angstrom=[row["value"] for row in metrics])
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not args.plan_only and report["run"]["status"] != "SUCCEEDED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
