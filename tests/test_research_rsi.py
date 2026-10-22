"""Frozen-view counterexamples and scoped revision decisions, no science runs."""
import json
import sqlite3
import pytest

from etalon.research import MethodEvaluation, TaskSplit
from etalon.research import continuation, evidence
from etalon.research.components import record_json
from etalon.research.evidence_refs import EvidenceError
from etalon.research.evidence_refs import file_identity, make_ref
from etalon.research.method_revision import resolve_method_revision
from test_method_revision import case as revision_case, save as save_revision
from test_research_continuation import case, save, step, checkpoint, publish


def test_frozen_view_reduces_actual_source_reads_with_identical_context(case, monkeypatch):
    cp = save(case, checkpoint(case, save(case, step(case[1]))))
    ws = publish(case)
    original, calls = evidence._source, []
    def traced(*args):
        calls.append(args[1]["source_id"])
        return original(*args)
    monkeypatch.setattr(evidence, "_source", traced)
    old = continuation.resume_research(ws, cp)
    assert len(calls) == 2
    calls.clear()
    assert continuation.resume_research(ws, cp, read_mode="frozen_view") == old
    assert len(calls) == 1
    case[2].write_text("Changed pause authority")
    assert continuation.resume_research(ws, cp, read_mode="frozen_view")["authority"]["state"] == "review_required"


@pytest.mark.parametrize("damage", ["source", "projected", "wal"])
def test_mid_read_change_cannot_return_clean_frozen_context(case, monkeypatch, damage):
    cp = save(case, checkpoint(case, save(case, step(case[1]))))
    ws = publish(case)
    original = continuation._check_frozen_view
    def changed(*args):
        if damage == "source":
            with sqlite3.connect(case[0].store.path) as db:
                db.execute("CREATE TABLE counterexample (n INTEGER)")
        elif damage == "projected":
            record = case[0].get(cp["record_id"])
            from pathlib import Path
            path = Path(record["path"])
            path.write_bytes(path.read_bytes() + b" ")
        else:
            from pathlib import Path
            Path(str(case[0].store.path) + "-wal").write_bytes(b"nonempty-wal")
        return original(*args)
    monkeypatch.setattr(continuation, "_check_frozen_view", changed)
    ctx = continuation.resume_research(ws, cp, read_mode="frozen_view")
    assert ctx["review_required"] and {"reason": "frozen_view_changed_during_read"} in ctx["issues"]


def test_live_source_uses_original_path_and_no_cross_call_cache(case, monkeypatch):
    cp = save(case, checkpoint(case, save(case, step(case[1]))))
    ws = publish(case)
    with sqlite3.connect(case[0].store.path) as db:
        key, value = db.execute("SELECT key,body FROM metadata ORDER BY key LIMIT 1").fetchone()
    document = json.loads(ws.read_text())
    document["sources"][0]["identity"] = {"kind": "journal_identity", "key": key, "value": json.loads(value)}
    ws.write_text(json.dumps(document))
    monkeypatch.setattr(continuation, "_check_frozen_view", lambda *a: pytest.fail("live view reused"))
    assert continuation.resume_research(ws, cp, read_mode="frozen_view") == continuation.resume_research(ws, cp)


@pytest.mark.parametrize("closed", [True, False])
def test_published_member_hash_and_unclosed_scope_fallback(case, monkeypatch, closed):
    root = case[3]
    payload = root / "member.txt"
    payload.write_text("released member")
    body = {"members": [{"member": "payload", "path": str(payload), **file_identity(payload), "visibility": "released"}]}
    foreign = {**make_ref("data", "bundle-1", body), "member": "payload"}
    manifest = root / "data.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": "bundle-1", "record_type": "data_snapshot", "record": body, "visibility": "released"}]}))
    source = {"source_id": "data", "kind": "artifact_manifest", "location": str(manifest),
              "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}}
    def workspace():
        ws = publish(case)
        config = json.loads(ws.read_text())
        config["sources"].append(source)
        ws.write_text(json.dumps(config))
        return ws
    s = save(case, step(foreign))
    marks = continuation.capture_watermarks(workspace(), source_ids=["data"]) if closed else []
    cp = save(case, checkpoint(case, s, source_watermarks=marks))
    ws = workspace()
    if not closed:
        monkeypatch.setattr(continuation, "_check_frozen_view", lambda *a: pytest.fail("unclosed source set reused"))
    assert continuation.resume_research(ws, cp, read_mode="frozen_view") == continuation.resume_research(ws, cp)
    payload.write_text("damaged member")
    ctx = continuation.resume_research(ws, cp, read_mode="frozen_view")
    assert ctx["review_required"] and any(i["reason"] == "member_identity_mismatch" for i in ctx["issues"])


def evaluation(case, *, measured=True):
    j, p, directory, ref, _ = case
    data = record_json(j, {"actual_fixture_comparison": True}, directory, source_kind="fixture", bindings={"kind": "accounting"})
    split = j.record(TaskSplit(("fixture",), ("unrun-validation",), ("unrun-transfer",),
        {"fixture": "fixed", "unrun-validation": "not-released", "unrun-transfer": "not-released"}, "test decisions only"))
    return ref(j.record(MethodEvaluation(tuple(r["record_id"] for r in p["candidate_version_refs"]),
        (p["base_method_ref"]["record_id"],), data, split, data, (data,) if measured else (),
        (data,) if measured else (), "diagnostic", "Fixture measured success", "fixture only")))


def resolve(case, **kw):
    j, p, directory, ref, _ = case
    checked = kw.pop("evaluation_ref") if "evaluation_ref" in kw else evaluation(case)
    return resolve_method_revision(j, ref(save_revision(case)), checked,
        directory=directory, source_id="fixture", source_kind="fixture", task_id="next-use",
        question="Which exact method should the next call use?", scope="fixture/local-next-use", reason="Fixture evidence", **kw)


def test_adopt_retain_and_rollback_append_uses_without_changing_prior(revision_case):
    j, p, _, _, _ = revision_case
    candidate = p["candidate_version_refs"][0]
    adopted = resolve(revision_case, outcome="adopt", candidate_ref=candidate)
    before = j.store.events()
    assert resolve(revision_case, outcome="adopt", candidate_ref=candidate) == adopted
    assert j.store.events() == before
    retained = resolve(revision_case, outcome="retain")
    rolled = resolve(revision_case, outcome="rollback", prior_use_ref=adopted["use_ref"], counterevidence_refs=p["problem_refs"])
    assert retained["selected_method_ref"] == rolled["selected_method_ref"] == p["base_method_ref"]
    assert j.get(adopted["use_ref"]["record_id"])["selected_methods"][0]["method_version_ref"] == candidate["record_id"]
    assert rolled["default_changed"] is False and j.store.actions() == []


def test_unevaluated_candidate_and_unattributed_rollback_are_rejected(revision_case):
    p = revision_case[1]
    with pytest.raises(ValueError, match="measured"):
        resolve(revision_case, outcome="adopt", candidate_ref=p["candidate_version_refs"][0], evaluation_ref=evaluation(revision_case, measured=False))
    with pytest.raises(ValueError, match="rollback needs"):
        resolve(revision_case, outcome="rollback")
    with pytest.raises(ValueError, match="exact revision candidate"):
        resolve(revision_case, outcome="adopt", candidate_ref=p["producer_method_ref"])
