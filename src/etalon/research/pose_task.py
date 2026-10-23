"""Explicit pose-task I/O over the existing scientific-input and Artifact APIs.

This is a domain contract, not a scheduler. Evaluation files are deliberately
absent from the execution request. A driver records the returned objects in its
existing ResearchJournal and links actual receipts through research-step.
"""
from __future__ import annotations

from pathlib import Path

from etalon.active.schema import digest
from .scientific_input import compile_input, consumption_receipt, identity

SCHEMA = "etalon-pose-task/1"
TARGETS = {"receptor": "constraint", "ligand": "constraint", "box": "constraint",
           "seed": "selection", "num_modes": "selection", "exhaustiveness": "selection"}


def compile_task(task, *, verify_files=True):
    """Return an execution-only request with explicit parameter consumption."""
    if task.get("schema") != SCHEMA:
        raise ValueError("unsupported pose task")
    for key in ("task_id", "question", "case_id", "split", "input", "output_contract", "method"):
        if not task.get(key):
            raise ValueError("missing pose task field: " + key)
    if task["split"] not in {"development", "validation", "regression"}:
        raise ValueError("explicit task split required")
    if task["input"]["task_id"] != task["task_id"]:
        raise ValueError("task identity differs from scientific input")
    if task["output_contract"] != {"poses": "SDF", "scores": "typed-with-direction",
                                   "receipt": "request-and-file-identities", "failure": "retained"}:
        raise ValueError("unsupported output contract")
    compiled = compile_input(task["input"], allowed_targets=TARGETS, verify_files=verify_files)
    p = compiled["parameters"]
    if set(p) != set(TARGETS):
        raise ValueError("pose task must bind every execution parameter")
    for name in ("receptor", "ligand"):
        if not Path(p[name]).is_absolute():
            raise ValueError("absolute input paths required")
        sources = [s for s in task["input"]["sources"] if s["path"] == p[name] and s["role"] == "conditioning"]
        if len(sources) != 1:
            raise ValueError("execution file must have one conditioning source")
        binding = next(b for b in task["input"]["bindings"] if b["target"] == name)
        if sources[0]["id"] not in binding["basis_ids"]:
            raise ValueError("execution file must be directly bound to its source")
    box = p["box"]
    import math
    if set(box) != {"center", "size"} or any(len(box[k]) != 3 for k in box):
        raise ValueError("three-dimensional center and size required")
    if not all(type(v) in (int, float) and math.isfinite(v) for k in box for v in box[k]):
        raise ValueError("finite box required")
    if not all(0 < v <= 60 for v in box["size"]):
        raise ValueError("invalid box extent")
    for key, upper in (("seed", 2147483647), ("num_modes", 100), ("exhaustiveness", 512)):
        if type(p[key]) is not int or not 1 <= p[key] <= upper:
            raise ValueError("invalid " + key)
    return {"schema": "etalon-pose-request/1", "task_id": task["task_id"], "case_id": task["case_id"],
            "task_digest": digest(task), "method": task["method"], "parameters": p,
            "files": {k: identity(p[k]) for k in ("receptor", "ligand")} if verify_files else {},
            "consumption": consumption_receipt(compiled, consumed_targets=TARGETS)}


def select_pose_branch(applicable, *, default="empirical", candidate="cnn"):
    """An optional deterministic policy; only applicable adopted lessons can select.

    Related lessons remain visible hypotheses. Conflicting applicable outcomes
    retain the baseline. Callers persist this result before candidate execution.
    """
    matches = applicable.get("matches", [])
    # Supersession is identity-bound, not timestamp or outcome precedence.
    # Incomplete retrieval cannot establish the absence of counterevidence.
    incomplete = bool(applicable.get('next_cursor') or applicable.get('warnings')) or any(
        c.get('status') != 'complete' for c in applicable.get('coverage', []))
    by_ref = {digest(item['ref']): item for item in matches}
    edges = {}
    invalid = len(by_ref) != len(matches)
    for key,item in by_ref.items():
        current = item['experience']; edges[key] = []
        for ref in current.get('supersedes', []):
            prior_key = digest(ref); prior = by_ref.get(prior_key)
            if prior is None or any(k not in current or current[k] != prior['experience'].get(k)
                for k in ('scope','conditions','capability_id')):
                invalid = True
            else:
                edges[key].append(prior_key)
    def cycle(key, visiting, done):
        if key in visiting: return True
        if key in done: return False
        visiting.add(key)
        if any(cycle(child,visiting,done) for child in edges[key]): return True
        visiting.remove(key); done.add(key)
        return False
    done = set()
    invalid = invalid or any(cycle(key,set(),done) for key in edges)
    retired = {k for values in edges.values() for k in values} if not invalid else set()
    active = [item for key,item in by_ref.items() if key not in retired]
    outcomes = {item["experience"]["outcome"] for item in active}
    selected = candidate if outcomes == {"adopt"} and not (invalid or incomplete) else default
    return {"schema": "etalon-pose-branch-selection/1", "selected": selected,
            "basis_refs": [item["ref"] for item in active],
            "superseded_refs": [by_ref[k]['ref'] for k in sorted(retired)],
            "retrieval_incomplete": incomplete, "invalid_supersession": invalid,
            "related_refs": [item["ref"] for item in applicable.get("related", [])],
            "reason": "applicable adopted experience" if selected == candidate else
                      "no unopposed applicable adoption; retain baseline",
            "unknown_alternative_outcome": True, "automatic_execution": False}
