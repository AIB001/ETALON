"""A persistent, bounded outer loop for explicitly reviewed protocol experiments.

propose -> compile-only validation -> authorized trial -> evidence review -> promote/retire.
No operation in this module calls a scientific executor. A promotion permits further budgeted
queries; it is NOT a claim of accuracy, superiority, or automatic scientific certification.
All state transitions and endpoint registration share the campaign's SQLite transaction.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from etalon.active.budget import affordable_capacity, cost_quote
from etalon.active.cascade import CascadeRecipe
from etalon.active.schema import Endpoint, canonical, digest, finite
from etalon.active.store import BudgetExhausted, CampaignStore, StateError


class ProtocolUnavailable(BudgetExhausted):
    """A retired protocol or a trial with no remaining dispatch allowance."""


@dataclass(frozen=True)
class TrialPolicy:
    budget: float
    max_actions: int
    min_admitted: int = 3
    min_pairs: int = 3
    max_failure_fraction: float = 0.25

    def __post_init__(self) -> None:
        finite(self.budget, "trial budget", minimum=0)
        if self.budget <= 0:
            raise ValueError("trial budget must be positive")
        for name in ("max_actions", "min_admitted", "min_pairs"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == "min_pairs" else 1):
                raise ValueError(f"{name} has an invalid count")
        if max(self.min_admitted, self.min_pairs) > self.max_actions:
            raise ValueError("trial action allowance cannot meet its declared evidence threshold")
        finite(self.max_failure_fraction, "max_failure_fraction", minimum=0)
        if not 0 <= self.max_failure_fraction <= 1:
            raise ValueError("max_failure_fraction must be in [0, 1]")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read(db: Any, key: str) -> Any:
    row = db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _write(db: Any, key: str, body: Any) -> None:
    db.execute("INSERT INTO metadata(key,body) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
               (key, canonical(body)))


def _idle(db: Any) -> None:
    if (db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone()
            or db.execute("SELECT 1 FROM rounds WHERE status='running'").fetchone()):
        raise StateError("protocol transitions require completed rounds and no pending actions")


def _configuration(db: Any) -> dict[str, Any]:
    value = _read(db, "configuration")
    if value is None:
        raise StateError("configure the campaign first")
    return value


def _recipe(body: Mapping[str, Any]) -> CascadeRecipe:
    return CascadeRecipe(**{**body, "input_files": tuple(tuple(item) for item in body.get("input_files", ()))})


def _counts(db: Any, endpoint_id: str) -> dict[str, float | int]:
    row = db.execute("SELECT COUNT(*),COALESCE(SUM(cost),0),COALESCE(SUM(CASE WHEN status IN "
                     "('reserved','running') THEN reservation ELSE 0 END),0) FROM actions WHERE endpoint_id=?",
                     (endpoint_id,)).fetchone()
    if not all(math.isfinite(value) for value in (row[1], row[2])):
        raise StateError("protocol accounting exceeds finite numeric range; preserve the recorded charges "
                         "and rescale cost units in a new journal")
    return {"actions": row[0], "spent": row[1], "reserved": row[2]}


def _capacity(db: Any, endpoint_id: str, quote: float) -> int | None:
    control = _read(db, "protocol:endpoint:" + endpoint_id)
    if control is None:
        return None  # Legacy explicitly registered endpoints remain supported.
    record = _read(db, "protocol:proposal:" + control["proposal_id"])
    if record is None:
        raise StateError("protocol control has lost its proposal record")
    if record["status"] == "promoted":
        return None
    if record["status"] != "trial":
        return 0
    counts, limits = _counts(db, endpoint_id), record["trial"]["limits"]
    available = limits["budget"] - counts["spent"] - counts["reserved"]
    if not math.isfinite(available):
        raise StateError("protocol remaining budget exceeds finite numeric range; rescale cost units in a new journal")
    cap = affordable_capacity(quote, available, limit=max(0, limits["max_actions"] - counts["actions"]))
    if "search_id" in control:
        from etalon.active.proposer import search_capacity

        search_cap = search_capacity(db, endpoint_id, quote)
        if search_cap is not None:
            cap = min(cap, search_cap)
    return cap


def enforce_protocol_quota(db: Any, endpoint_id: str, quote: float, *, candidate_id: str | None = None) -> None:
    """Called inside the SAME transaction as CampaignStore.reserve()."""
    if _capacity(db, endpoint_id, quote) == 0:
        raise ProtocolUnavailable("protocol is inactive or its trial budget/action allowance is exhausted")
    control = _read(db, "protocol:endpoint:" + endpoint_id)
    if control and "search_id" in control:
        from etalon.active.proposer import search_capacity

        search_capacity(db, endpoint_id, quote, candidate_id=candidate_id)


def protocol_evidence_issue(db: Any, endpoint_id: str, result: Mapping[str, Any], *,
                            executed: bool, action_id: str | None = None) -> str | None:
    """Fail closed on mismatched evidence for controlled protocols, not legacy endpoints.

    This checks executor provenance inside the journal transaction, not a cryptographic
    attestation of arbitrary caller code. The trusted CascadeExecutor verifies the actual
    plan, input hashes and readout bytes. Raw/imported results always remain in the journal.
    """
    if _read(db, "protocol:endpoint:" + endpoint_id) is None:
        return None
    if not executed:
        return "controlled protocols require action-scoped execution evidence; historical import is not a trial"
    bound = _read(db, "protocol:recipe:" + endpoint_id)
    if bound is None:
        return "controlled protocol has no bound recipe certificate"
    provenance = result.get("provenance")
    if not isinstance(provenance, Mapping):
        return "missing protocol execution provenance"
    if (not action_id or provenance.get("action_id") != action_id
            or provenance.get("candidate_id") != result.get("candidate_id")):
        return "execution provenance does not identify this action and molecule"
    protocol = bound["endpoint"]["protocol"]
    if provenance.get("mode") != "live_molcascade" or provenance.get("protocol_id") != protocol:
        return "executor mode or protocol identity does not match the controlled endpoint"
    graph = provenance.get("evidence_graph")
    if (not isinstance(graph, Mapping) or graph.get("protocol_id") != protocol
            or graph.get("graph_hash") != bound["graph"]["graph_hash"]):
        return "execution evidence graph does not match the bound certificate"
    readout = graph.get("readout")
    artifact = provenance.get("artifact_id")
    if (not isinstance(readout, Mapping) or readout.get("available") is not True
            or not isinstance(artifact, str) or not artifact or readout.get("artifact_id") != artifact):
        return "readout is unavailable or its artifact does not match the scalar evidence"
    return None


def protocol_limits(store: CampaignStore, endpoints: Mapping[str, Endpoint],
                    observations: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Additional-query caps for planning; reserve rechecks current state atomically."""
    with store.connection() as db:
        db.execute("BEGIN")
        return {key: cap for key, endpoint in endpoints.items()
                if (cap := _capacity(db, key, cost_quote(endpoint, observations))) is not None}


