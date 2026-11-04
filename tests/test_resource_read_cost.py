"""Avoid unrelated code reads without reusing a stale verification verdict."""
from etalon.research import read_scope


def test_ordinary_read_scope_does_not_fingerprint_unused_projection_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(read_scope, "implementation_identity", lambda: calls.append("read") or "v1")
    with read_scope.verified_read():
        assert calls == []
    assert calls == []


def test_cache_export_and_new_import_still_recheck_implementation(monkeypatch):
    calls = []
    version = ["v1"]
    monkeypatch.setattr(read_scope, "implementation_identity", lambda: calls.append(version[0]) or version[0])
    first = read_scope.ReadScope()
    saved = first.snapshot()
    saved["sources"] = {"example": "cached projection"}
    assert calls == ["v1"]
    unchanged = read_scope.ReadScope(saved)
    assert unchanged.previous == saved["sources"]
    version[0] = "v2"
    changed = read_scope.ReadScope(saved)
    assert changed.previous == {}
    assert calls == ["v1", "v1", "v2"]
