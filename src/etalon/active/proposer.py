"""Learn which *preauthorized* protocol edits deserve a bounded audit trial.

This is a finite, within-campaign linear-bandit baseline, not arbitrary graph search.
All variants use the same acquired objective panel. Its cross-validation score is
selection/training evidence, NOT an independent performance test. Proposing does not
validate, start a trial, promote, or execute a component. Those remain explicit calls.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from time import perf_counter
from typing import Any

from etalon.active.budget import affordable_capacity, fits_budget
from etalon.active.mutations import DesignSpace, mutate_recipe
from etalon.active.protocols import (
    ProtocolRegistry,
    TrialPolicy,
    _configuration,
    _counts,
    _idle,
    _read,
    _recipe,
    _write,
    protocol_evidence_issue,
)
from etalon.active.schema import Evaluation, canonical, digest, finite
from etalon.active.store import BudgetExhausted, CampaignStore, StateError

VERSION = "bounded-protocol-search/1"


def _search(db: Any, identifier: str) -> dict[str, Any]:
    record = _read(db, "protocol:search:" + identifier)
    if record is None:
        raise KeyError(f"unknown protocol search {identifier}")
    return record


def _save(db: Any, record: dict[str, Any]) -> None:
    _write(db, "protocol:search:" + record["id"], record)


def _economic_contract(version: str, opportunity_cost: float, cost_unit: str) -> dict[str, Any]:
    """An explicit score/cost exchange rate, never an inferred CADD value model."""
    from etalon.active.protocol_score import AUDIT_ECONOMICS_VERSION

    return {"version": version, "prediction_version": AUDIT_ECONOMICS_VERSION,
            "opportunity_cost": opportunity_cost, "cost_unit": cost_unit,
            "utility": "best_completed_panel_skill_with_zero_outside_option",
            "predictive_distribution": "clipped_gaussian_latent_plus_noise",
            "stopping_rule": "max_affordable_expected_improvement_minus_opportunity_charge_le_zero"}


def _variant(record: dict[str, Any], identifier: str) -> dict[str, Any]:
    return next(v for v in record["body"]["variants"] if v["id"] == identifier)


def _accounting(db: Any, record: dict[str, Any]) -> dict[str, float]:
    spent, reserved = 0.0, 0.0
    allocated = sum(e["allocated_budget"] for e in record["experiments"])
    for experiment in record["experiments"]:
        if experiment["reward"] is not None:
            # Explicit promotion may later buy exploitation queries. They belong to
            # the campaign budget, not the already closed audit experiment.
            spent += experiment["reward"]["accounting_cost"]
        else:
            counts = _counts(db, experiment["endpoint_id"])
            spent += counts["spent"]
            reserved += counts["reserved"]
    return {"budget": record["body"]["budget"], "spent": spent, "reserved": reserved,
            "remaining": record["body"]["budget"] - spent - reserved,
            "allocated": allocated, "allocation_remaining": record["body"]["budget"] - allocated}


def _reference_panel(db: Any, objective: str, identifiers: Sequence[str]) -> list[dict[str, Any]]:
    rows = db.execute("SELECT a.id,a.round_id,o.admitted,o.body,o.ruling FROM actions a "
                      "JOIN observations o ON o.action_id=a.id WHERE a.endpoint_id=? ORDER BY a.rowid", (objective,))
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in identifiers}
    for row in rows:
        result = json.loads(row[3])
        key = result["candidate_id"]
        if key in grouped and row[1] > 0 and row[2] and result["status"] == "ok":
            grouped[key].append({"candidate_id": key, "action_id": row[0], "value": result["value"],
                                 "cost": result["cost"], "evidence_hash": digest({
                                     "result": result, "ruling": json.loads(row[4])})})
    if any(len(grouped[key]) != 1 for key in identifiers):
        raise ValueError("audit panel needs exactly one admitted runtime objective result per molecule; no imported labels")
    panel = [grouped[key][0] for key in identifiers]
    values = [row["value"] for row in panel]
    if len(set(values)) < 2:
        raise ValueError("audit objective panel must be nonconstant")
    return panel


def _check_references(db: Any, record: dict[str, Any]) -> None:
    body = record["body"]
    current = _reference_panel(db, body["objective"], [r["candidate_id"] for r in body["panel"]])
    if current != body["panel"]:
        raise StateError("frozen audit objective evidence changed")


def bind_search_proposal(db: Any, body: dict[str, Any], proposal_id: str) -> None:
    """Registry hook: bind a durable selection intent and proposal in ONE transaction."""
    link = _read(db, "protocol:search-endpoint:" + body["endpoint"]["id"])
    if link is None:
        reject_search_registration(db, body["endpoint"])
        return
    record = _search(db, link["search_id"])
    experiment = next(e for e in record["experiments"] if e["endpoint_id"] == body["endpoint"]["id"])
    variant = _variant(record, experiment["variant_id"])
    if (record["status"] != "open" or experiment["reward"] is not None
            or body["base_endpoint"] != record["body"]["base_endpoint"]
            or body["objective"] != record["body"]["objective"]
            or body["space_id"] != record["body"]["space_id"]
            or body["edits"] != variant["edits"] or canonical(body["recipe"]) != canonical(variant["recipe"])
            or body["endpoint"]["protocol"] != variant["protocol_id"]
            or body["endpoint"]["cost"] != variant["quote"]
            or body["rationale"] != experiment["rationale"] or body["proposed_by"] != VERSION
            or body["source_failure"] is not None):
        raise StateError("proposal does not match its frozen search authorization")
    if experiment["proposal_id"] not in {None, proposal_id}:
        raise StateError("search intent already has a different proposal")
    experiment["proposal_id"] = proposal_id
    _save(db, record)
    _write(db, "protocol:search-proposal:" + proposal_id, link)


def reject_search_registration(db: Any, endpoint: Mapping[str, Any]) -> None:
    """Selected intents cannot be registered through the unrestricted legacy path."""
    for row in db.execute("SELECT body FROM metadata WHERE key LIKE 'protocol:search:%'"):
        record = json.loads(row[0])
        for experiment in record["experiments"]:
            if (endpoint["id"] == experiment["endpoint_id"]
                    or endpoint["protocol"] == _variant(record, experiment["variant_id"])["protocol_id"]):
                raise StateError("a selected search protocol must use its controlled trial, not legacy registration or an alias")


def authorize_search_trial(db: Any, proposal: dict[str, Any], limits: TrialPolicy) -> dict[str, Any]:
    """Registry hook. A search proposal cannot loosen its predeclared panel trial."""
    link = _read(db, "protocol:search-proposal:" + proposal["id"])
    if link is None:
        reject_search_registration(db, proposal["body"]["endpoint"])
        return {}
    record = _search(db, link["search_id"])
    experiment = next(e for e in record["experiments"] if e["proposal_id"] == proposal["id"])
    variant = _variant(record, experiment["variant_id"])
    if record["status"] != "open" or experiment["reward"] is not None:
        raise StateError("protocol search is closed or its audit is already scored")
    if limits.as_dict() != variant["trial_limits"]:
        raise StateError("search trial limits differ from the preauthorized policy")
    _check_references(db, record)
    if not fits_budget(limits.budget, _accounting(db, record)["remaining"]):
        raise BudgetExhausted("search budget cannot cover its full panel trial")
    return {"search_id": record["id"], "variant_id": variant["id"]}


def search_capacity(db: Any, endpoint_id: str, quote: float, *, candidate_id: str | None = None) -> int | None:
    control = _read(db, "protocol:endpoint:" + endpoint_id)
    if not control or "search_id" not in control:
        return None
    proposal = _read(db, "protocol:proposal:" + control["proposal_id"])
    if proposal["status"] == "promoted":
        return None  # Explicit graduation after a frozen reward permits ordinary campaign use.
    record = _search(db, control["search_id"])
    if record["status"] != "open":
        return 0
    panel = {r["candidate_id"] for r in record["body"]["panel"]}
    if candidate_id is not None and candidate_id not in panel:
        raise StateError("search trial queries must stay within the frozen audit panel")
    return affordable_capacity(quote, _accounting(db, record)["remaining"], limit=len(panel))


def _search_candidate_limits(db: Any) -> dict[str, set[str]]:
    """Read panel restrictions on the same SQLite snapshot as a decision's inputs."""
    records = [(row[0].removeprefix("protocol:endpoint:"), json.loads(row[1]))
               for row in db.execute("SELECT key,body FROM metadata WHERE key LIKE 'protocol:endpoint:%'")]
    result = {}
    for endpoint_id, control in records:
        if "search_id" not in control:
            continue
        proposal = _read(db, "protocol:proposal:" + control["proposal_id"])
        if proposal["status"] != "promoted":
            result[endpoint_id] = {r["candidate_id"] for r in _search(db, control["search_id"])["body"]["panel"]}
    return result


