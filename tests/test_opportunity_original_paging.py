"""Bounded original paging fixtures; no new scientific evidence or execution."""
import base64
import copy
import json
from pathlib import Path

import pytest

from etalon.active.store import StateError
from etalon.research import Artifact
from etalon.research import opportunity_state as state
from etalon.research import evidence
from etalon.research.evidence_refs import EvidenceError, file_identity
from test_opportunity_state import SID, case, close, reopen, save


def workspace(case):
    path = case["root"] / "paged-originals.json"
    path.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "paging-fixture",
        "sources": [{"source_id": SID, "kind": "journal", "location": str(case["j"].store.path),
                     "identity": {"kind": "snapshot_sha256", "value": file_identity(case["j"].store.path)["sha256"]}}],
        "status_refs": [], "location_map": []}), encoding="utf-8")
    return path


def raw_artifact(case, raw, name):
    path = case["root"] / name
    path.write_bytes(raw)
    return case["ref"](case["j"].record(Artifact(str(path), **file_identity(path),
        source_kind="fixture", role="diagnostic", bindings={})))


def large_envelope(case, *, original=None, size=70000):
    ref = case["artifact"]({"aaa_padding": "x" * size, "original_ref": original or case["problem"],
                             "title": "Fixture wrapper with reference after the first page"})
    raw = Path(case["j"].get(ref["record_id"])["path"]).read_bytes()
    assert raw.find((original or case["problem"])["record_id"].encode()) > state.ORIGINAL_PAGE_BYTES
    return ref


@pytest.mark.parametrize("external", [False, True])
def test_large_wrapper_follows_late_original_refs_and_cannot_create_novelty(case, external):
    envelope = large_envelope(case)
    ws = workspace(case) if external else None
    sid = "local-diagnostic" if external else SID
    expected = state._origins(case["j"], [case["problem"]], sid, ws)
    assert state._origins(case["j"], [envelope], sid, ws) == expected
    # Byte changes in another wrapper still retain the same actual leaf identity.
    renamed = case["artifact"]({"aaa_padding": "other prose" * 8000, "source_ref": case["problem"]})
    ws = workspace(case) if external else None
    assert state._origins(case["j"], [renamed], sid, ws) == expected
    terminal = save(case, close(case))
    with pytest.raises(EvidenceError, match="unseen original"):
        reopen(case, terminal, envelope)


def test_external_paging_preserves_reverse_counter_refs_at_end_of_large_envelope(case):
    envelope = case["artifact"]({"aaa_padding": "x" * 80000,
        "counter_refs": [case["negative"]], "support_refs": [case["problem"]]})
    ws = workspace(case)
    expected = state._origins(case["j"], [case["problem"], case["negative"]], "diagnostic", ws)
    assert state._origins(case["j"], [envelope], "diagnostic", ws) == expected


@pytest.mark.parametrize("fault", ["truncation", "identity", "content", "continuation", "total"])
def test_inconsistent_external_pages_fail_closed(case, monkeypatch, fault):
    envelope = large_envelope(case)
    ws = workspace(case)
    original = evidence.read_evidence

    def altered(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("view") == "artifact" and kwargs.get("offset", 0) >= state.ORIGINAL_PAGE_BYTES:
            result = copy.deepcopy(result)
            fragment = result["fragment"]
            chunk = base64.b64decode(fragment["content"])
            if fault == "truncation":
                fragment["content"] = base64.b64encode(chunk[:-1]).decode()
            elif fault == "identity":
                result["ref"]["sha256"] = "0" * 64
            elif fault == "content":
                fragment["content"] = base64.b64encode(b"!" + chunk[1:]).decode()
            elif fault == "continuation":
                fragment["next_offset"] = None
            else:
                fragment["total_bytes"] -= 1
        return result

    monkeypatch.setattr(evidence, "read_evidence", altered)
    with pytest.raises(EvidenceError, match="original page|original byte identity"):
        state._origins(case["j"], [envelope], "diagnostic", ws)


def test_external_original_changed_between_pages_is_rejected(case, monkeypatch):
    envelope = large_envelope(case)
    ws = workspace(case)
    path = Path(case["j"].get(envelope["record_id"])["path"])
    original = evidence.read_evidence

    def changed(*args, **kwargs):
        if kwargs.get("view") == "artifact" and kwargs.get("offset", 0) >= state.ORIGINAL_PAGE_BYTES:
            path.write_bytes(b"changed original")
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence, "read_evidence", changed)
    with pytest.raises(EvidenceError):
        state._origins(case["j"], [envelope], "diagnostic", ws)


def test_local_original_is_checked_before_and_after_read(case, monkeypatch):
    envelope = large_envelope(case)
    path = Path(case["j"].get(envelope["record_id"])["path"])
    original = case["j"].artifact_path
    calls = []

    def changed(artifact):
        calls.append(artifact.path)
        if len(calls) == 2:
            path.write_bytes(b"changed between local checks")
        return original(artifact)

    monkeypatch.setattr(case["j"], "artifact_path", changed)
    with pytest.raises(StateError, match="hash/size"):
        state._origins(case["j"], [envelope], SID)
    assert len(calls) == 2


@pytest.mark.parametrize("external", [False, True])
def test_malformed_large_json_cannot_be_reclassified_as_a_new_binary_leaf(case, external):
    raw = b'{"aaa_padding":"' + b"x" * 70000 + b'", "original_ref":'
    ref = raw_artifact(case, raw, "truncated-json-original.bin")
    with pytest.raises(EvidenceError, match="malformed or truncated"):
        state._origins(case["j"], [ref], "diagnostic" if external else SID,
                       workspace(case) if external else None)


@pytest.mark.parametrize("external", [False, True])
def test_per_original_size_bound_is_explicit(case, external):
    oversized = large_envelope(case, size=state.MAX_ORIGINAL_BYTES)
    with pytest.raises(EvidenceError, match="bounded 1 MiB"):
        state._origins(case["j"], [oversized], "diagnostic" if external else SID,
                       workspace(case) if external else None)


def test_closure_total_has_a_separate_bound_without_enlarging_global_views(case, monkeypatch):
    # Small test-only budget exercises the same aggregate accounting as 8 MiB.
    monkeypatch.setattr(state, "MAX_ORIGINAL_CLOSURE_BYTES", 100000)
    first = large_envelope(case, size=60000)
    second = large_envelope(case, size=60001)
    with pytest.raises(EvidenceError, match="closure exceeds bounded 8 MiB"):
        state._origins(case["j"], [first, second], SID)
    assert evidence.MAX_READ_BYTES == 65536
    assert state.MAX_ORIGINALS == 256


@pytest.mark.parametrize("external", [False, True])
def test_large_non_json_binary_keeps_verified_leaf_identity(case, external):
    ref = raw_artifact(case, b"binary fixture\x00\xff" * 5000, "binary-original.bin")
    original = case["j"].get(ref["record_id"])
    assert state._origins(case["j"], [ref], "diagnostic" if external else SID,
                          workspace(case) if external else None) == [
        {key: original[key] for key in ("sha256", "bytes")}]


def test_state_content_limit_did_not_expand_with_original_paging(case):
    payload = close(case)
    payload["reason"] = "x" * 17000
    with pytest.raises(EvidenceError, match="16 KiB"):
        state.validate_content(payload)
