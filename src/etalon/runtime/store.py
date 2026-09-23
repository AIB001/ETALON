"""Transactional workflow state, fenced ownership, receipts and resource accounting.

Scientific observations stay in CampaignStore. This journal links their execution
to data and screening steps; it never turns a missing receipt into a free success.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from etalon.active.schema import canonical, digest
from etalon.active.store import StateError
from etalon.runtime.schema import absolute, amounts, identifier


class RuntimeStore:
    def __init__(self, workspace: str | Path, *, create: bool = False):
        self.workspace = absolute(workspace).resolve()
        self.path = self.workspace / "etalon-runtime.sqlite"
        if any(Path(str(self.path) + s).is_symlink() for s in ("", "-wal", "-shm", "-journal")):
            raise ValueError("runtime journal and sidecars cannot be symlinks")
        if not create and not self.path.is_file():
            raise FileNotFoundError(self.path)
        if create:
            self.workspace.mkdir(parents=True, exist_ok=True)
            with self.connection(write=True) as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, plan TEXT NOT NULL,
                        state TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
                        pid INTEGER, process_identity TEXT, heartbeat REAL,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        created REAL NOT NULL, updated REAL NOT NULL,
                        error TEXT, llm_calls INTEGER NOT NULL DEFAULT 0,
                        proposal TEXT, elapsed REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS nodes (
                        job TEXT NOT NULL REFERENCES jobs(id), id TEXT NOT NULL,
                        definition TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting',
                        parameters TEXT, result TEXT, evidence TEXT, costs TEXT NOT NULL DEFAULT '{}',
                        error TEXT, started REAL, finished REAL,
                        PRIMARY KEY (job,id)
                    );
                    CREATE TABLE IF NOT EXISTS receipts (
                        job TEXT NOT NULL, node TEXT NOT NULL, key TEXT NOT NULL,
                        body TEXT NOT NULL, sha256 TEXT NOT NULL,
                        PRIMARY KEY (job,node,key), FOREIGN KEY (job,node) REFERENCES nodes(job,id)
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        job TEXT NOT NULL REFERENCES jobs(id), at REAL NOT NULL,
                        kind TEXT NOT NULL, body TEXT NOT NULL
                    );
                """)

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path) if write else self.path.as_uri() + "?mode=ro",
                             uri=not write, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
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
    def event(db: sqlite3.Connection, job: str, kind: str, body: Any) -> None:
        db.execute("INSERT INTO events(job,at,kind,body) VALUES (?,?,?,?)",
                   (job, time.time(), kind, canonical(body)))

    @staticmethod
    def owned(db: sqlite3.Connection, job: str, epoch: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None or row["epoch"] != epoch:
            raise StateError("workflow ownership changed; stale worker cannot publish")
        return row

    def create(self, job: str, plan: dict) -> bool:
        identifier(job)
        created = False
        with self.connection(write=True) as db:
            old = db.execute("SELECT plan_id,plan FROM jobs WHERE id=?", (job,)).fetchone()
            if old:
                if old["plan_id"] != plan["plan_id"] or old["plan"] != canonical(plan):
                    raise StateError("job id already belongs to a different workflow or input plan")
            else:
                if self.job_root(job).exists():
                    raise FileExistsError("job directory exists without an owning runtime record")
                now = time.time()
                db.execute("INSERT INTO jobs(id,plan_id,plan,state,created,updated) VALUES (?,?,?,'queued',?,?)",
                           (job, plan["plan_id"], canonical(plan), now, now))
                for node in plan["spec"]["nodes"]:
                    db.execute("INSERT INTO nodes(job,id,definition) VALUES (?,?,?)", (job, node["id"], canonical(node)))
                self.event(db, job, "created", {"plan_id": plan["plan_id"]})
                created = True
        # Persist ownership before materializing its directory. A failed commit
        # must not leave an orphan that neither status nor reconcile can address.
        # Repeated submit also completes a creation interrupted after this commit.
        self.job_root(job).mkdir(parents=True, exist_ok=True)
        return created

    def job_root(self, job: str) -> Path:
        parent = self.workspace / "jobs"
        root = parent / identifier(job)
        if parent.is_symlink() or root.is_symlink():
            raise ValueError("runtime job directories cannot be symlinks")
        return root

    def get(self, job: str) -> dict:
        identifier(job)
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise KeyError(f"unknown workflow {job}")
            report = dict(row)
            for key in ("plan", "process_identity", "proposal"):
                report[key] = json.loads(report[key]) if report[key] else None
            report["nodes"] = []
            for row in db.execute("SELECT * FROM nodes WHERE job=? ORDER BY rowid", (job,)):
                node = dict(row)
                for key in ("definition", "parameters", "result", "evidence", "costs"):
                    node[key] = json.loads(node[key]) if node[key] else None
                report["nodes"].append(node)
            report["sequence"] = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE job=?", (job,)).fetchone()[0]
            report["events"] = [{**dict(r), "body": json.loads(r["body"])} for r in db.execute(
                "SELECT * FROM events WHERE job=? ORDER BY sequence DESC LIMIT 100", (job,))][::-1]
        report["resources"] = self.balance(report["plan"]["spec"]["limits"], report["nodes"])
        report["workspace"] = str(self.workspace)
        return report

    @staticmethod
    def balance(limits: dict, nodes: list) -> dict:
        result = {unit: {"limit": limit, "spent": 0.0, "reserved": 0.0} for unit, limit in limits.items()}
        for node in nodes:
            for unit, cost in (node.get("costs") or {}).items():
                item = result.setdefault(unit, {"limit": None, "spent": 0.0, "reserved": 0.0})
                if cost["spent"] is None:
                    item["reserved"] += cost["reserved"]
                else:
                    item["spent"] += cost["spent"]
        for value in result.values():
            value["remaining"] = None if value["limit"] is None else value["limit"] - value["spent"] - value["reserved"]
        return result

    def set_state(self, job: str, epoch: int, state: str, *, error: str | None = None) -> None:
        with self.connection(write=True) as db:
            self.owned(db, job, epoch)
            db.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=?", (state, error, time.time(), job))
            self.event(db, job, state, {"error": error})

    def heartbeat(self, job: str, epoch: int, *, elapsed: float | None = None) -> bool:
        with self.connection(write=True) as db:
            self.owned(db, job, epoch)
            db.execute("UPDATE jobs SET heartbeat=?,updated=? WHERE id=?", (time.time(), time.time(), job))
            if elapsed is not None:
                db.execute("UPDATE jobs SET elapsed=? WHERE id=?", (elapsed, job))
            return bool(db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job,)).fetchone()[0])

    def count_call(self, job: str, epoch: int) -> None:
        with self.connection(write=True) as db:
            row = self.owned(db, job, epoch)
            cap = json.loads(row["plan"])["spec"]["controller"]["max_calls"]
            if row["llm_calls"] >= cap:
                raise StateError("controller model-call allowance exhausted")
            db.execute("UPDATE jobs SET llm_calls=llm_calls+1 WHERE id=?", (job,))
            self.event(db, job, "model_call_reserved", {"call": row["llm_calls"] + 1,
                       "basis": "actual Transport.ask attempt; token price is not measured"})

    def start_node(self, job: str, epoch: int, node: dict, parameters: dict, decision: dict) -> None:
        with self.connection(write=True) as db:
            row = self.owned(db, job, epoch)
            if row["cancel_requested"]:
                raise KeyboardInterrupt("workflow cancellation requested")
            current = db.execute("SELECT state FROM nodes WHERE job=? AND id=?", (job, node["id"])).fetchone()
            if current is None or current[0] != "waiting":
                raise StateError("only an undispatched node may start")
            rows = [{"costs": json.loads(r[0])} for r in db.execute("SELECT costs FROM nodes WHERE job=?", (job,))]
            balance = self.balance(json.loads(row["plan"])["spec"]["limits"], rows)
            for unit, amount in node["resources"].items():
                if unit not in balance or amount > balance[unit]["remaining"] + 1e-12:
                    raise StateError(f"workflow resource allowance exhausted: {unit}")
            costs = {unit: {"reserved": amount, "spent": None, "basis": "reserved; outcome not reconciled"}
                     for unit, amount in node["resources"].items()}
            db.execute("UPDATE nodes SET state='running',parameters=?,costs=?,started=? WHERE job=? AND id=?",
                       (canonical(parameters), canonical(costs), time.time(), job, node["id"]))
            self.event(db, job, "dispatched", {"node_id": node["id"], "decision": decision,
                                              "parameters_hash": digest(parameters), "resources": node["resources"]})

    def receipt(self, job: str, epoch: int, node: str, key: str, body: dict) -> None:
        with self.connection(write=True) as db:
            self.owned(db, job, epoch)
            encoded, sha = canonical(body), digest(body)
            previous = db.execute("SELECT body,sha256 FROM receipts WHERE job=? AND node=? AND key=?", (job, node, key)).fetchone()
            if previous and tuple(previous) != (encoded, sha):
                raise StateError("an execution receipt is immutable")
            if not previous:
                db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)", (job, node, key, encoded, sha))
                self.event(db, job, "receipt", {"node_id": node, "key": key, "sha256": sha})

    def receipts(self, job: str, node: str) -> dict:
        with self.connection() as db:
            result = {}
            for row in db.execute("SELECT * FROM receipts WHERE job=? AND node=?", (job, node)):
                body = json.loads(row["body"])
                if digest(body) != row["sha256"]:
                    raise StateError("execution receipt digest changed")
                result[row["key"]] = body
            return result

    def finish_node(self, job: str, epoch: int, node: str, result: dict | None,
                    evidence: dict, spent: dict, *, basis: dict, state: str = "verified", error: str | None = None) -> None:
        amounts(spent)
        with self.connection(write=True) as db:
            self.owned(db, job, epoch)
            row = db.execute("SELECT costs,state FROM nodes WHERE job=? AND id=?", (job, node)).fetchone()
            if row is None or row["state"] not in {"running", "reconciliation_required"}:
                raise StateError("node is not awaiting a verified outcome")
            costs = json.loads(row["costs"])
            if set(spent) != set(costs):
                raise ValueError("settlement must account for every reserved resource in its original unit")
            for unit, amount in spent.items():
                costs[unit] = {**costs[unit], "spent": amount, "basis": basis[unit]}
            db.execute("UPDATE nodes SET state=?,result=?,evidence=?,costs=?,error=?,finished=? WHERE job=? AND id=?",
                       (state, canonical(result), canonical(evidence), canonical(costs), error, time.time(), job, node))
            self.event(db, job, "node_" + state, {"node_id": node, "evidence": evidence, "costs": costs, "error": error})

    def unresolved(self, job: str, epoch: int, node: str, error: str) -> None:
        with self.connection(write=True) as db:
            self.owned(db, job, epoch)
            db.execute("UPDATE nodes SET state='reconciliation_required',error=? WHERE job=? AND id=? AND state IN ('running','reconciliation_required')",
                       (error, job, node))
            self.event(db, job, "reconciliation_required", {"node_id": node, "error": error})
