"""Transactional experiment journal with reservations and conservative crash recovery.

Each action is recorded before invoking an external tool. A running action left by a crash keeps
its reservation and cannot be silently repeated. Resolve it with the real result (or a documented
failure and cost) before continuing. Events and current state commit in the same SQLite transaction.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from etalon.active.budget import cost_quote, fits_budget
from etalon.active.schema import (
    Action,
    CampaignSpec,
    Candidate,
    Endpoint,
    Evaluation,
    canonical,
    digest,
)
from etalon.judgment.waiver import WaiverSet
from etalon.learn.admissible import Admission, Measurement, Ruling, rule


class StateError(RuntimeError):
    """A state transition or resume would invalidate the experiment record."""


class BudgetExhausted(StateError):
    """The action does not fit after outstanding reservations are included."""


class CampaignStore:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path).resolve()
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, round_id INTEGER NOT NULL,
                    candidate_id TEXT NOT NULL REFERENCES candidates(id),
                    endpoint_id TEXT NOT NULL, replicate INTEGER NOT NULL,
                    reservation REAL NOT NULL, cost REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL, body TEXT NOT NULL,
                    UNIQUE(candidate_id, endpoint_id, replicate)
                );
                CREATE TABLE IF NOT EXISTS observations (
                    action_id TEXT PRIMARY KEY REFERENCES actions(id),
                    admitted INTEGER NOT NULL, body TEXT NOT NULL, ruling TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if write and self.read_only:
            raise StateError("journal was opened read-only")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro" if self.read_only else str(self.path),
                             uri=self.read_only, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, kind: str, body: Any) -> None:
        db.execute("INSERT INTO events(at,kind,body) VALUES (?,?,?)", (
            datetime.now(UTC).isoformat(), kind, canonical(body),
        ))

    def configure(self, spec: CampaignSpec, endpoints: Sequence[Endpoint]) -> None:
        by_id = {endpoint.id: endpoint for endpoint in endpoints}
        if len(by_id) != len(endpoints) or spec.objective not in by_id:
            raise ValueError("endpoint ids must be unique and include the objective")
        if len({endpoint.target for endpoint in endpoints}) != 1:
            raise ValueError("a campaign models one target; create a separate campaign per target")
        body = canonical({"schema_version": 1, "spec": spec.as_dict(),
                          "endpoints": [by_id[key].as_dict() for key in sorted(by_id)]})
        with self.connection(write=True) as db:
            previous = db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()
            if previous is not None:
                # Older journals omit newly introduced default policy settings. Compare their
                # meaning without rewriting history or changing a non-default setting.
                old = json.loads(previous[0])
                normalized = {**old, "spec": CampaignSpec(**old["spec"]).as_dict()}
                if canonical(normalized) != body:
                    raise StateError("campaign configuration differs from its journal; use a new database")
            if previous is None:
                db.execute("INSERT INTO metadata VALUES ('configuration',?)", (body,))
                self._event(db, "configured", json.loads(body))

    @staticmethod
    def _configuration(db: sqlite3.Connection) -> tuple[CampaignSpec, dict[str, Endpoint]]:
        row = db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()
        if row is None:
            raise StateError("configure the campaign before adding experiments")
        data = json.loads(row[0])
        return CampaignSpec(**data["spec"]), {e["id"]: Endpoint(**e) for e in data["endpoints"]}

    def configuration(self) -> tuple[CampaignSpec, dict[str, Endpoint]]:
        with self.connection() as db:
            return self._configuration(db)

    def bind_resource(self, name: str, identity: dict[str, Any], *, before_first_round: bool = False) -> None:
        """Pin a resource identity, optionally refusing retroactive attribution of execution.

        An already-matching pin can always be checked on resume. With before_first_round,
        a missing pin must be recorded before any runtime round/action, in the same write
        transaction as the history check. Explicit historical imports remain distinguishable.
        """
        if (not isinstance(name, str) or not name.strip() or not isinstance(identity, dict)
                or type(before_first_round) is not bool):
            raise ValueError("resource binding needs a name, identity mapping and boolean execution guard")
        body = canonical(identity)
        with self.connection(write=True) as db:
            key = "resource:" + name
            previous = db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()
            if previous is not None and previous[0] != body:
                raise StateError(f"resource {name!r} changed; use a new campaign journal")
            if previous is None:
                if before_first_round and (
                        db.execute("SELECT 1 FROM rounds LIMIT 1").fetchone()
                        or db.execute("SELECT 1 FROM actions WHERE round_id!=0 LIMIT 1").fetchone()):
                    raise StateError("cannot retroactively bind a missing implementation identity after execution rounds")
                db.execute("INSERT INTO metadata VALUES (?,?)", (key, body))
                self._event(db, "resource_bound", {**identity, "name": name})

    def register_endpoints(self, endpoints: Sequence[Endpoint], *, rationale: str) -> None:
        """Add redesigned component/cascade protocols without rewriting historical labels.

        Existing endpoints and the objective are immutable. New protocols use new ids and learn
        their own relation to the unchanged objective. Register only between resolved rounds.
        """
        if not rationale.strip():
            raise ValueError("a reason for the new protocol is required")
        with self.connection(write=True) as db:
            row = db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()
            if row is None:
                raise StateError("configure a campaign before registering additional endpoints")
            if (db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone()
                    or db.execute("SELECT 1 FROM rounds WHERE status='running'").fetchone()):
                raise StateError("finish running rounds and resolve pending actions before redesigning the available protocols")
            config = json.loads(row[0])
            known = {e["id"]: e for e in config["endpoints"]}
            controlled = {e["protocol"] for e in known.values() if db.execute(
                "SELECT 1 FROM metadata WHERE key=?", ("protocol:endpoint:" + e["id"],)).fetchone()}
            target = known[config["spec"]["objective"]]["target"]
            for endpoint in endpoints:
                body = endpoint.as_dict()
                if endpoint.target != target:
                    raise ValueError("new endpoints must measure the same campaign target")
                if endpoint.id in known:
                    if canonical(known[endpoint.id]) != canonical(body):
                        raise StateError("protocol changed under an existing id; register a new endpoint id")
                    continue
                if endpoint.protocol in controlled:
                    raise StateError("a controlled protocol cannot be registered under an unrestricted alias")
                from etalon.active.proposer import reject_search_registration

                reject_search_registration(db, body)
                known[endpoint.id] = body
                self._event(db, "endpoint_registered", {"endpoint": body, "rationale": rationale})
            config["endpoints"] = [known[key] for key in sorted(known)]
            db.execute("UPDATE metadata SET body=? WHERE key='configuration'", (canonical(config),))

    def add_candidates(self, candidates: Sequence[Candidate]) -> int:
        self.configuration()
        added = 0
        with self.connection(write=True) as db:
            first = db.execute("SELECT body FROM candidates LIMIT 1").fetchone()
            width = len(json.loads(first[0])["features"]) if first else None
            for candidate in candidates:
                if width is None:
                    width = len(candidate.features)
                if len(candidate.features) != width:
                    raise ValueError("all candidates must use the declared representation width")
                body = canonical(candidate.as_dict())
                previous = db.execute("SELECT body FROM candidates WHERE id=?", (candidate.id,)).fetchone()
                if previous is not None:
                    if previous[0] != body:
                        raise StateError(f"candidate {candidate.id!r} changed; register a new state id")
                    continue
                db.execute("INSERT INTO candidates VALUES (?,?)", (candidate.id, body))
                self._event(db, "candidate_added", candidate.as_dict())
                added += 1
        return added

    @staticmethod
    def _candidates(db: sqlite3.Connection) -> dict[str, Candidate]:
        rows = db.execute("SELECT body FROM candidates ORDER BY id").fetchall()
        return {value["id"]: Candidate.from_dict(value)
                for row in rows if (value := json.loads(row[0]))}

    def candidates(self) -> dict[str, Candidate]:
        with self.connection() as db:
            return self._candidates(db)

    @staticmethod
    def _balance(db: sqlite3.Connection, budget: float) -> dict[str, float]:
        row = db.execute("""SELECT COALESCE(SUM(cost),0),
            COALESCE(SUM(CASE WHEN status IN ('reserved','running') THEN reservation ELSE 0 END),0)
            FROM actions""").fetchone()
        balance = {"budget": budget, "spent": row[0], "reserved": row[1],
                   "remaining": budget - row[0] - row[1]}
        if any(not math.isfinite(value) for value in balance.values()):
            raise StateError("campaign accounting exceeds finite numeric range; preserve the recorded charges "
                             "and rescale cost units in a new journal")
        return balance

    def balance(self) -> dict[str, float]:
        with self.connection() as db:
            db.execute("BEGIN")
            spec, _ = self._configuration(db)
            return self._balance(db, spec.budget)

    def start_round(self, model: dict[str, Any], *, expected_event_cutoff: int | None = None) -> int:
        """Start an idle round, optionally only if a read-only planning snapshot is current.

        A different worker's unfinished round is never implicitly interrupted. After an
        actual crash, reconcile outcomes and call recover_idle_rounds explicitly.
        """
        if not isinstance(model, dict):
            raise ValueError("round planning metadata must be a mapping")
        if expected_event_cutoff is not None and (
                isinstance(expected_event_cutoff, bool) or not isinstance(expected_event_cutoff, int)
                or expected_event_cutoff < 0):
            raise ValueError("expected_event_cutoff must be a nonnegative integer")
        with self.connection(write=True) as db:
            self._configuration(db)
            if expected_event_cutoff is not None:
                current = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
                if current != expected_event_cutoff:
                    raise StateError("stale planning snapshot; inspect the current state before starting a round")
            if db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone():
                raise StateError("unresolved actions exist; recover them before starting a round")
            if db.execute("SELECT 1 FROM rounds WHERE status='running'").fetchone():
                raise StateError("an unfinished round exists; finish it or explicitly recover idle rounds before continuing")
            cursor = db.execute("INSERT INTO rounds(body,status) VALUES (?,'running')", (canonical(model),))
            identifier = int(cursor.lastrowid)
            self._event(db, "round_started", {**model, "round_id": identifier})
            return identifier

    def finish_round(self, identifier: int, summary: dict[str, Any]) -> None:
        if type(identifier) is not int or identifier < 1 or not isinstance(summary, dict):
            raise ValueError("finish_round requires a positive integer round id and mapping summary")
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM actions WHERE round_id=? AND status IN ('reserved','running')",
                          (identifier,)).fetchone():
                raise StateError("cannot finish a round with unresolved actions")
            row = db.execute("SELECT body,status FROM rounds WHERE id=?", (identifier,)).fetchone()
            if row is None or row[1] != "running":
                raise StateError("round is not running")
            body = {**json.loads(row[0]), **summary}
            db.execute("UPDATE rounds SET body=?,status='completed' WHERE id=?", (canonical(body), identifier))
            self._event(db, "round_completed", {**summary, "round_id": identifier})

    def recover_idle_rounds(self, *, reason: str) -> tuple[int, ...]:
        """Explicitly close abandoned rounds after all outcomes have been reconciled.

        Call only after confirming no worker is still dispatching actions. No action is
        executed, refunded or retried. Pending actions must first be resolved with their
        actual outcome/cost; even an empty running round otherwise blocks protocol edits.
        """
        if not reason.strip():
            raise ValueError("round recovery requires a documented reason")
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone():
                raise StateError("resolve pending actions before recovering interrupted rounds")
            identifiers = tuple(r[0] for r in db.execute("SELECT id FROM rounds WHERE status='running' ORDER BY id"))
            for identifier in identifiers:
                db.execute("UPDATE rounds SET status='interrupted' WHERE id=?", (identifier,))
                self._event(db, "round_interrupted", {"round_id": identifier, "reason": reason,
                                                      "recovery": "explicit_no_pending_actions"})
            return identifiers

    @staticmethod
    def _protocol_ruling(db: sqlite3.Connection, result: Evaluation, ruling: Ruling, *,
                         executed: bool, action_id: str | None = None) -> Ruling:
        from etalon.active.protocols import protocol_evidence_issue

        issue = protocol_evidence_issue(db, result.endpoint_id, result.as_dict(), executed=executed, action_id=action_id)
        if issue is None:
            return ruling
        # This is an identity/admission check, not a fabricated scientific fault observation.
        return replace(ruling, admission=Admission.WITHHELD,
                       blocking=(*ruling.blocking, "PROTOCOL_EVIDENCE_MISMATCH"), notes=(*ruling.notes, issue))

    def reserve(self, round_id: int, candidate_id: str, endpoint_id: str,
                decision: dict[str, Any]) -> Action:
        if (type(round_id) is not int or round_id < 1
                or any(not isinstance(value, str) or not value.strip() for value in (candidate_id, endpoint_id))
                or not isinstance(decision, dict)):
            raise ValueError("reserve requires a positive integer round, string identities and mapping decision")
        with self.connection(write=True) as db:
            spec, endpoints = self._configuration(db)
            if endpoint_id not in endpoints:
                raise ValueError("reserve requires a registered endpoint")
            endpoint = endpoints[endpoint_id]
            if db.execute("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)).fetchone() is None:
                raise ValueError("reserve requires a registered candidate")
            current = db.execute("SELECT status FROM rounds WHERE id=?", (round_id,)).fetchone()
            if current is None or current[0] != "running":
                raise StateError("actions require a running round")
            attempts = db.execute("SELECT COUNT(*) FROM actions WHERE candidate_id=? AND endpoint_id=?",
                                  (candidate_id, endpoint_id)).fetchone()[0]
            if attempts >= endpoint.max_replicates:
                raise StateError("replicate limit reached; implicit retries are not permitted")
            history = [{"result": json.loads(row[0])} for row in db.execute("SELECT body FROM observations")]
            quote = cost_quote(endpoint, history)
            from etalon.active.protocols import enforce_protocol_quota

            enforce_protocol_quota(db, endpoint_id, quote, candidate_id=candidate_id)
            remaining = self._balance(db, spec.budget)["remaining"]
            if not fits_budget(quote, remaining):
                raise BudgetExhausted("action exceeds the unreserved budget")
            if spec.policy == "decision_aware" and spec.confirmation_reserve and endpoint_id != spec.objective:
                # Enforce the guard in the same transaction as the reservation. A stale plan
                # or a concurrent historical import must not consume the final quoted assay.
                high = endpoints[spec.objective]
                counts = dict(db.execute("SELECT candidate_id,COUNT(*) FROM actions WHERE endpoint_id=? "
                                         "GROUP BY candidate_id", (spec.objective,)).fetchall())
                eligible = 0
                for candidate_row in db.execute("SELECT body FROM candidates"):
                    candidate = json.loads(candidate_row[0])
                    eligible += int(counts.get(candidate["id"], 0) < high.max_replicates
                                    and (not high.requires_handoff or bool(candidate["handoff"])))
                guard = min(spec.confirmation_reserve, eligible) * cost_quote(high, history)
                if not eligible or not fits_budget(quote + guard, remaining):
                    raise BudgetExhausted("proxy action would consume the objective confirmation budget")
            action = Action(uuid.uuid4().hex, round_id, candidate_id, endpoint_id,
                            attempts, quote, decision)
            db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,0,'reserved',?)", (
                action.id, round_id, candidate_id, endpoint_id, attempts, quote,
                canonical(action.as_dict()),
            ))
            self._event(db, "action_reserved", action.as_dict())
            return action

    def start_action(self, action_id: str) -> None:
        with self.connection(write=True) as db:
            cursor = db.execute("UPDATE actions SET status='running' WHERE id=? AND status='reserved'",
                                (action_id,))
            if cursor.rowcount != 1:
                raise StateError("only a reserved action can start; running actions require recovery")
            self._event(db, "action_started", {"action_id": action_id})

    def resolve(self, action_id: str, result: Evaluation, *, waivers: WaiverSet | None = None) -> None:
        """Commit a real outcome; quality failures retain their cost and raw evidence."""

        with self.connection(write=True) as db:
            _, endpoints = self._configuration(db)
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row is None or row["status"] not in {"reserved", "running"}:
                raise StateError("action is absent or already resolved")
            if (result.candidate_id, result.endpoint_id) != (row["candidate_id"], row["endpoint_id"]):
                raise ValueError("result belongs to a different molecule or endpoint")
            if result.units != endpoints[result.endpoint_id].units:
                raise ValueError("result units do not match the endpoint; no implicit conversion")
            ruling = rule(Measurement(result.candidate_id, None, result.value,
                                      result.checks, result.units, result.provenance),
                          waivers, require_comparator=False)
            ruling = self._protocol_ruling(db, result, ruling, executed=row["round_id"] > 0, action_id=action_id)
            admitted = result.status == "ok" and ruling.teaches
            status = "completed" if admitted else ("invalid" if result.status == "ok" else result.status)
            db.execute("INSERT INTO observations VALUES (?,?,?,?)", (
                action_id, int(admitted), canonical(result.as_dict()), canonical(ruling.as_dict()),
            ))
            db.execute("UPDATE actions SET status=?,cost=? WHERE id=?", (status, result.cost, action_id))
            self._event(db, "action_resolved", {"action_id": action_id, "result": result.as_dict(),
                                               "admitted": admitted, "ruling": ruling.as_dict()})

    def import_evaluation(self, result: Evaluation, *, source_id: str) -> None:
        """Seed from explicit historical evidence. Costs supplied here count toward the budget."""

        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("a known endpoint and a stable source id are required")
        identifier = "import-" + digest([source_id, result.candidate_id, result.endpoint_id])
        ruling = rule(Measurement(result.candidate_id, None, result.value, result.checks,
                                  result.units, result.provenance), require_comparator=False)
        with self.connection(write=True) as db:
            _, endpoints = self._configuration(db)
            if result.endpoint_id not in endpoints:
                raise ValueError("a known endpoint and a stable source id are required")
            if result.units != endpoints[result.endpoint_id].units:
                raise ValueError("historical observation units differ from the endpoint")
            if db.execute("SELECT 1 FROM candidates WHERE id=?", (result.candidate_id,)).fetchone() is None:
                raise ValueError("historical observation requires a registered candidate")
            previous = db.execute("SELECT body FROM observations WHERE action_id=?", (identifier,)).fetchone()
            if previous is not None:
                if previous[0] != canonical(result.as_dict()):
                    raise StateError("historical source changed; record a new source version")
                return
            ruling = self._protocol_ruling(db, result, ruling, executed=False)
            admitted = result.status == "ok" and ruling.teaches
            count = db.execute("SELECT COUNT(*) FROM actions WHERE candidate_id=? AND endpoint_id=?",
                               (result.candidate_id, result.endpoint_id)).fetchone()[0]
            action = Action(identifier, 0, result.candidate_id, result.endpoint_id, count, 0.0,
                            {"source_id": source_id})
            db.execute("INSERT INTO actions VALUES (?,?,?,?,?,0,?,?,?)", (
                identifier, 0, result.candidate_id, result.endpoint_id, count, result.cost,
                "completed" if admitted else "invalid", canonical(action.as_dict()),
            ))
            db.execute("INSERT INTO observations VALUES (?,?,?,?)", (
                identifier, int(admitted), canonical(result.as_dict()), canonical(ruling.as_dict()),
            ))
            self._event(db, "observation_imported", {"action": action.as_dict(),
                                                   "result": result.as_dict(), "admitted": admitted})

    @staticmethod
    def _actions(db: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = db.execute("SELECT body,status,cost FROM actions ORDER BY rowid").fetchall()
        return [{**json.loads(row[0]), "status": row[1], "cost": row[2]} for row in rows]

    def actions(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return self._actions(db)

    @staticmethod
    def _observations(db: sqlite3.Connection, *, admitted_only: bool = False) -> list[dict[str, Any]]:
        rows = db.execute("SELECT * FROM observations" + (" WHERE admitted=1" if admitted_only else "")
                          + " ORDER BY rowid").fetchall()
        return [{"action_id": row["action_id"], "admitted": bool(row["admitted"]),
                 "result": json.loads(row["body"]), "ruling": json.loads(row["ruling"])} for row in rows]

    def observations(self, *, admitted_only: bool = False) -> list[dict[str, Any]]:
        with self.connection() as db:
            return self._observations(db, admitted_only=admitted_only)

    @staticmethod
    def _rounds(db: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = db.execute("SELECT * FROM rounds ORDER BY id").fetchall()
        return [{**json.loads(row["body"]), "round_id": row["id"], "status": row["status"]} for row in rows]

    def rounds(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return self._rounds(db)

    @staticmethod
    def _events(db: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = db.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        return [{"sequence": r["sequence"], "at": r["at"], "kind": r["kind"],
                 "body": json.loads(r["body"])} for r in rows]

    def events(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return self._events(db)

    def _snapshot(self, db: sqlite3.Connection) -> dict[str, Any]:
        from etalon.active.proposer import _search_candidate_limits
        from etalon.active.protocols import _capacity

        spec, endpoints = self._configuration(db)
        observations = self._observations(db)
        return {"spec": spec, "endpoints": endpoints, "candidates": self._candidates(db),
                "observations": observations, "actions": self._actions(db),
                "balance": self._balance(db, spec.budget), "rounds": self._rounds(db),
                "event_cutoff": db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0],
                "endpoint_limits": {key: cap for key, endpoint in endpoints.items()
                                    if (cap := _capacity(db, key, cost_quote(endpoint, observations))) is not None},
                "endpoint_candidates": _search_candidate_limits(db)}

    def snapshot(self) -> dict[str, Any]:
        """One read-only SQLite snapshot for scientific planning and its dispatch guard.

        Configuration/candidates use the existing typed objects. Evidence, costs, protocol
        permissions and the event cutoff all describe the same committed database state.
        No model is fitted, no resource is acquired and no journal row is rewritten here.
        """
        with self.connection() as db:
            db.execute("BEGIN")
            return self._snapshot(db)

    @staticmethod
    def _status(snapshot: dict[str, Any]) -> dict[str, Any]:
        spec, endpoints = snapshot["spec"], snapshot["endpoints"]
        observations, actions = snapshot["observations"], snapshot["actions"]
        return {"spec": spec.as_dict(), "endpoints": [e.as_dict() for e in endpoints.values()],
                "balance": snapshot["balance"], "candidates": len(snapshot["candidates"]),
                "observations": len(observations), "admitted": sum(o["admitted"] for o in observations),
                "pending": [a for a in actions if a["status"] in {"reserved", "running"}],
                "rounds": snapshot["rounds"]}

    def status(self) -> dict[str, Any]:
        return self._status(self.snapshot())

    def export(self) -> dict[str, Any]:
        """Export evidence, events and displayed state from one committed read snapshot."""
        with self.connection() as db:
            db.execute("BEGIN")
            snapshot = self._snapshot(db)
            return {"state": self._status(snapshot),
                    "candidates": [candidate.as_dict() for candidate in snapshot["candidates"].values()],
                    "actions": snapshot["actions"], "observations": snapshot["observations"],
                    "events": self._events(db), "event_cutoff": snapshot["event_cutoff"]}
