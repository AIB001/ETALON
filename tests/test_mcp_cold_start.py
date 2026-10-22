"""Cold stdio regressions: real pipes and imports, no scientific calculation."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import timedelta

import pytest

from etalon.mcp import _common

ROOT = Path(__file__).resolve().parents[1]


def _stop_tree(child):
    if child.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                       capture_output=True, timeout=10, check=False)
    else:
        os.killpg(child.pid, signal.SIGKILL)
    child.wait(timeout=10)


def _launch(arguments, **kwargs):
    return subprocess.Popen([sys.executable, "-B", *arguments],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        start_new_session=sys.platform != "win32", **kwargs)


def test_stdio_input_preserves_nonpipe_streams_and_posix(monkeypatch, tmp_path):
    stream = io.StringIO("input")
    monkeypatch.setattr(sys, "stdin", stream)
    with _common.stdio_input():
        assert sys.stdin is stream
    assert not stream.closed
    source = tmp_path / "input.txt"
    source.write_text("file input", encoding="utf-8")
    with source.open(encoding="utf-8") as regular_file:
        monkeypatch.setattr(sys, "stdin", regular_file)
        with _common.stdio_input():
            assert sys.stdin is regular_file
        assert not regular_file.closed
    monkeypatch.setattr(sys, "platform", "linux")
    assert _common._windows_pipe_input(object()) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows anonymous-pipe regression")
def test_idle_pipe_allows_cold_import_and_preserves_split_text_and_eof(tmp_path):
    pytest.importorskip("numpy")
    ready = tmp_path / "import-ready"
    code = f'''
import json,os,sys,threading,time
from pathlib import Path
from etalon.mcp._common import stdio_input
original=sys.stdin
lines=[]
with stdio_input():
    assert sys.stdin is not original
    assert 'numpy' not in sys.modules
    reader=threading.Thread(target=lambda: lines.extend(sys.stdin),daemon=True)
    reader.start()
    time.sleep(0.1)
    import numpy
    Path({str(ready)!r}).write_text(numpy.__file__,encoding='utf-8')
    reader.join(10)
    assert not reader.is_alive(), 'reader did not stop at EOF'
assert sys.stdin is original and not original.closed
os.fstat(original.fileno())
try:
    with stdio_input():
        raise RuntimeError('fixture')
except RuntimeError:
    pass
assert sys.stdin is original and not original.closed
print(json.dumps(lines),flush=True)
'''
    child = _launch(["-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 12
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "cold native import blocked while pipe had no input"
        # Split inside a UTF-8 code point and between CR/LF; include multiple
        # messages, a replacement character, and a final line without newline.
        data = '{"name":"光"}\r\n{"id":2}\n\nlast'.encode("utf-8") + b"\xff"
        split = data.index("光".encode("utf-8")) + 1
        crlf_split = data.index(b"\r") + 1
        for chunk in (data[:split], data[split:split + 1], data[split + 1:crlf_split], data[crlf_split:]):
            child.stdin.write(chunk)
            child.stdin.flush()
            time.sleep(0.02)
        child.stdin.close()
        child.stdin = None
        output, error = child.communicate(timeout=12)
        assert child.returncode == 0, error.decode(errors="replace")
        assert json.loads(output.decode("utf-8")) == ['{"name":"光"}\n', '{"id":2}\n', '\n', 'last\ufffd']
    finally:
        _stop_tree(child)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle-error regression")
def test_invalid_pipe_handle_is_an_error_not_eof():
    import ctypes

    class Kernel:
        @staticmethod
        def PeekNamedPipe(*args):
            ctypes.set_last_error(6)  # ERROR_INVALID_HANDLE, not an EOF condition.
            return 0

    reader = _common._WindowsPipeReader(0, 0, Kernel(), ctypes)
    with pytest.raises(OSError):
        reader.readinto(bytearray(8))
    reader.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pipe cancellation regression")
def test_open_pipe_reader_stops_on_cancellation_and_exception():
    code = '''
import sys,threading,time
import anyio
from etalon.mcp._common import stdio_input
original=sys.stdin
with stdio_input():
    async def run():
        with anyio.move_on_after(0.1) as scope:
            await anyio.to_thread.run_sync(sys.stdin.readline)
        assert scope.cancel_called
    anyio.run(run)
assert sys.stdin is original and not original.closed
try:
    with stdio_input():
        reader=threading.Thread(target=sys.stdin.readline,daemon=True)
        reader.start()
        time.sleep(0.1)
        raise RuntimeError('fixture')
except RuntimeError:
    pass
reader.join(2)
assert not reader.is_alive()
assert sys.stdin is original and not original.closed
'''
    child = _launch(["-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # Keep stdin open and empty until the child exits; communicate() would
        # close it early and hide cancellation or buffer-lock failures.
        child.wait(timeout=8)
        output, error = child.communicate()
        assert child.returncode == 0, (output + error).decode(errors="replace")
    finally:
        _stop_tree(child)


def test_real_cold_stdio_configure_is_bounded_and_memory_stays_lazy(tmp_path):
    pytest.importorskip("mcp")
    child = _launch([str(Path(__file__).resolve()), "--probe", str(tmp_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        output, error = child.communicate(timeout=40)
        assert child.returncode == 0, (output + error).decode(errors="replace")
        report = json.loads((tmp_path / "cold-stdio.json").read_text(encoding="utf-8"))
        assert report["before"]["numpy"] is None and report["before"]["pyarrow"] is None
        assert report["after"]["numpy"] and report["after"]["pyarrow"]
        assert report["first_configure"]["ok"] and report["second_configure"]["ok"]
        assert report["configure_seconds"] < 20
    finally:
        _stop_tree(child)


async def _probe(directory):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Add a read-only test observer to the real server. Use main() so the
    # production transport lifecycle is exercised, including normal EOF exit.
    code = '''
import json,sys
from etalon.mcp import server
build=server.build
def observed_build():
    mcp=build()
    @mcp.tool()
    def test_import_snapshot() -> str:
        return json.dumps({name:getattr(sys.modules.get(name),'__file__',None)
                           for name in ('numpy','pyarrow','etalon','mcp')})
    return mcp
server.build=observed_build
server.main()
'''
    server = StdioServerParameters(command=sys.executable, args=["-B", "-c", code],
                                   env={"PYTHONPATH": str(ROOT / "src")})
    manifest = directory / "memory-fixture.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [{
        "record_id": "cold-start-fixture", "record_type": "observation", "visibility": "released",
        "record": {"task_id": "cold-start", "role": "diagnostic", "cost": None,
                   "statement": "Engineering fixture; no scientific conclusion"}}]}), encoding="utf-8")
    workspace = directory / "memory-workspace.json"
    workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1",
        "workspace_id": "cold-start-fixture", "sources": [{"source_id": "fixture",
        "kind": "artifact_manifest", "location": manifest.name,
        "identity": {"kind": "snapshot_sha256", "value": hashlib.sha256(manifest.read_bytes()).hexdigest()}}]}),
        encoding="utf-8")
    design = {"schema": "etalon-cascade-design/1", "name": "cold-stdio-fixture",
              "tiers": [{"id": "measure", "title": "CPU property fixture", "criteria": [{"id": "properties",
                  "backend": "features.rdkit_properties@0.1.0", "settings": {"schema_version": 1}}]}]}
    async with (stdio_client(server) as (read, write),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session):
        await session.initialize()
        await session.list_tools()
        await session.read_resource("etalon://skills/research-context")

        async def call(name, arguments=None):
            result = await session.call_tool(name, arguments or {})
            assert not result.isError, result
            return json.loads(result.content[0].text)

        memory = await call("etalon_evidence_query", {"workspace": str(workspace)})
        assert memory["ok"] and len(memory["items"]) == 1
        before = await call("test_import_snapshot")
        assert before["numpy"] is None and before["pyarrow"] is None, before
        start = time.monotonic()
        first = await call("etalon_screen_configure", {
            "workspace": str(directory / "first"), "configuration": design})
        elapsed = time.monotonic() - start
        after = await call("test_import_snapshot")
        second = await call("etalon_screen_configure", {
            "workspace": str(directory / "second"), "configuration": design})
        assert (await call("etalon_workflow_capabilities"))["ok"]
        (directory / "cold-stdio.json").write_text(json.dumps({
            "before": before, "after": after, "configure_seconds": elapsed,
            "memory_items": len(memory["items"]),
            "first_configure": first, "second_configure": second,
        }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    assert sys.argv[1] == "--probe"
    asyncio.run(asyncio.wait_for(_probe(Path(sys.argv[2])), timeout=35))
