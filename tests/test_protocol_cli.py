"""Read-only protocol inspection and an explicitly synthetic, workspace-free benchmark."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from etalon.__main__ import main
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active import cli as active_cli
from etalon.boundary.screen import Screen


def make_store(tmp_path):
    store = CampaignStore(tmp_path / "existing.sqlite")
    endpoint = Endpoint("reference", "target", "score", "arbitrary", "test-only/1", 1,
                        requires_handoff=False)
    store.configure(CampaignSpec("reference", 12, "test_units", "test-vector/1"), [endpoint])
    return store


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_searches_lists_an_empty_registry_without_changing_journal_bytes(tmp_path, capsys):
    store = make_store(tmp_path)
    before = file_hash(store.path)
    files = set(tmp_path.iterdir())
    assert main(["active", "searches", "--database", str(store.path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"searches": [], "plans": []}
    assert file_hash(store.path) == before
    assert set(tmp_path.iterdir()) == files


def test_searches_never_creates_a_missing_database_or_output(tmp_path, capsys):
    database = tmp_path / "does-not-exist" / "campaign.sqlite"
    output = tmp_path / "missing-query.json"
    assert main(["active", "searches", "--database", str(database), "--output", str(output)]) == 2
    response = json.loads(capsys.readouterr().out)
    assert not response["ok"] and "FileNotFoundError" in response["error"]
    assert not database.exists() and not database.parent.exists() and not output.exists()


def test_searches_writes_new_json_exclusively_but_keeps_the_database_read_only(tmp_path, capsys):
    store = make_store(tmp_path)
    before = file_hash(store.path)
    output = tmp_path / "reports" / "searches.json"
    assert main(["active", "searches", "--database", str(store.path), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {"searches": [], "plans": []}
    assert json.loads(capsys.readouterr().out)["output"] == str(output.resolve())
    assert file_hash(store.path) == before


@pytest.mark.parametrize("command", ["searches", "protocol-benchmark", "protocol-stopping-benchmark"])
def test_existing_output_is_preserved_and_refused_before_command_work(
        tmp_path, capsys, monkeypatch, command):
    output = tmp_path / "existing-report.json"
    content = '{"existing_evidence":"must not be overwritten"}\n'
    output.write_text(content, encoding="utf-8")
    before = file_hash(output)
    arguments = ["active", command, "--output", str(output)]
    if command == "searches":
        arguments.extend(["--database", str(tmp_path / "absent.sqlite")])

    def forbidden_command(_arguments):
        pytest.fail("output collision must be checked before command inspection or benchmark execution")

    monkeypatch.setattr(active_cli, "_run", forbidden_command)
    assert main(arguments) == 2
    assert "overwrite" in json.loads(capsys.readouterr().out)["error"]
    assert output.read_text(encoding="utf-8") == content and file_hash(output) == before
    assert not (tmp_path / "absent.sqlite").exists()


def test_protocol_benchmark_runs_forty_bounded_synthetic_trials_without_workspace_or_scientific_tools(
        tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def no_scientific_execution(*_args, **_kwargs):
        pytest.fail("the synthetic protocol benchmark must not invoke scientific tools")

    def no_database(*_args, **_kwargs):
        pytest.fail("the workspace-free synthetic benchmark must not open or create a database")

    monkeypatch.setattr(Screen, "run", no_scientific_execution)
    monkeypatch.setattr(Screen, "plan", no_scientific_execution)
    monkeypatch.setattr(sqlite3, "connect", no_database)
    output = tmp_path / "benchmark.json"
    assert main(["active", "protocol-benchmark", "--seeds", "0,1", "--budget", "2",
                 "--max-trials", "2", "--output", str(output)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["output"] == str(output.resolve())
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["synthetic"] is True and report["seeds"] == [0, 1]
    assert report["budget"] == report["max_trials"] == 2
    assert len(report["runs"]) == 40
    assert len({(run["seed"], run["scenario"], run["group"]) for run in report["runs"]}) == 40
    for run in report["runs"]:
        assert 0 <= run["spent"] <= run["budget"] == 2
        assert run["remaining"] == run["budget"] - run["spent"]
        assert run["queries"] == len(run["observed"]) == len(run["trace"]) == 2
        assert len({row["id"] for row in run["observed"]}) == 2
        assert all(row["spent"] <= run["budget"] for row in run["trace"])
    assert "no real protocol execution" in report["claim"]
    assert set(tmp_path.iterdir()) == {output}


@pytest.mark.parametrize("invalid", [
    ["--seeds", "0,0"], ["--seeds", "not-a-seed"], ["--budget", "0"], ["--max-trials", "0"],
])
def test_protocol_benchmark_invalid_controls_return_two_without_an_output(tmp_path, capsys, invalid):
    output = tmp_path / "invalid-benchmark.json"
    assert main(["active", "protocol-benchmark", *invalid, "--output", str(output)]) == 2
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False and "ValueError" in response["error"]
    assert not output.exists() and not list(tmp_path.iterdir())


def test_stopping_benchmark_cli_has_no_database_or_scientific_side_effects(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def forbidden(*_args, **_kwargs):
        pytest.fail("synthetic stopping benchmark cannot open a database or launch science")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(Screen, "run", forbidden)
    monkeypatch.setattr(Screen, "plan", forbidden)
    output = tmp_path / "stopping.json"
    assert main(["active", "protocol-stopping-benchmark", "--seeds", "0,1", "--budget", "2",
                 "--max-trials", "2", "--opportunity-costs", "0.1,0.3", "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == str(output.resolve())
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["synthetic"] is True and report["opportunity_costs"] == [0.1, 0.3]
    assert len(report["runs"]) == 80
    assert all(0 <= row["spent"] <= 2 for row in report["runs"])
    assert set(tmp_path.iterdir()) == {output}


@pytest.mark.parametrize("invalid", [
    ["--seeds", "0,0"], ["--seeds", "bad"], ["--budget", "0"], ["--max-trials", "0"],
    ["--opportunity-costs", "0"], ["--opportunity-costs", "nan"],
    ["--opportunity-costs", "bad"], ["--opportunity-costs", "0.1,0.1"],
])
def test_stopping_cli_rejects_bad_controls_without_output(tmp_path, capsys, invalid):
    output = tmp_path / "invalid.json"
    assert main(["active", "protocol-stopping-benchmark", *invalid, "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not output.exists() and not list(tmp_path.iterdir())