def search_candidate_limits(store: CampaignStore) -> dict[str, set[str]]:
    with store.connection() as db:
        return _search_candidate_limits(db)


def check_search_promotion(db: Any, proposal_id: str) -> None:
    link = _read(db, "protocol:search-proposal:" + proposal_id)
    if link is not None:
        record = _search(db, link["search_id"])
        experiment = next(e for e in record["experiments"] if e["proposal_id"] == proposal_id)
        if experiment["reward"] is None:
            raise StateError("freeze the complete audit reward before promoting a search protocol")


class ProtocolSearch:
    def __init__(self, store: CampaignStore) -> None:
        self.store = store
        self.registry = ProtocolRegistry(store)

    def catalogue(self, base_endpoint_id: str, space_id: str, *, max_variants: int = 32) -> dict[str, Any]:
        """Enumerate canonical-order combinations; never execute or register a variant."""
        from etalon.active.graph import inspect_recipe

        started = perf_counter()
        if type(max_variants) is not int or not 1 <= max_variants <= 256:
            raise ValueError("max_variants must be an integer in [1, 256]")
        with self.store.connection() as db:
            bound = _read(db, "protocol:recipe:" + base_endpoint_id)
            space_body = _read(db, "protocol:space:" + space_id)
        if bound is None or space_body is None:
            raise StateError("bind the base recipe and exact edit space first")
        space = DesignSpace.from_dict(space_body)
        edits = space.allowed_edits
        if len(edits) > max_variants:
            raise ValueError("finite enumeration exceeds max_variants; review a smaller design space")
        count = sum(math.comb(len(edits), size) for size in range(1, min(space.max_edits, len(edits)) + 1))
        if count > max_variants:
            raise ValueError("finite enumeration exceeds max_variants; review a smaller design space")
        variants, rejected, seen = [], [], set()
        for size in range(1, min(space.max_edits, len(edits)) + 1):
            for combination in itertools.combinations(edits, size):
                identifier = "variant/1:" + digest({"base": bound["endpoint"]["protocol"],
                                                     "space": space_id, "edits": combination})
                try:
                    recipe = mutate_recipe(_recipe(bound["recipe"]), combination, space)
                    graph = inspect_recipe(recipe)
                    if recipe.protocol_id in seen:
                        raise ValueError("another canonical combination yields this same recipe")
                    seen.add(recipe.protocol_id)
                    variants.append({"id": identifier, "edits": list(combination), "recipe": json.loads(canonical(asdict(recipe))),
                                     "protocol_id": recipe.protocol_id, "graph": graph})
                except (ValueError, OSError, ImportError) as error:
                    rejected.append({"id": identifier, "edits": list(combination), "error": str(error)})
        return {"variants": sorted(variants, key=lambda v: v["id"]), "rejected": rejected,
                "enumeration": "canonical_order_combinations_not_permutations", "planning_seconds": perf_counter() - started}

    def authorize(self, base_endpoint_id: str, space_id: str, *, panel_ids: Sequence[str],
                  quotes: Mapping[str, float], budget: float, max_trials: int,
                  policy: str = "linear_ucb", beta: float = 1.0, ridge: float = 1.0,
                  noise: float = 0.5, seed: int = 0, opportunity_cost: float | None = None,
                  rationale: str, max_variants: int = 32) -> str:
        from etalon.active.protocol_score import PANEL_VERSION, rank_variants

        if (not rationale.strip() or isinstance(panel_ids, str) or not 4 <= len(panel_ids) <= 128
                or len(set(panel_ids)) != len(panel_ids) or any(not isinstance(key, str) or not key for key in panel_ids)):
            raise ValueError("authorize a unique fixed panel of 4 to 128 molecules and give a rationale")
        if type(max_trials) is not int or not 1 <= max_trials <= len(quotes):
            raise ValueError("max_trials must fit the explicitly quoted variant set")
        finite(budget, "search budget", minimum=0)
        if budget <= 0:
            raise ValueError("search budget must be positive")
        catalogue = self.catalogue(base_endpoint_id, space_id, max_variants=max_variants)
        available = {v["id"]: v for v in catalogue["variants"]}
        if not quotes or set(quotes) - set(available):
            raise ValueError("quote only explicitly enumerated, valid variants")
        variants = []
        for key in sorted(quotes):
            finite(quotes[key], "per-query quote", minimum=0)
            limits = TrialPolicy(len(panel_ids) * quotes[key], len(panel_ids), 3, 3, 0.25)
            variants.append({**available[key], "quote": quotes[key], "trial_limits": limits.as_dict()})
        tokens = sorted({digest(edit) for variant in variants for edit in variant["edits"]})
        features = {v["id"]: [1.0, *[float(token in {digest(edit) for edit in v["edits"]}) / math.sqrt(len(v["edits"]))
                                      for token in tokens]] for v in variants}
        costs = {v["id"]: v["trial_limits"]["budget"] for v in variants}
        parameters = {"policy": policy, "beta": beta, "ridge": ridge, "noise": noise, "seed": seed}
        # Absence preserves legacy body identities and behavior, including old journals.
        if opportunity_cost is not None:
            parameters["opportunity_cost"] = opportunity_cost
        model = rank_variants(features, [], costs, **parameters)  # Validate before a write.
        with self.store.connection(write=True) as db:
            _idle(db)
            config = _configuration(db)
            endpoints = {e["id"]: e for e in config["endpoints"]}
            bound = _read(db, "protocol:recipe:" + base_endpoint_id)
            if bound is None or endpoints.get(base_endpoint_id) != bound["endpoint"]:
                raise StateError("base recipe binding changed")
            if any(v["protocol_id"] in {e["protocol"] for e in endpoints.values()} for v in variants):
                raise StateError("search variants must be new protocols, not registered aliases")
            if (not fits_budget(budget, self.store._balance(db, config["spec"]["budget"])["remaining"])
                    or not fits_budget(min(costs.values()), budget)):
                raise ValueError("search budget must cover a full panel and fit the remaining campaign budget")
            panel = _reference_panel(db, config["spec"]["objective"], list(panel_ids))
            body = {"version": VERSION, "base_endpoint": endpoints[base_endpoint_id], "space_id": space_id,
                    "objective": config["spec"]["objective"], "panel": panel,
                    "variants": variants, "features": features, "feature_names": ["intercept", *tokens],
                    "budget": budget, "max_trials": max_trials, "parameters": parameters,
                    "ranker_version": model["version"],
                    "reward_contract": {"version": PANEL_VERSION, "ridge": 0.1,
                                        "missing_penalty": "baseline_error_plus_baseline_SSE_over_panel_size"},
                    "rationale": rationale}
            if policy == "audit_ei":
                body["economic_contract"] = _economic_contract(
                    model["version"], model["economics"]["opportunity_cost"], config["spec"]["cost_unit"])
            identifier = "protocol-search/1:" + digest(body)
            previous = _read(db, "protocol:search:" + identifier)
            if previous is None:
                _save(db, {"id": identifier, "body": body, "status": "open", "experiments": []})
                self.store._event(db, "protocol_search_authorized", {"search_id": identifier, "body": body,
                                                                     "enumeration_seconds": catalogue["planning_seconds"],
                                                                     "rejected_variants": catalogue["rejected"]})
            return identifier

    def get(self, search_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            return _search(db, search_id)

    def searches(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM metadata WHERE key LIKE 'protocol:search:%' ORDER BY key")]

    def _plan(self, db: Any, record: dict[str, Any]) -> dict[str, Any]:
        from etalon.active.protocol_score import (
            AUDIT_RANKER_VERSION,
            PANEL_VERSION,
            RANKER_VERSION,
            rank_variants,
        )

        body = record["body"]
        economic = body["parameters"]["policy"] == "audit_ei"
        expected_version = AUDIT_RANKER_VERSION if economic else RANKER_VERSION
        if body["ranker_version"] != expected_version or body["reward_contract"]["version"] != PANEL_VERSION:
            raise StateError("protocol search ranker/reward version changed; explicitly authorize a new search")
        config = _configuration(db)
        if economic:
            expected_contract = _economic_contract(expected_version, body["parameters"].get("opportunity_cost"),
                                                   config["spec"]["cost_unit"])
            if body.get("economic_contract") != expected_contract:
                raise StateError("protocol search economic contract changed; explicitly authorize a new search")
        elif "economic_contract" in body or "opportunity_cost" in body["parameters"]:
            raise StateError("legacy protocol search cannot acquire an implicit economic stopping rule")
        observed = [{"id": e["variant_id"], "reward": e["reward"]["skill"], "evidence_hash": e["reward"]["evidence_hash"]}
                    for e in record["experiments"] if e["reward"] is not None]
        model = rank_variants(body["features"], observed,
                              {v["id"]: v["trial_limits"]["budget"] for v in body["variants"]}, **body["parameters"])
        if economic:
            model["economics"]["cost_unit"] = config["spec"]["cost_unit"]
        budget = _accounting(db, record)
        campaign = self.store._balance(db, config["spec"]["budget"])
        known = {e["protocol"] for e in config["endpoints"]}
        tried = {e["variant_id"] for e in record["experiments"]}
        ranking = [row for row in model["ranking"] if row["id"] not in tried
                   and _variant(record, row["id"])["protocol_id"] not in known
                   and fits_budget(row["cost"], min(budget["remaining"], budget["allocation_remaining"], campaign["remaining"]))]
        reason = "ready"
        if record["status"] != "open":
            reason = "search_closed"
        elif (db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone()
              or db.execute("SELECT 1 FROM rounds WHERE status='running'").fetchone()):
            reason = "campaign_not_idle"
        elif any(e["reward"] is None for e in record["experiments"]):
            reason = "audit_incomplete"
        elif len(record["experiments"]) >= body["max_trials"]:
            reason = "trial_limit"
        elif not ranking:
            reason = "no_affordable_untried_variants"
        elif economic and ranking[0]["score"] <= 0:
            # This is a read-only, one-step recommendation, not revocation, promotion,
            # a scientific failure claim, or permission to truncate a selected panel.
            reason = "economic_stop"
        cutoff = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
        result = {"search_id": record["id"], **model, "ranking": ranking, "selected": ranking[0]["id"] if ranking and reason == "ready" else None,
                  "stop_reason": reason, "budget": budget, "campaign_balance": campaign, "event_cutoff": cutoff,
                  "search_hash": digest(body), "reward_ids": [r["evidence_hash"] for r in observed],
                  "reference_action_ids": [r["action_id"] for r in body["panel"]],
                  "propensity": None, "selection_semantics": "deterministic_given_seed; not randomized-logging/OPE evidence"}
        return {**result, "snapshot_hash": digest(result)}

    def plan(self, search_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            db.execute("BEGIN")  # One read snapshot, including metadata, costs and event cutoff.
            return self._plan(db, _search(db, search_id))

    def propose_next(self, search_id: str, *, expected_snapshot_hash: str, rationale: str) -> str:
        if not rationale.strip():
            raise ValueError("protocol selection requires a rationale")
        with self.store.connection(write=True) as db:
            _idle(db)
            record = _search(db, search_id)
            plan = self._plan(db, record)
            if plan["snapshot_hash"] != expected_snapshot_hash:
                raise StateError("stale search snapshot; inspect the current plan before selecting")
            if plan["selected"] is None:
                raise StateError("search cannot propose: " + plan["stop_reason"])
            _check_references(db, record)
            endpoint_id = "search-" + digest([search_id, plan["selected"]])[:24]
            experiment = {"variant_id": plan["selected"], "endpoint_id": endpoint_id, "proposal_id": None,
                          "reward": None, "rationale": rationale, "decision": plan,
                          "allocated_budget": _variant(record, plan["selected"])["trial_limits"]["budget"]}
            record["experiments"].append(experiment)
            _save(db, record)
            _write(db, "protocol:search-endpoint:" + endpoint_id,
                   {"search_id": search_id, "variant_id": plan["selected"]})
            self.store._event(db, "protocol_search_selected", {"search_id": search_id, "experiment": experiment})
        # A crash here leaves an auditable intent, not a duplicate alias on resume.
        return self.resume_proposal(search_id)

    def resume_proposal(self, search_id: str) -> str:
        record = self.get(search_id)
        if not record["experiments"]:
            raise StateError("no durable selection intent to resume")
        experiment = record["experiments"][-1]
        if experiment["proposal_id"] is not None:
            return experiment["proposal_id"]
        variant = _variant(record, experiment["variant_id"])
        return self.registry.propose(record["body"]["base_endpoint"]["id"], experiment["endpoint_id"],
                                     space_id=record["body"]["space_id"], edits=variant["edits"], cost=variant["quote"],
                                     rationale=experiment["rationale"], proposed_by=VERSION)

    def audit_plan(self, search_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            db.execute("BEGIN")
            record = _search(db, search_id)
            if record["status"] != "open" or not record["experiments"]:
                raise StateError("an open search with a selected trial is required")
            experiment = record["experiments"][-1]
            if experiment["reward"] is not None or experiment["proposal_id"] is None:
                raise StateError("audit requires an unscored materialized proposal")
            proposal = _read(db, "protocol:proposal:" + experiment["proposal_id"])
            if proposal["status"] != "trial":
                raise StateError("validate and explicitly start the authorized trial before executing")
            attempted = {r[0] for r in db.execute("SELECT candidate_id FROM actions WHERE endpoint_id=?", (experiment["endpoint_id"],))}
            return {"search_id": search_id, "proposal_id": experiment["proposal_id"], "endpoint_id": experiment["endpoint_id"],
                    "candidate_ids": [r["candidate_id"] for r in record["body"]["panel"] if r["candidate_id"] not in attempted],
                    "budget": _accounting(db, record)}

    def run_audit(self, search_id: str, executor: Any, *, max_actions: int) -> dict[str, Any]:
        """Explicitly query a fixed panel, not an AL-selected survivor subset. No retries."""
        if type(max_actions) is not int or max_actions < 1 or not callable(executor):
            raise ValueError("audit execution needs an explicit executor and positive max_actions")
        executed = []
        reason = "action_limit"
        for _ in range(max_actions):
            with self.store.connection() as db:
                _idle(db)
            plan = self.audit_plan(search_id)
            if not plan["candidate_ids"]:
                reason = "panel_attempted"
                break
            candidate = self.store.candidates()[plan["candidate_ids"][0]]
            endpoint = self.store.configuration()[1][plan["endpoint_id"]]
            if endpoint.requires_handoff:
                raise StateError("this audit runner only dispatches explicitly bound CascadeRecipes")
            round_id = self.store.start_round({"mode": "fixed_protocol_audit", "search_id": search_id,
                                               "proposal_id": plan["proposal_id"]})
            try:
                action = self.store.reserve(round_id, candidate.id, endpoint.id,
                                            {"mode": "fixed_protocol_audit", "search_id": search_id})
            except (BudgetExhausted, StateError) as error:
                self.store.finish_round(round_id, {"actions": [], "error": str(error)})
                reason = "reservation_refused"
                break
            self.store.start_action(action.id)
            try:
                result = executor(action, candidate, endpoint, None)
                if not isinstance(result, Evaluation):
                    raise TypeError("executor must return an Evaluation")
                if (result.candidate_id, result.endpoint_id, result.units) != (candidate.id, endpoint.id, endpoint.units):
                    result = Evaluation(candidate.id, endpoint.id, None, endpoint.units, result.cost, status="invalid",
                                        provenance={"error": "audit executor identity/units mismatch", "raw_result": result.as_dict()})
            except Exception as error:
                result = Evaluation(candidate.id, endpoint.id, None, endpoint.units, action.reserved_cost,
                                    status="failed", provenance={"error": f"{type(error).__name__}: {error}",
                                                                 "cost_basis": "reservation; actual cost unavailable"})
            result = replace(result, provenance={**result.provenance, "endpoint_protocol": endpoint.protocol,
                                                  "protocol_search_id": search_id})
            self.store.resolve(action.id, result)
            self.store.finish_round(round_id, {"actions": [action.id], "mode": "fixed_protocol_audit"})
            executed.append(action.id)
        return {"search_id": search_id, "actions": executed, "stop_reason": reason, "balance": self.store.balance()}

    def score(self, search_id: str) -> dict[str, Any]:
        from etalon.active.protocol_score import PANEL_VERSION, panel_skill

        with self.store.connection(write=True) as db:
            _idle(db)
            record = _search(db, search_id)
            if not record["experiments"]:
                raise StateError("no audit experiment to score")
            experiment = record["experiments"][-1]
            if experiment["reward"] is not None:
                return experiment["reward"]
            control = _read(db, "protocol:endpoint:" + experiment["endpoint_id"])
            if (experiment["proposal_id"] is None or control is None
                    or control.get("proposal_id") != experiment["proposal_id"] or control.get("search_id") != search_id):
                raise StateError("audit scoring requires the search-bound controlled trial")
            if record["body"]["reward_contract"]["version"] != PANEL_VERSION:
                raise StateError("audit reward version changed after search authorization")
            _check_references(db, record)
            identifiers = [r["candidate_id"] for r in record["body"]["panel"]]
            rows = list(db.execute("SELECT a.id,a.candidate_id,a.round_id,a.status,o.admitted,o.body,o.ruling "
                                   "FROM actions a LEFT JOIN observations o ON o.action_id=a.id "
                                   "WHERE a.endpoint_id=? ORDER BY a.rowid", (experiment["endpoint_id"],)))
            grouped = {key: [r for r in rows if r[1] == key and r[2] > 0] for key in identifiers}
            if any(len(grouped[key]) != 1 or grouped[key][0][5] is None
                   or grouped[key][0][3] in {"reserved", "running"} for key in identifiers):
                raise StateError("complete the full runtime audit panel before freezing a reward; imports cannot substitute")
            values, costs, evidence = [], [], []
            for key in identifiers:
                row = grouped[key][0]
                result = json.loads(row[5])
                issue = protocol_evidence_issue(db, experiment["endpoint_id"], result, executed=True, action_id=row[0])
                values.append(result["value"] if row[4] and result["status"] == "ok" and issue is None else None)
                costs.append(result["cost"])
                evidence.append({"action_id": row[0], "result": result, "ruling": json.loads(row[6]),
                                 "admitted": bool(row[4]), "protocol_evidence_issue": issue})
            variant = _variant(record, experiment["variant_id"])
            reward = panel_skill([r["value"] for r in record["body"]["panel"]], values, costs,
                                 variant["quote"], ridge=record["body"]["reward_contract"]["ridge"])
            accounting_cost = _counts(db, experiment["endpoint_id"])["spent"]
            reward = {**reward, "search_id": search_id, "variant_id": variant["id"], "proposal_id": experiment["proposal_id"],
                      "panel_ids": identifiers, "evidence": evidence, "accounting_cost": accounting_cost,
                      "reference_action_ids": [r["action_id"] for r in record["body"]["panel"]],
                      "scope": "reused audit-panel selection response, not independent CADD generalization or causal repair evidence"}
            reward["evidence_hash"] = digest(reward)
            experiment["reward"] = reward
            _save(db, record)
            self.store._event(db, "protocol_search_scored", {"search_id": search_id, "reward": reward})
            return reward

    def close(self, search_id: str, *, rationale: str) -> None:
        if not rationale.strip():
            raise ValueError("closing a search requires a rationale")
        with self.store.connection(write=True) as db:
            _idle(db)
            record = _search(db, search_id)
            if record["status"] == "closed":
                return
            record["status"], record["closure"] = "closed", {"rationale": rationale, "budget": _accounting(db, record)}
            _save(db, record)
            self.store._event(db, "protocol_search_closed", {"search_id": search_id, **record["closure"]})
