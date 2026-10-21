"""MCP boundary checks for independent, read-only functional operations."""
import json
import sqlite3
from pathlib import Path

from etalon.mcp.server import register_tools


class Collector:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def collect(function):
            self.tools[function.__name__] = function
            return function
        return collect


def tools():
    collected = Collector()
    register_tools(collected)
    return collected.tools


def test_missing_monitoring_inputs_do_not_create_state(tmp_path):
    api = tools()
    before = list(tmp_path.iterdir())
    for name, args in (
        ("etalon_sweep_status", {
            "workspace": str(tmp_path / "absent-workspace"),
            "pool": str(tmp_path / "absent-pool.sqlite"),
            "ledger": str(tmp_path / "absent-ledger.jsonl"),
        }),
        ("etalon_sweep_progress", {
            "workspace": str(tmp_path / "absent-workspace"), "run_id": "unseen",
        }),
        ("etalon_calibrate_gates", {
            "workspace": str(tmp_path / "absent-workspace"), "run_id": "unseen",
            "panel_json": '[{"parent_id":"fixture","known_active":true}]',
        }),
        ("etalon_authorize_gates", {
            "workspace": str(tmp_path / "absent-workspace"), "run_id": "unseen",
            "panel_json": '[{"parent_id":"fixture","known_active":true}]',
        }),
    ):
        result = json.loads(api[name](**args))
        assert result["ok"] is False
        assert result["error"]["code"] == "FileNotFound"
        assert list(tmp_path.iterdir()) == before


def test_monitoring_old_pool_neither_migrates_nor_requires_workspace_or_ledger(tmp_path):
    pool = tmp_path / "old.sqlite"
    with sqlite3.connect(pool) as db:
        db.executescript("""
            CREATE TABLE molecule(key TEXT, smiles TEXT, source TEXT, batch TEXT);
            CREATE TABLE batch(batch_id TEXT, size INTEGER, emitted_at TEXT,
                claimed_by TEXT, claimed_at TEXT, run_id TEXT, revision_id TEXT,
                outcome TEXT, recorded_at TEXT);
            INSERT INTO molecule VALUES ('one','CCO','fixture','old');
            INSERT INTO batch VALUES ('old',1,'then','worker','then','run','rev',NULL,NULL);
        """)
    original = pool.read_bytes()
    result = json.loads(tools()["etalon_sweep_status"](
        workspace=str(tmp_path / "unused-workspace"), pool=str(pool),
        ledger=str(tmp_path / "unused-ledger"),
    ))
    assert result["ok"] is True
    assert result["sweep"]["batches"]["outstanding"] == 1
    assert result["sweep"]["etalon_revisions"] == []
    assert pool.read_bytes() == original
    assert list(tmp_path.iterdir()) == [pool]


def test_productivity_can_be_queried_without_a_plan_or_skill(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = json.loads(tools()["etalon_generation_productivity"](
        loops_json=json.dumps([{
            "tag": "already-measured", "model": "engineering-fixture", "pocket": "box",
            "chunks": [{"requested": 100, "delivered": 90, "unique": 0, "seconds": 2}] * 3,
        }]),
    ))
    assert result["ok"] is True
    assert "already-measured" in result["retire"]
    assert list(tmp_path.iterdir()) == []


def test_productivity_in_cold_stdio_server_returns_and_session_stays_usable(tmp_path):
    """A fresh real transport catches cold imports that an already-warm pytest process hides."""
    import asyncio
    from datetime import timedelta
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", (
                "import sys; from etalon.mcp.server import main; "
                "assert 'numpy' not in sys.modules and 'scipy' not in sys.modules; main()"
            )],
            cwd=str(tmp_path),
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                 "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=20)
            ) as session:
                await session.initialize()
                response = await session.call_tool("etalon_generation_productivity", {
                    "loops_json": json.dumps([{
                        "tag": "cold", "model": "fixture", "pocket": "fixture",
                        "chunks": [
                            {"requested": 100, "delivered": 100, "unique": 0, "seconds": 60}
                        ] * 3,
                    }]),
                })
                assert not response.isError
                result = json.loads(response.content[0].text)
                assert result["ok"] and "cold" in result["retire"]
                assert result["loops"][0]["uniqueness"]["cost_multiplier"] is None
                # A successful arithmetic response must not leave stdout or the session broken.
                next_response = await session.call_tool("etalon_capabilities", {})
                assert not next_response.isError
                assert json.loads(next_response.content[0].text)["ok"]
    asyncio.run(check())