def _failure(db: Any, action_id: str, base_id: str) -> dict[str, Any]:
    row = db.execute("SELECT a.endpoint_id,a.status,o.admitted,o.body,o.ruling,a.round_id FROM actions a "
                     "JOIN observations o ON o.action_id=a.id WHERE a.id=?", (action_id,)).fetchone()
    if row is None or row[0] != base_id or row[1] in {"reserved", "running"} or row[2] or row[5] <= 0:
        raise StateError("repair source must be a resolved, non-admitted runtime result of the base endpoint")
    result = json.loads(row[3])
    codes = {c["code"] for c in result.get("checks", []) if c["fired"] and c.get("evaluable", True)}
    code = result.get("provenance", {}).get("failure_code")
    if isinstance(code, str) and code:
        codes.add(code)
    if not codes:
        raise ValueError("repair needs a recorded structured failure code, not guessed error prose")
    return {"action_id": action_id, "result_hash": digest({"result": result, "ruling": json.loads(row[4])}),
            "failure_codes": sorted(codes)}


class ProtocolRegistry:
    def __init__(self, store: CampaignStore) -> None:
        self.store = store

    def bind_seed(self, endpoint_id: str, recipe: CascadeRecipe, *, rationale: str) -> None:
        """Persist an already-registered recipe so the dispatch table can be reconstructed."""
        from etalon.active.graph import inspect_recipe

        if not rationale.strip():
            raise ValueError("recipe binding requires a rationale")
        graph = inspect_recipe(recipe)
        with self.store.connection(write=True) as db:
            _idle(db)
            known = {e["id"]: e for e in _configuration(db)["endpoints"]}
            endpoint = known.get(endpoint_id)
            if (endpoint is None or endpoint["protocol"] != recipe.protocol_id
                    or endpoint["requires_handoff"] or endpoint["max_replicates"] != 1):
                raise ValueError("seed recipe must match a registered, single-run cascade endpoint")
            if _read(db, "protocol:endpoint:" + endpoint_id) is not None:
                raise StateError("a controlled trial cannot be rebound as an unrestricted seed")
            key = "protocol:recipe:" + endpoint_id
            body = {"endpoint": endpoint, "recipe": asdict(recipe), "graph": graph, "origin": "seed"}
            previous = _read(db, key)
            if previous is not None:
                if canonical(previous) != canonical(body):
                    raise StateError("bound recipe or compiler identity changed")
                return
            _write(db, key, body)
            self.store._event(db, "protocol_seed_bound", {"endpoint_id": endpoint_id, "protocol_id": recipe.protocol_id,
                                                         "graph_hash": graph["graph_hash"], "rationale": rationale})

    def bind_space(self, space: Any, *, rationale: str) -> str:
        """Explicitly authorize a finite exact edit vocabulary; never inferred from failures."""
        from etalon.active.mutations import DesignSpace

        if not isinstance(space, DesignSpace) or not rationale.strip():
            raise ValueError("a DesignSpace and an explicit review rationale are required")
        key = "protocol:space:" + space.fingerprint
        with self.store.connection(write=True) as db:
            _idle(db)
            _configuration(db)
            body = space.as_dict()
            previous = _read(db, key)
            if previous is not None and canonical(previous) != canonical(body):
                raise StateError("design space identity collision")
            if previous is None:
                _write(db, key, body)
                self.store._event(db, "protocol_space_bound", {"space_id": space.fingerprint,
                                                             "space": body, "rationale": rationale})
        return space.fingerprint

    def propose(self, base_endpoint_id: str, endpoint_id: str, *, space_id: str,
                edits: Sequence[dict[str, Any]], cost: float, rationale: str,
                proposed_by: str, source_action_id: str | None = None) -> str:
        from etalon.active.mutations import DesignSpace, mutate_recipe

        if not rationale.strip() or not proposed_by.strip() or endpoint_id == base_endpoint_id:
            raise ValueError("proposal needs a new endpoint id, reason and named proposer")
        with self.store.connection() as db:
            bound = _read(db, "protocol:recipe:" + base_endpoint_id)
            space_body = _read(db, "protocol:space:" + space_id)
        if bound is None or space_body is None:
            raise StateError("bind the base recipe and reviewed design space before proposing")
        base = Endpoint(**bound["endpoint"])
        variant = mutate_recipe(_recipe(bound["recipe"]), edits, DesignSpace.from_dict(space_body))
        endpoint = replace(base, id=endpoint_id, protocol=variant.protocol_id, cost=cost)
        with self.store.connection(write=True) as db:
            _idle(db)
            config = _configuration(db)
            known = {e["id"]: e for e in config["endpoints"]}
            if known.get(base_endpoint_id) != base.as_dict():
                raise StateError("base endpoint changed")
            source = _failure(db, source_action_id, base_endpoint_id) if source_action_id else None
            if source:
                for edit in edits:
                    if not set(edit.get("allowed_failures", ())) & set(source["failure_codes"]):
                        raise ValueError("each repair edit must explicitly allow an observed failure code")
            body = {"schema_version": 1, "base_endpoint": base.as_dict(), "endpoint": endpoint.as_dict(),
                    "recipe": asdict(variant), "edits": list(edits), "space_id": space_id,
                    "rationale": rationale, "proposed_by": proposed_by, "source_failure": source,
                    "objective": config["spec"]["objective"]}
            identifier = "proposal/1:" + digest(body)
            key = "protocol:proposal:" + identifier
            previous = _read(db, key)
            if previous is not None:
                return identifier
            if endpoint_id in known:
                raise StateError("proposal endpoint id is already registered")
            for existing in db.execute("SELECT body FROM metadata WHERE key LIKE 'protocol:proposal:%'"):
                if json.loads(existing[0])["body"]["endpoint"]["id"] == endpoint_id:
                    raise StateError("endpoint id is already owned by another proposal; use a new endpoint id")
            # Identical recipe aliases are not a way to buy extra independent replicates.
            if any(e["protocol"] == variant.protocol_id for e in known.values()):
                raise StateError("this protocol is already registered; a new alias is not a new experiment")
            from etalon.active.proposer import bind_search_proposal

            bind_search_proposal(db, body, identifier)
            _write(db, key, {"id": identifier, "body": body, "status": "proposed",
                             "validation": None, "trial": None, "review": None})
            self.store._event(db, "protocol_proposed", {"proposal_id": identifier, "body": body})
        return identifier

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            record = _read(db, "protocol:proposal:" + proposal_id)
        if record is None:
            raise KeyError(f"unknown protocol proposal {proposal_id}")
        return record

    def proposals(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM metadata WHERE key LIKE 'protocol:proposal:%' ORDER BY key")]

    def validate(self, proposal_id: str) -> dict[str, Any]:
        """Persist a compile-only certificate or diagnostic; no endpoint becomes queryable."""
        from etalon.active.graph import inspect_recipe

        record = self.get(proposal_id)
        if record["status"] not in {"proposed", "invalid", "validated"}:
            raise StateError("only unregistered proposals can be validated")
        try:
            graph = inspect_recipe(_recipe(record["body"]["recipe"]))
            validation = {"ok": True, "scope": "compile_only", "proposal_hash": digest(record["body"]), "graph": graph}
        except (ValueError, OSError, ImportError) as error:
            validation = {"ok": False, "scope": "compile_only", "proposal_hash": digest(record["body"]),
                          "error": f"{type(error).__name__}: {error}"}
        with self.store.connection(write=True) as db:
            _idle(db)
            current = _read(db, "protocol:proposal:" + proposal_id)
            if current != record:
                raise StateError("proposal state changed during validation; reload before retrying")
            current["validation"], current["status"] = validation, "validated" if validation["ok"] else "invalid"
            _write(db, "protocol:proposal:" + proposal_id, current)
            self.store._event(db, "protocol_validated", {"proposal_id": proposal_id, **validation})
        return validation

    def _recheck(self, record: dict[str, Any]) -> dict[str, Any]:
        from etalon.active.graph import inspect_recipe

        validation = record["validation"]
        if not validation or not validation["ok"] or validation["proposal_hash"] != digest(record["body"]):
            raise StateError("proposal has no matching successful compile certificate")
        graph = inspect_recipe(_recipe(record["body"]["recipe"]))
        if canonical(graph) != canonical(validation["graph"]):
            raise StateError("compiled protocol changed since validation")
        return graph

    def start_trial(self, proposal_id: str, limits: TrialPolicy, *, rationale: str) -> None:
        """Register and bind the new endpoint atomically, with predeclared rollout thresholds."""
        if not isinstance(limits, TrialPolicy) or not rationale.strip():
            raise ValueError("an explicit TrialPolicy and authorization rationale are required")
        record = self.get(proposal_id)
        if record["status"] in {"trial", "promoted"}:
            if record["trial"]["limits"] != limits.as_dict():
                raise StateError("trial policy cannot change after seeing results")
            return
        if record["status"] != "validated":
            raise StateError("trial requires a validated proposal")
        graph = self._recheck(record)
        with self.store.connection(write=True) as db:
            _idle(db)
            current = _read(db, "protocol:proposal:" + proposal_id)
            if current["status"] in {"trial", "promoted"} and current["trial"]["limits"] == limits.as_dict():
                return
            if current != record:
                raise StateError("proposal state changed before trial registration")
            config, body = _configuration(db), record["body"]
            known = {e["id"]: e for e in config["endpoints"]}
            if known.get(body["base_endpoint"]["id"]) != body["base_endpoint"] or config["spec"]["objective"] != body["objective"]:
                raise StateError("proposal's base endpoint/objective no longer matches")
            if body["source_failure"] != (_failure(db, body["source_failure"]["action_id"], body["base_endpoint"]["id"])
                                          if body["source_failure"] else None):
                raise StateError("repair source evidence changed")
            endpoint = body["endpoint"]
            if endpoint["id"] in known or any(e["protocol"] == endpoint["protocol"] for e in known.values()):
                raise StateError("new endpoint/protocol is already registered")
            if limits.budget < endpoint["cost"] or limits.budget > config["spec"]["budget"]:
                raise ValueError("trial budget must cover a quote and fit the campaign's total budget cap")
            from etalon.active.proposer import authorize_search_trial

            search_control = authorize_search_trial(db, record, limits)
            known[endpoint["id"]] = endpoint
            config["endpoints"] = [known[key] for key in sorted(known)]
            _write(db, "configuration", config)
            _write(db, "protocol:recipe:" + endpoint["id"],
                   {"endpoint": endpoint, "recipe": body["recipe"], "graph": graph, "origin": proposal_id})
            _write(db, "protocol:endpoint:" + endpoint["id"], {"proposal_id": proposal_id, **search_control})
            current["status"], current["trial"] = "trial", {"limits": limits.as_dict(), "rationale": rationale}
            _write(db, "protocol:proposal:" + proposal_id, current)
            self.store._event(db, "endpoint_registered", {"endpoint": endpoint, "rationale": rationale,
                                                         "proposal_id": proposal_id})
            self.store._event(db, "protocol_trial_started", {"proposal_id": proposal_id, "limits": limits.as_dict(),
                                                            "graph_hash": graph["graph_hash"], "rationale": rationale})

    def _report(self, db: Any, record: dict[str, Any]) -> dict[str, Any]:
        endpoint_id = record["body"]["endpoint"]["id"]
        objective = record["body"]["objective"]
        registered = {endpoint["id"] for endpoint in _configuration(db)["endpoints"]}
        if endpoint_id in registered:
            control = _read(db, "protocol:endpoint:" + endpoint_id)
            if control is None or control.get("proposal_id") != record["id"]:
                raise StateError("endpoint slot belongs to a different registration; its evidence cannot "
                                 "be attributed to this proposal")
        rows = [{"action_id": r[0], "admitted": bool(r[1]), "result": json.loads(r[2]), "round_id": r[3]}
                for r in db.execute("SELECT o.action_id,o.admitted,o.body,a.round_id FROM observations o "
                                    "JOIN actions a ON a.id=o.action_id WHERE a.endpoint_id=? ORDER BY a.rowid", (endpoint_id,))]
        objective_ids = {json.loads(r[0])["candidate_id"] for r in db.execute(
            "SELECT o.body FROM observations o JOIN actions a ON a.id=o.action_id "
            "WHERE a.endpoint_id=? AND o.admitted=1", (objective,))}
        runtime = [r for r in rows if r["round_id"] > 0]
        for row in runtime:
            row["protocol_evidence_issue"] = protocol_evidence_issue(
                db, endpoint_id, row["result"], executed=True, action_id=row["action_id"])
        accepted = [r for r in runtime if r["admitted"] and r["protocol_evidence_issue"] is None]
        good_ids = {r["result"]["candidate_id"] for r in accepted}
        # All actual attempts remain in the denominator, including missing/failed artifacts.
        # Imports consume quota and money but cannot manufacture a successful runtime trial.
        failed = len(runtime) - len(accepted)
        failure_fraction = failed / len(runtime) if runtime else None
        criteria = record["trial"]["limits"] if record["trial"] else None
        reasons = []
        if criteria is None:
            reasons.append("no_authorized_trial")
        else:
            if len(good_ids) < criteria["min_admitted"]:
                reasons.append("insufficient_admitted_molecules")
            if len(good_ids & objective_ids) < criteria["min_pairs"]:
                reasons.append("insufficient_objective_pairs")
            if failure_fraction is None or failure_fraction > criteria["max_failure_fraction"]:
                reasons.append("failure_fraction_above_limit_or_no_results")
        pending = db.execute("SELECT COUNT(*) FROM actions WHERE endpoint_id=? AND status IN ('reserved','running')",
                             (endpoint_id,)).fetchone()[0]
        if pending:
            reasons.append("pending_actions")
        return {"proposal_id": record["id"], "status": record["status"], "endpoint_id": endpoint_id,
                **_counts(db, endpoint_id), "admitted_molecules": len(good_ids),
                "paired_molecules": len(good_ids & objective_ids), "failed_results": failed,
                "runtime_results": len(runtime), "imported_results": len(rows) - len(runtime),
                "unverified_runtime_results": sum(r["protocol_evidence_issue"] is not None for r in runtime),
                "failure_fraction": failure_fraction, "evidence_action_ids": [r["action_id"] for r in runtime],
                "evidence_hash": digest(rows), "limits": criteria,
                "rollout_criteria_met": not reasons, "unmet_criteria": reasons,
                "scope": "Operational rollout criteria only; not an independent comparison, "
                         "scientific accuracy validation, or proof that the repair caused recovery."}

    def report(self, proposal_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            db.execute("BEGIN")
            record = _read(db, "protocol:proposal:" + proposal_id)
            if record is None:
                raise KeyError(proposal_id)
            return self._report(db, record)

    def promote(self, proposal_id: str, *, rationale: str) -> None:
        if not rationale.strip():
            raise ValueError("promotion requires an explicit review rationale")
        record = self.get(proposal_id)
        if record["status"] == "promoted":
            return
        if record["status"] != "trial":
            raise StateError("only an authorized trial can be promoted")
        self._recheck(record)
        with self.store.connection(write=True) as db:
            _idle(db)
            current = _read(db, "protocol:proposal:" + proposal_id)
            if current["status"] == "promoted":
                return
            if current != record:
                raise StateError("protocol changed before promotion")
            from etalon.active.proposer import check_search_promotion

            check_search_promotion(db, proposal_id)
            report = self._report(db, current)
            if not report["rollout_criteria_met"]:
                raise StateError("trial does not meet its predeclared rollout criteria: " + ", ".join(report["unmet_criteria"]))
            current["status"], current["review"] = "promoted", {"rationale": rationale, "report": report}
            _write(db, "protocol:proposal:" + proposal_id, current)
            self.store._event(db, "protocol_promoted", {"proposal_id": proposal_id, **current["review"]})

    def retire(self, proposal_id: str, *, rationale: str) -> None:
        if not rationale.strip():
            raise ValueError("retirement requires a rationale")
        with self.store.connection(write=True) as db:
            _idle(db)
            record = _read(db, "protocol:proposal:" + proposal_id)
            if record is None:
                raise KeyError(proposal_id)
            if record["status"] == "retired":
                return
            report = self._report(db, record)
            record["status"], record["review"] = "retired", {"rationale": rationale, "report": report}
            _write(db, "protocol:proposal:" + proposal_id, record)
            self.store._event(db, "protocol_retired", {"proposal_id": proposal_id, **record["review"]})

    def recipes(self) -> dict[str, CascadeRecipe]:
        """Read persisted recipes; constructing an executor still requires an explicit caller."""
        with self.store.connection() as db:
            rows = db.execute("SELECT body FROM metadata WHERE key LIKE 'protocol:recipe:%' ORDER BY key").fetchall()
        records = [json.loads(row[0]) for row in rows]
        return {r["endpoint"]["id"]: _recipe(r["recipe"]) for r in records}
