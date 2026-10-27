"""Portable installation and real stdio MCP hook protocol contracts."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

from etalon import host, research_hooks
from etalon import host_install
from test_research_graph_memory import graph_case


def test_doctor_supports_export_without_optional_research_hooks(tmp_path):
    project = tmp_path / "plain-host"
    host.export(project)
    result = host.doctor(project)
    assert result["configuration_verified"]
    assert result["configured_events"] == []
    assert not result["entry_exists_on_server"]
    assert result["verification_stages"]["static_installation"] == "verified"
    assert result["verification_stages"]["login"] == "not_checked"
    assert result["verification_stages"]["mcp_reachability"] == "not_checked"
    assert not result["acceptance_complete"]


@pytest.mark.parametrize("consumer", ["codex", "claude"])
def test_move_rebind_preserves_sources_and_same_hook_definition(tmp_path, consumer):
    entry = tmp_path / "entry.json"
    entry.write_text("{}", encoding="utf-8")
    before = tmp_path / "original"
    report = host.install(before, research_entry=entry, host=consumer)
    assert report["hook_transport"] == "mcp_tool"
    name = ".codex/hooks.json" if consumer == "codex" else ".claude/settings.json"
    hook_bytes = (before / name).read_bytes()
    if consumer == "codex":
        assert str(tmp_path).encode() not in hook_bytes
    for event, groups in json.loads(hook_bytes)["hooks"].items():
        expected = "command" if consumer == "claude" and event == "SessionStart" else "mcp_tool"
        assert all(handler["type"] == expected for group in groups for handler in group["hooks"])
    after = tmp_path / "moved project"
    shutil.copytree(before, after)
    next_entry = tmp_path / "next.json"
    next_entry.write_text("{}", encoding="utf-8")
    rebound = host.install(after, research_entry=next_entry, host=consumer)
    assert rebound["status"] == "rebound"
    if consumer == "codex":
        assert (after / name).read_bytes() == hook_bytes
    else:
        startup = json.loads((after / name).read_bytes())["hooks"]["SessionStart"][0]["hooks"][0]
        assert startup["args"] == [str(after / ".etalon/research-hook.py")]
    binding = json.loads((after / ".etalon/research-hook-binding.json").read_bytes())
    assert binding["entry"] == str(next_entry)
    assert Path(binding["state_dir"]).is_relative_to(after)
    assert host.verify(before)["export_matches_manifest"]
    assert host.verify(after)["runtime_matches"]
    assert not host.install(after, research_entry=next_entry, host=consumer)["changed"]
    assert host.doctor(after)["native_delivery"] == "not_verified"
    assert ("PostToolUseFailure" in host.doctor(after)["configured_events"]) == (consumer == "claude")
    assert Path(rebound["operation"], "receipt.json").is_file()


def test_claude_startup_delivers_context_without_mcp_client(graph_case, tmp_path):
    project = tmp_path / "claude-startup"
    host.install(project, research_entry=graph_case["entry"], host="claude")
    handler = json.loads((project / ".claude/settings.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]
    env = {k: v for k, v in os.environ.items() if k != "ETALON_RESEARCH_HOOK_BINDING"}
    result = subprocess.run([handler["command"], *handler["args"]],
        input=json.dumps({"hook_event_name": "SessionStart", "session_id": "startup-without-mcp",
                          "source": "startup"}).encode(), capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    payload = json.loads(context.split("\n", 1)[1])
    assert payload["delivery"]["mode"] == "baseline"
    assert "resume_frontier" in payload["context"]


def test_install_preserves_user_changes_and_arbitrary_project(tmp_path):
    entry = tmp_path / "entry.json"
    entry.write_text("{}")
    project = tmp_path / "user-project"
    project.mkdir()
    (project / "AGENTS.md").write_text("user instructions")
    with pytest.raises(ValueError, match="not an ETALON export"):
        host.install(project, research_entry=entry)
    assert (project / "AGENTS.md").read_text() == "user instructions"
    exported = tmp_path / "export"
    host.install(exported, research_entry=entry)
    (exported / "AGENTS.md").write_text("modified guidance")
    with pytest.raises(ValueError, match="modified export member"):
        host.install(exported, research_entry=entry)
    assert (exported / "AGENTS.md").read_text() == "modified guidance"


def test_rebind_can_add_skills_without_replacing_the_project(tmp_path):
    entry = tmp_path / "entry.json"
    entry.write_text("{}")
    project = tmp_path / "claude"
    host.install(project, research_entry=entry, host="claude", selected_skills=["research"])
    settings = (project / ".claude/settings.json").read_bytes()
    sentinel = project / ".etalon/research-hook-receipts/session-existing.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"existing": true}')
    upgraded = host.install(project, research_entry=entry, host="claude",
                            selected_skills=["research", "method-configuration"])
    assert upgraded["changed"] and host.doctor(project)["configuration_verified"]
    assert (project / ".claude/skills/etalon-method-configuration/SKILL.md").is_file()
    assert (project / ".claude/settings.json").read_bytes() == settings
    assert sentinel.read_text() == '{"existing": true}'
    assert not host.install(project, research_entry=entry, host="claude")["changed"]
    with pytest.raises(ValueError, match="cannot remove"):
        host.install(project, research_entry=entry, host="claude", selected_skills=["research"])


def test_rebind_resumes_interrupted_owned_file_replacement(tmp_path, monkeypatch):
    entry = tmp_path / "first.json"
    entry.write_text("{}")
    project = tmp_path / "host"
    host.install(project, research_entry=entry)
    next_entry = tmp_path / "next.json"
    next_entry.write_text("{}")
    original = host_install._write
    def interrupted(path, data):
        original(path, data)
        if path == project / ".etalon/research-hook-binding.json":
            raise RuntimeError("after binding replacement")
    monkeypatch.setattr(host_install, "_write", interrupted)
    with pytest.raises(RuntimeError):
        host.install(project, research_entry=next_entry)
    monkeypatch.setattr(host_install, "_write", original)
    assert host.install(project, research_entry=next_entry)["status"] == "rebound"
    assert host.verify(project)["export_matches_manifest"]


@pytest.mark.parametrize("boundary", ["research-hook-binding.json", host.MANIFEST, "receipt.json"])
def test_initial_install_resumes_each_publication_boundary(tmp_path, monkeypatch, boundary):
    entry = tmp_path / "entry.json"
    entry.write_text("{}")
    project = tmp_path / "initial-host"
    original = host_install._write
    failed = False
    def interrupted(path, data):
        nonlocal failed
        if path.name == boundary == "receipt.json" and not failed:
            failed = True
            raise RuntimeError("initial install interruption")
        original(path, data)
        if path.name == boundary and not failed:
            failed = True
            raise RuntimeError("initial install interruption")
    monkeypatch.setattr(host_install, "_write", interrupted)
    with pytest.raises(RuntimeError, match="initial install interruption"):
        host.install(project, research_entry=entry)
    assert failed
    intent_files = list(tmp_path.glob(".etalon-install-*/.etalon/installations/*/intent.json"))
    intent_files += list(project.glob(".etalon/installations/*/intent.json"))
    assert len(intent_files) == 1
    identity = host_install._identity(host_install._read(intent_files[0]))
    monkeypatch.setattr(host_install, "_write", original)
    report = host.install(project, research_entry=entry)
    assert report["status"] in {"installed", "recovered"}
    archive = Path(report["operation"])
    assert host_install._identity(host_install._read(archive / "intent.json")) == identity
    assert host_install._io(archive / "receipt.json").is_file()
    assert host.verify(project)["export_matches_manifest"]
    assert host.install(project, research_entry=entry)["status"] == "already_installed"


@pytest.mark.parametrize("mutate_member", [False, True])
def test_interrupted_initial_install_rejects_changed_request_or_owned_bytes(tmp_path, monkeypatch, mutate_member):
    first = tmp_path / "first.json"
    first.write_text("{}")
    project = tmp_path / "initial-host"
    original = host_install._write
    def interrupted(path, data):
        original(path, data)
        if path.name == "research-hook-binding.json":
            raise RuntimeError("initial install interruption")
    monkeypatch.setattr(host_install, "_write", interrupted)
    with pytest.raises(RuntimeError):
        host.install(project, research_entry=first)
    monkeypatch.setattr(host_install, "_write", original)
    stage = next(path for path in tmp_path.glob(".etalon-install-*") if path.is_dir())
    old_intent = host_install._read(next(stage.glob(".etalon/installations/*/intent.json")))
    second = tmp_path / "second.json"
    second.write_text("{}")
    if mutate_member:
        (stage / "AGENTS.md").write_text("user modification must survive")
    with pytest.raises(ValueError, match="modified staging|does not match"):
        host.install(project, research_entry=first if mutate_member else second)
    assert not project.exists()
    assert host_install._read(next(stage.glob(".etalon/installations/*/intent.json"))) == old_intent
    if mutate_member:
        assert (stage / "AGENTS.md").read_text() == "user modification must survive"


def test_rebind_manifest_committed_before_receipt_is_explicitly_completed(tmp_path, monkeypatch):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text("{}")
    second.write_text("{}")
    project = tmp_path / "host"
    host.install(project, research_entry=first)
    original = host_install._write
    def interrupted(path, data):
        if path.name == "receipt.json":
            raise RuntimeError("after manifest before receipt")
        original(path, data)
    monkeypatch.setattr(host_install, "_write", interrupted)
    with pytest.raises(RuntimeError):
        host.install(project, research_entry=second)
    monkeypatch.setattr(host_install, "_write", original)
    assert host.verify(project)["export_matches_manifest"]
    pending = [path for path in (project / ".etalon/installations").glob("*")
               if (path / "intent.json").is_file() and not (path / "receipt.json").exists()]
    assert len(pending) == 1
    intent_before = host_install._read(pending[0] / "intent.json")
    with pytest.raises(ValueError, match="unfinished installation does not match"):
        host.install(project, research_entry=first)
    report = host.install(project, research_entry=second)
    assert report["status"] == "recovered" and Path(report["operation"]) == pending[0]
    assert host_install._read(pending[0] / "intent.json") == intent_before
    assert host_install._io(pending[0] / "receipt.json").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_rebind_backups_support_long_windows_paths(tmp_path):
    entry = tmp_path / "entry.json"
    entry.write_text("{}")
    project = tmp_path / "a-moderately-long-adapter-project-name"
    host.install(project, research_entry=entry)
    new_entry = tmp_path / "new-entry.json"
    new_entry.write_text("{}")
    report = host.install(project, research_entry=new_entry)
    archive = Path(report["operation"])
    backups = list(host_install._io(archive / "before").rglob("*"))
    assert any(path.is_file() and len(str(path)) > 260 for path in backups)
    assert host_install._read(archive / "before" / host.MANIFEST)
    assert host.verify(project)["export_matches_manifest"]


def test_hook_binding_is_server_owned_and_errors_are_protocol_errors(tmp_path, monkeypatch):
    from etalon.mcp import host as tools
    registered = {}
    class Server:
        def tool(self):
            def register(function):
                registered[function.__name__] = function
                return function
            return register
    tools.register(Server())
    monkeypatch.delenv("ETALON_RESEARCH_HOOK_BINDING", raising=False)
    result = registered["etalon_research_hook"]({"hook_event_name": "SessionStart", "session_id": "test"})
    assert result.isError and "hookSpecificOutput" not in result.content[0].text


@pytest.mark.parametrize("consumer", ["codex", "claude"])
def test_native_mcp_tool_result_baseline_compact_resume_and_idempotency(graph_case, tmp_path, consumer):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    project = tmp_path / "native-mcp"
    host.install(project, host=consumer, research_entry=graph_case["entry"])
    if consumer == "codex":
        config = tomllib.loads((project / ".codex/config.toml").read_text())["mcp_servers"]["etalon"]
    else:
        config = json.loads((project / ".mcp.json").read_text())["mcpServers"]["etalon"]
    async def exercise():
        params = StdioServerParameters(command=config["command"], args=config["args"],
                                       env=config["env"], cwd=str(tmp_path))
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            event = {"hook_event_name": "PreToolUse", "session_id": "isolated-native-test",
                     "tool_name": "mcp__etalon__etalon_research_context", "tool_use_id": "call-1",
                     "tool_input": {"entry": str(graph_case["entry"])}}
            first = await session.call_tool("etalon_research_hook", {"event": event})
            assert not first.isError
            out = json.loads(first.content[0].text)
            payload = json.loads(out["hookSpecificOutput"]["additionalContext"].split("\n", 1)[1])
            assert payload["delivery"]["mode"] == "baseline"
            assert "resume_frontier" in payload["context"]
            repeated = await session.call_tool("etalon_research_hook", {"event": event})
            assert repeated.content[0].text == first.content[0].text
            compact = await session.call_tool("etalon_research_hook", {"event": {
                "hook_event_name": "PreCompact", "session_id": event["session_id"], "event_id": "compact-1"}})
            assert not compact.isError and json.loads(compact.content[0].text) == {}
            after = await session.call_tool("etalon_research_hook", {"event": {
                "hook_event_name": "SessionStart", "session_id": event["session_id"], "source": "compact"}})
            assert not after.isError
            payload = json.loads(json.loads(after.content[0].text)["hookSpecificOutput"]["additionalContext"].split("\n", 1)[1])
            assert payload["delivery"]["mode"] == "baseline"
            assert payload["context"]["continuation"]["authority_state"] == "paused"
            bad = await session.call_tool("etalon_research_hook", {"event": {"hook_event_name": "Bogus",
                                                                          "session_id": event["session_id"]}})
            assert bad.isError
            # A real MCP schema rejection, then a supplied host-shaped event.
            # This tests transport/receipt continuity, not native host dispatch.
            failed_call = await session.call_tool("etalon_evidence_read", {"limit": 0})
            assert failed_call.isError
            failure_event = {"hook_event_name": "PostToolUse" if consumer == "codex" else "PostToolUseFailure",
                "session_id": event["session_id"], "event_id": "real-mcp-rejection",
                "tool_name": "mcp__etalon__etalon_evidence_read", "tool_use_id": "rejected-call",
                "tool_input": {"limit": 0}, "tool_response": failed_call.model_dump(mode="json")}
            observed = await session.call_tool("etalon_research_hook", {"event": failure_event})
            repeated = await session.call_tool("etalon_research_hook", {"event": failure_event})
            assert not observed.isError and observed.content[0].text == repeated.content[0].text == "{}"
            binding = json.loads((project / ".etalon/research-hook-binding.json").read_bytes())
            receipt_root = research_hooks._receipt_io_path(Path(binding["state_dir"]))
            receipts = [json.loads(path.read_bytes()) for path in receipt_root.rglob("*.json")]
            failures = [value for value in receipts if value["event_identity"]["value"] == "real-mcp-rejection"]
            assert len(failures) == 1
            failure = failures[0]
            assert failure["tool_observation"]["observation"] == "failed"
            assert "mcp_is_error" in failure["tool_observation"]["failure_signals"]
            assert failure["tool_observation"]["response_sha256"] == research_hooks._digest(failure_event["tool_response"])
            assert failure["scientific_writes"] == 0 and not failure["automatic_retry"]
    asyncio.run(asyncio.wait_for(exercise(), timeout=90))


def test_mcp_handler_does_not_recurse(tmp_path):
    result = research_hooks.handle_event({}, {"hook_event_name": "PreToolUse", "session_id": "own",
        "tool_name": "mcp__etalon__etalon_research_hook"}, host="codex", state_dir=tmp_path)
    assert result["ignored"] == "own_hook_handler" and result["host_output"] == {}
