"""Readiness must report broken installations and fail machine-readable CLI checks."""

from __future__ import annotations

import json

from etalon.__main__ import main
from etalon.doctor import _package, diagnose


def test_dependency_version_failure_is_explained(monkeypatch):
    monkeypatch.setattr("etalon.doctor.importlib.metadata.version", lambda _: "1.10.8")
    assert _package("pydantic", "pydantic", (2, 10), (3,)) == {
        "ready": False, "version": "1.10.8", "problem": "unsupported dependency version"}


def test_installed_but_broken_dependency_is_not_ready(monkeypatch):
    monkeypatch.setattr("etalon.doctor.importlib.metadata.version", lambda _: "2.13.5")

    def fail(name):
        raise ImportError("a shared library is absent")

    monkeypatch.setattr("etalon.doctor.importlib.import_module", fail)
    result = _package("pydantic", "pydantic", (2, 10), (3,))
    assert result["ready"] is False and "shared library" in result["problem"]


def test_doctor_nonzero_exit_depends_on_requested_capability(monkeypatch, capsys):
    monkeypatch.setattr("etalon.doctor.diagnose", lambda **kwargs: {
        "readiness": {"cascade": False, "active": True}})
    assert main(["doctor", "--require", "cascade"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["doctor", "--require", "active"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_infra_json_exit_code_does_not_hide_a_broken_install(monkeypatch, capsys):
    monkeypatch.setattr("etalon.boundary.infra.describe", lambda: {"molcascade": {"pinned": False}})
    assert main(["infra", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["molcascade"]["pinned"] is False


def test_numeric_learning_does_not_require_the_cascade_numpy_floor(monkeypatch):
    monkeypatch.setattr("etalon.doctor._package", lambda name, module, lower, upper: {
        "ready": name != "numpy" or lower <= (1, 25)})
    monkeypatch.setattr("etalon.boundary.infra.describe", lambda: {
        "molcascade": {"pinned": True}, "prism": {"pinned": True}})
    report = diagnose()
    assert report["readiness"]["active"] is True
    assert report["readiness"]["cascade"] is False
