"""Optional local batch of existing action-result reads; edit or skip this example.

Only observes supplied handles. It never submits, retries, reconciles, or admits
science. Full per-item snapshots remain available; stdout is for status/recovery
inspection, not a replacement for the evidence needed for scientific inference.

    python examples/local_sdk_batch.py --handles handles.json --output new-evidence

The input is a JSON array of complete etalon-action-handle/1 objects returned by
submit_action, including schema/backend/workspace/request_id/job_id/plan_id/node_id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from etalon.runtime.api import read_action_result


def read_action_results(handles: list[dict], output_directory: str | Path) -> list[dict]:
    """Read independent items, keeping read errors distinct from execution failures.

    Use a fresh output directory each time; these are observation snapshots, not a
    second execution ledger. Repeated handles are safe reads, not retries. A failed
    read has unknown execution/cost state; it does not justify a zero or a restart.
    Do not sum repeated execution references or entries marked nonadditive.
    """
    if not isinstance(handles, list):
        raise ValueError("handles must be a JSON array")
    output = Path(output_directory).absolute()
    output.mkdir(parents=True, exist_ok=False)
    summaries = []
    seen_executions = {}
    for index, handle in enumerate(handles):
        try:
            result = read_action_result(handle)
        except Exception as error:
            payload = {"handle": handle, "read_error": {"type": type(error).__name__, "message": str(error)}}
            summary = {"read_status": "failed", "execution_state": "unknown",
                       "accounting_refs": None, "recovery": None, "error": payload["read_error"],
                       "suggested_attention": "inspect read error; do not infer execution failure or retry"}
        else:
            payload = {"handle": handle, "result": result}
            identity = json.dumps([result["plan_id"], result["execution_refs"][0]], sort_keys=True)
            duplicate = seen_executions.get(identity)
            seen_executions.setdefault(identity, index + 1)
            accounting = []
            for reference in result["accounting_refs"]:
                # Keep amounts, basis, uncertainty and nonadditivity verbatim.
                # Detailed charge receipts remain in the complete item snapshot.
                accounting.append({key: value for key, value in reference.items()
                                   if key != "execution_cost_refs"})
            summary = {"read_status": "ok", "request_id": result["request_id"],
                       "same_execution_as_item": duplicate,
                       "execution_state": result["execution_state"],
                       "native_status": result["native_status"],
                       "verification": result["verification"],
                       "accounting_refs": accounting, "recovery": result["recovery"],
                       "observation_count": len(result["observations"]),
                       "observations": [{"action_id": observation["action_id"],
                                         "admission": observation["admission"],
                                         "status": observation["result"].get("status"),
                                         "value": observation["result"].get("value"),
                                         "units": observation["result"].get("units")}
                                        for observation in result["observations"]],
                       "suggested_attention": "inspect unresolved evidence; no automatic retry"
                       if result["execution_state"] in {"unresolved", "pending", "running"}
                       else "inspect failure evidence or stop"
                       if result["execution_state"] in {"failed", "blocked", "cancelled"}
                       else "read scientific evidence if needed, or stop"}
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        path = output / f"item-{index + 1:04d}.json"
        with path.open("xb") as stream:
            stream.write(encoded)
        summaries.append({"item": index + 1, **summary,
                          "full_result": {"path": str(path), "sha256": hashlib.sha256(encoded).hexdigest(),
                                          "bytes": len(encoded)}})
    return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    handles = json.loads(args.handles.read_text(encoding="utf-8"))
    print(json.dumps(read_action_results(handles, args.output), ensure_ascii=False, indent=2, allow_nan=False))
