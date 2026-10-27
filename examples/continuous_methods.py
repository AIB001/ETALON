"""Real CPU method handoff through MCP; descriptors are not binding efficacy.

Run with --workspace on Linux. The example host inspects a small completed batch,
then sends optional advice while the next batch keeps running. The memoized
variant avoids reparsing identical SMILES, without changing the descriptor.
No model is called. The same proposal API is available to a native agent.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def compute(request, result):
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    task = json.loads(Path(request).read_text())
    inputs = task["inputs"]
    started = time.perf_counter()
    if "reference" in inputs:
        baseline, candidate = inputs["reference"], inputs["candidate"]
        same = baseline["values_sha256"] == candidate["values_sha256"] and baseline["count"] == candidate["count"]
        outputs = {"same_descriptor_values": same, "reference": baseline, "candidate": candidate}
        feedback = {"decision": "reuse_within_exact_smiles_scope" if same else "retain_reference",
                    "scope": "RDKit molecular weight for these inputs; no binding or synthesis claim"}
    else:
        smiles = ["CCO", "CC(=O)Oc1ccccc1C(=O)O", "c1ccccc1", "CCN", "CC(=O)O", "CC(C)O"] * inputs["repeats"]
        values, cache, parsed = [], {}, 0
        for value in smiles:
            if inputs["algorithm"] == "memoized" and value in cache:
                weight = cache[value]
            else:
                molecule = Chem.MolFromSmiles(value)
                weight = Descriptors.MolWt(molecule) if molecule is not None else None
                cache[value] = weight
                parsed += 1
            values.append(weight)
        payload = json.dumps(values, separators=(",", ":")).encode()
        Path(task["outputs"]["values"]).write_bytes(payload)
        outputs = {"values_sha256": hashlib.sha256(payload).hexdigest(), "count": len(values),
                   "unique_inputs": len(cache), "parse_count": parsed,
                   "compute_seconds": time.perf_counter()-started}
        feedback = {"identical_input_repetition": len(values) > len(cache),
                    "candidate": "memoize_exact_smiles", "scope": "same RDKit descriptor and exact input strings"}
    Path(result).write_text(json.dumps({"schema": "etalon-method-result/1",
        "summary": "CPU descriptor method result; not a molecular efficacy result",
        "outputs": outputs, "feedback": feedback}), encoding="utf-8")


async def run(workspace):
    from datetime import timedelta
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    workspace = Path(workspace).absolute()
    workspace.mkdir(parents=True, exist_ok=True)
    script = str(Path(__file__).absolute())
    def node(name, inputs, dependencies=()):
        return {"id": name, "operation": "method.run", "depends_on": list(dependencies),
                "arguments": {"method_id": "rdkit-molwt/"+inputs.get("algorithm", "comparison"),
                    "command": [sys.executable, script], "files": [script], "inputs": inputs,
                    "outputs": {} if "reference" in inputs else {"values": "weights.json"}},
                "resources": {"wall_second": 120}}
    sample = node("sample", dict(algorithm="literal", repeats=20))
    bulk = node("bulk", dict(algorithm="literal", repeats=10000), ["sample"])
    next_batch = node("next", dict(algorithm="literal", repeats=10000), ["bulk"])
    spec = {"schema": "etalon-workflow/1", "objective": "Continuous CPU methods with side-channel feedback",
            "nodes": [sample, bulk, next_batch], "limits": {"wall_second": 600}, "max_seconds": 240}
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).absolute().parents[1]/"src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    started = time.perf_counter()
    calls = []
    server = StdioServerParameters(command=sys.executable, args=["-m", "etalon.mcp"], env=env)
    async with stdio_client(server) as (read, write), ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session:
        await session.initialize()
        async def call(name, **arguments):
            before = time.perf_counter()
            result = await session.call_tool("etalon_"+name, arguments)
            data = json.loads(result.content[0].text)
            calls.append({"name": name, "seconds": time.perf_counter()-before})
            if result.isError or not data.get("ok"):
                raise RuntimeError(data)
            return data
        plan = await call("workflow_plan", spec=spec)
        await call("workflow_submit", spec=spec, workspace=str(workspace), job_id="cpu-methods", plan_id=plan["plan_id"])
        while True:
            status = (await call("workflow_status", workspace=str(workspace), job_id="cpu-methods"))["job"]
            states = {n["id"]: n["state"] for n in status["nodes"]}
            if states["sample"] == "verified" and states["bulk"] == "running":
                break
            if time.perf_counter()-started > 90 or status["state"] in {"failed", "blocked", "reconciliation_required", "succeeded"}:
                raise RuntimeError(status)
            await asyncio.sleep(.25)
        feedback = await call("workflow_artifact", workspace=str(workspace), job_id="cpu-methods", node_id="sample", member="/feedback")
        # This small example host owns the proposal. Runtime never contains this policy.
        if not feedback["data"]["identical_input_repetition"]:
            raise RuntimeError("example produced no applicable repetition feedback")
        revised = node("next", dict(algorithm="memoized", repeats=10000), ["bulk"])
        comparison = node("compare", {"reference": {"$ref": "bulk.outputs"}, "candidate": {"$ref": "next.outputs"}})
        proposal = dict(kind="revise", revision_id="exact-smiles-reuse", expected_plan_id=status["plan"]["revision_id"],
                        nodes=[revised, comparison], reason="Observed duplicate inputs; reuse the exact descriptor within this task")
        await call("workflow_advance", workspace=str(workspace), job_id="cpu-methods", proposal=proposal)
        while True:
            status = (await call("workflow_status", workspace=str(workspace), job_id="cpu-methods"))["job"]
            if status["state"] == "succeeded":
                break
            if time.perf_counter()-started > 180 or status["state"] in {"failed", "blocked", "reconciliation_required"}:
                raise RuntimeError(status)
            await asyncio.sleep(.5)
        result = await call("workflow_artifact", workspace=str(workspace), job_id="cpu-methods", node_id="compare", member="/outputs")
        await call("workflow_submit", spec=spec, workspace=str(workspace), job_id="cpu-methods", plan_id=plan["plan_id"])
        await call("workflow_advance", workspace=str(workspace), job_id="cpu-methods", proposal=proposal)
        final = (await call("workflow_status", workspace=str(workspace), job_id="cpu-methods"))["job"]
        assert final["sequence"] == status["sequence"] and final["epoch"] == status["epoch"]
        report = {"result": result["data"], "workflow": final,
                  "total_wall_seconds": time.perf_counter()-started,
                  "mcp_calls": calls, "model_calls": 0, "model_tokens": 0,
                  "scope": "actual RDKit CPU/MCP integration; scripted host policy, not autonomous LLM or affinity validation"}
        (workspace/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("result", "total_wall_seconds", "model_calls", "scope")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace")
    parser.add_argument("--request")
    parser.add_argument("--result")
    args = parser.parse_args()
    if args.request:
        compute(args.request, args.result)
    else:
        asyncio.run(run(args.workspace))
