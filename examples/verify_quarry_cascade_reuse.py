"""Capture source evidence and re-read an existing scientific chain without running it.

The capture mode makes explicitly new, bounded public queries. Neither mode calls
Screen.run/resume, conformer generation, docking, MD or scientific scoring. The
read mode refuses missing snapshots instead of silently acquiring them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from etalon.boundary.quarry import DataBudget
from etalon.boundary.screen import Screen
from etalon.data.artifacts import read_snapshot, write_json
from etalon.data.service import plan_data, run_data
from etalon.data.source_archive import source_archive_status
from quarry_cascade_research import review, review_records


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def verify(root: Path, previous: Path, *, capture: bool):
    root, previous = root.absolute(), previous.absolute()
    if root == previous or root.is_relative_to(previous) or previous.is_relative_to(root):
        raise ValueError("source capture must use a separate task directory")
    root.mkdir(parents=True, exist_ok=True)
    selection = read(previous / "selection.json")
    if (root / "selection.json").exists():
        if read(root / "selection.json") != selection:
            raise ValueError("selected input identities changed")
    elif capture:
        write_json(root / "selection.json", selection)
    else:
        raise FileNotFoundError(root / "selection.json")
    sources = {}
    new_requests = 0
    for name in ("pubchem-properties", "rcsb-entry", "rcsb-polymer"):
        old_root, new_root = previous / "data" / name, root / "data" / name
        old = read_snapshot(old_root)
        request = read(old_root / "request.json")["request"]
        if request["kind"] != "query" or request["source"] not in {"pubchem", "rcsb"}:
            raise ValueError("this bounded acceptance only supports the selected public queries")
        acquired = False
        if not (new_root / "snapshot.json").exists():
            if not capture:
                raise FileNotFoundError(new_root / "snapshot.json")
            # Partial attempts remain visible and are not silently retried.
            if new_root.exists():
                raise FileExistsError(f"unsealed prior attempt requires explicit review: {new_root}")
            budget = DataBudget(max_requests=3, max_bytes=2_000_000, max_seconds=45)
            plan = plan_data(request, budget=budget)
            value = run_data(request, root, run_id=name, budget=budget, expected_plan_id=plan["plan_id"])
            new_requests += value["usage"]["requests"]
            acquired = True
        new = read_snapshot(new_root)
        if new["result"]["status"] != "complete":
            raise ValueError(f"incomplete new retrieval retained: {new_root}")
        if read(new_root / "request.json")["request"] != request:
            raise ValueError("new retrieval does not match the explicit source query")
        archive = source_archive_status(new)
        if archive.get("status") != "captured" or archive.get("coverage", {}).get("complete_bodies", 0) < 1:
            raise ValueError("new source verification requires a sealed original body")
        sources[name] = {"old_snapshot_id": old["snapshot_id"], "new_snapshot_id": new["snapshot_id"],
                         "legacy_coverage": source_archive_status(old),
                         "current_coverage": archive,
                         "acquired_this_invocation": acquired,
                         "meaning": "new retrieval, not recovered historical response bytes"}
    if capture and not (root / "review.json").exists():
        review(root, "explicit source identity review for archival acceptance/1")
    reviewed = read(root / "review.json")
    actual = review_records(read(root / "data/pubchem-properties/payload/records.json"), selection["candidates"])
    old_reviewed = read(previous / "review.json")
    if actual != reviewed["candidates"] or actual != old_reviewed["candidates"]:
        raise ValueError("new source chemical identities differ; old computation cannot represent this input")
    if reviewed["source_snapshot_id"] != sources["pubchem-properties"]["new_snapshot_id"]:
        raise ValueError("source review no longer binds the verified snapshot")
    polymers = read(root / "data/rcsb-polymer/payload/records.json")
    if len(polymers) != 1 or reviewed["target_context"] != polymers[0]:
        raise ValueError("source review no longer binds the verified target context")

    screen = Screen.open_existing(previous / "screen")
    result = screen.state("snd1-native-screen")
    if result is None or result.status != "SUCCEEDED":
        raise ValueError("expected completed prior scientific result is unavailable")
    stage_rows = {stage.stage_id: {"artifact_id": stage.artifact_id,
                  "rows": len(screen.read(stage.artifact_id))} for stage in result.committed}
    handoff = screen.handoff(result)
    if handoff != read(previous / "handoff.json"):
        raise ValueError("verified handoff differs from the retained original")
    return {"schema": "etalon-component-reuse-verification/1", "mode": "capture" if capture else "read",
            "sources": sources, "new_http_requests": new_requests,
            "source_identity_matches_prior_review": True,
            "scientific_execution_count": 0, "proposal_count": 0,
            "prior_scientific_run": {"root": str(previous), "run_id": result.run_id,
                "status": result.status, "verified_stage_rows": stage_rows,
                "handoff_rows": len(handoff), "handoff_content_digest": digest(handoff)},
            "limitations": ["New source acquisition does not repair missing historical original responses.",
                            "Source identity and output verification do not establish scientific benefit.",
                            "No complete MD-prepared system or assay admission is implied."],
            "r4b": "paused"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "read"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.previous, capture=args.mode == "capture"), ensure_ascii=False))
