"""Select, reserve, authorize, execute, admit, retrain: the executable feedback loop.

The numerical policy owns selection, not a language model. Executors are explicit injected
callables; constructing a campaign never launches a tool. Interrupts keep their reservation until
an operator resolves the real outcome, rather than guessing whether an external job ran.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from etalon.active.budget import cost_quote
from etalon.active.model import MultiEndpointGP
from etalon.active.policy import Choice, choose
from etalon.active.schema import Action, Candidate, Endpoint, Evaluation
from etalon.active.store import BudgetExhausted, CampaignStore, StateError
from etalon.authority.grant import SpendAuthorization, authorize, require
from etalon.faults.preflight import check_record
from etalon.judgment.waiver import WaiverSet


class Executor(Protocol):
    def __call__(self, action: Action, candidate: Candidate, endpoint: Endpoint,
                 grant: SpendAuthorization | None) -> Evaluation: ...


class ActiveCampaign:
    def __init__(self, store: CampaignStore, executor: Executor | None = None, *,
                 receptor_path: str | Path | None = None, waivers: WaiverSet | None = None,
                 toolchain_active: bool | None = None) -> None:
        self.store = store
        self.executor = executor
        self.receptor_path = Path(receptor_path).resolve() if receptor_path is not None else None
        self.waivers = waivers or WaiverSet()
        self.toolchain_active = toolchain_active

    def plan(self) -> tuple[MultiEndpointGP | None, list[Choice], str]:
        """Read-only planning. The same decision is recomputed before execution."""
        return self._plan_snapshot(self.store.snapshot())

    @staticmethod
    def _plan_snapshot(state: dict[str, Any]) -> tuple[MultiEndpointGP | None, list[Choice], str]:
        spec, endpoints = state["spec"], state["endpoints"]
        candidates, actions = state["candidates"], state["actions"]
        if any(a["status"] in {"reserved", "running"} for a in actions):
            raise StateError("unresolved actions exist; inspect and resolve their real outcomes before resuming")
        if any(r["status"] == "running" for r in state["rounds"]):
            raise StateError("an unfinished round exists; confirm its worker stopped and explicitly recover it")
        if not candidates:
            return None, [], "empty_pool"
        if len(candidates) > spec.max_candidates:
            raise ValueError(f"pilot pool limit is {spec.max_candidates}; use a scalable backend or an explicit smaller pool")
        observations = state["observations"]
        model = MultiEndpointGP(candidates, endpoints, spec.objective, observations,
                                limit=spec.max_observations)
        remaining = state["balance"]["remaining"]
        choices = choose(spec, candidates, endpoints, model, actions, observations,
                         remaining=remaining, slots=spec.batch_size,
                         endpoint_limits=state["endpoint_limits"],
                         endpoint_candidates=state["endpoint_candidates"])
        if not choices:
            if (spec.policy == "decision_aware" and spec.confirmation_reserve
                    and remaining < cost_quote(endpoints[spec.objective], observations)):
                reason = "confirmation_unaffordable"
            else:
                reason = "budget_exhausted" if remaining < min(cost_quote(e, observations) for e in endpoints.values()) else "no_eligible_actions"
            return model, [], reason
        choices = [replace(choice, evidence={**choice.evidence, "planning_event_cutoff": state["event_cutoff"]})
                   for choice in choices]
        return model, choices, "ready"

    def recommend(self) -> dict[str, Any]:
        """Read-only, acquired-data-only provisional and evidence-backed recommendations."""
        state = self.store.snapshot()
        spec, endpoints, candidates = state["spec"], state["endpoints"], state["candidates"]
        if len(candidates) > spec.max_candidates:
            raise ValueError(f"pilot pool limit is {spec.max_candidates}")
        observations = state["observations"]
        model = (MultiEndpointGP(candidates, endpoints, spec.objective, observations,
                                 limit=spec.max_observations) if candidates else None)
        return self._recommend_snapshot(state, model)

    @staticmethod
    def _recommend_snapshot(state: dict[str, Any], model: MultiEndpointGP | None) -> dict[str, Any]:
        from etalon.active.recommendation import recommend

        return recommend(state["spec"], state["candidates"], state["endpoints"], model,
                         state["actions"], state["observations"],
                         remaining=state["balance"]["remaining"],
                         endpoint_limits=state["endpoint_limits"],
                         endpoint_candidates=state["endpoint_candidates"])

    def inspect(self) -> dict[str, Any]:
        """Plan and recommend using one committed state and one model fit, without writes."""
        state = self.store.snapshot()
        model, choices, reason = self._plan_snapshot(state)
        return {"stop_reason": reason, "model": model.snapshot() if model else None,
                "event_cutoff": state["event_cutoff"],
                "recommendation": self._recommend_snapshot(state, model),
                "choices": [{"candidate_id": choice.candidate_id, "endpoint_id": choice.endpoint_id,
                             "score": choice.score, "evidence": choice.evidence} for choice in choices]}

    def run_round(self) -> dict[str, Any]:
        started = perf_counter()
        model, choices, reason = self.plan()
        planning_seconds = perf_counter() - started
        if not choices:
            return {"stop_reason": reason, "actions": [], "balance": self.store.balance()}
        assert model is not None
        if self.executor is None:
            raise ValueError("execution requires an explicit executor; plan() is read-only")
        cutoffs = {choice.evidence.get("planning_event_cutoff") for choice in choices}
        if len(cutoffs) != 1 or type(next(iter(cutoffs))) is not int:
            raise StateError("execution requires choices from one coherent journal snapshot")
        cutoff = next(iter(cutoffs))
        _, endpoints = self.store.configuration()
        candidates = self.store.candidates()
        round_id = self.store.start_round({"model": model.snapshot(),
                                          "training_actions": model.training_action_ids,
                                          "planning_seconds": planning_seconds,
                                          "planning_event_cutoff": cutoff}, expected_event_cutoff=cutoff)
        executed: list[str] = []
        reservation_error = None
        from etalon.active.protocols import ProtocolUnavailable

        for choice in choices:
            try:
                action = self.store.reserve(round_id, choice.candidate_id, choice.endpoint_id,
                                            {**choice.evidence, "score": choice.score})
            except ProtocolUnavailable:
                reason = "protocol_limit_reached"
                break
            except BudgetExhausted:
                # A previous tool may report an actual cost above its reservation. Keep the real
                # charge, stop dispatching, and expose overspend instead of rewriting history.
                reason = "budget_exhausted"
                break
            except StateError as error:
                # New external evidence may consume a selected replicate after the round
                # began. Do not retry, dispatch a stale choice or strand an empty round.
                reason, reservation_error = "state_changed", str(error)
                break
            candidate, endpoint = candidates[action.candidate_id], endpoints[action.endpoint_id]
            checks = ()
            grant = None
            if endpoint.requires_handoff:
                try:
                    if self.receptor_path is None:
                        raise ValueError("a live handoff endpoint requires the actual receptor_path")
                    checks = check_record(candidate.handoff, receptor_path=self.receptor_path,
                                          toolchain_active=self.toolchain_active)
                    authorized = authorize([candidate.handoff], receptor_path=self.receptor_path,
                                           toolchain_active=self.toolchain_active, waivers=self.waivers)
                    grant = require(candidate.handoff, authorized.grants, receptor_path=self.receptor_path)
                except (OSError, ValueError, PermissionError) as error:
                    self.store.resolve(action.id, Evaluation(candidate.id, endpoint.id, None, endpoint.units,
                        0.0, status="blocked", checks=checks,
                        provenance={"phase": "preflight", "error": str(error)}), waivers=self.waivers)
                    executed.append(action.id)
                    continue
            self.store.start_action(action.id)
            try:
                result = self.executor(action, candidate, endpoint, grant)
                if not isinstance(result, Evaluation):
                    raise TypeError("executor must return an Evaluation")
                if (result.candidate_id, result.endpoint_id, result.units) != (candidate.id, endpoint.id, endpoint.units):
                    result = Evaluation(candidate.id, endpoint.id, None, endpoint.units, result.cost,
                                        status="invalid", provenance={"error": "executor returned mismatched identity or units",
                                                                      "raw_result": result.as_dict()})
            except Exception as error:
                # No automatic retry, and no fictional free failed calculation. An executor that
                # can measure partial cost should return a failed Evaluation with that cost.
                result = Evaluation(candidate.id, endpoint.id, None, endpoint.units, action.reserved_cost,
                                    status="failed", provenance={"error": f"{type(error).__name__}: {error}",
                                                                 "cost_basis": "reservation; actual cost unavailable"})
            result = replace(result, checks=checks + result.checks, provenance={
                **result.provenance, "endpoint_protocol": endpoint.protocol,
                "authorization": grant.as_dict() if grant else None,
            })
            self.store.resolve(action.id, result, waivers=self.waivers)
            executed.append(action.id)
        summary = {"actions": executed, "balance": self.store.balance(), "stop_reason": reason,
                   "admitted_total": len(self.store.observations(admitted_only=True))}
        if reservation_error is not None:
            summary["reservation_error"] = reservation_error
        self.store.finish_round(round_id, summary)
        return {"round_id": round_id, **summary}

    def run(self, *, max_rounds: int) -> dict[str, Any]:
        if type(max_rounds) is not int or max_rounds < 1:
            raise ValueError("max_rounds must be a positive integer")
        rounds = []
        reason = "round_limit"
        for _ in range(max_rounds):
            outcome = self.run_round()
            if outcome["actions"]:
                rounds.append(outcome)
            if outcome["stop_reason"] != "ready":
                reason = outcome["stop_reason"]
                break
        return {"stop_reason": reason, "rounds": rounds, "state": self.store.status()}
