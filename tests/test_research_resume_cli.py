"""Exercise the real resume entrypoint from outside a relocated research folder."""
import json
import os
from pathlib import Path
import subprocess
import sys

import etalon
import pytest

from etalon.research.continuation import resume_research
from etalon.research.entry import resolve_entry
from etalon.research.evidence_refs import file_identity
from etalon.research.handoff import export_research_handoff, plan_research_handoff
from test_research_graph_memory import graph_case


@pytest.mark.parametrize("entry_kind", ["original", "handoff", "locator"])
def test_resume_cli_resolves_entry_relative_to_its_file_and_remains_read_only(graph_case, tmp_path, entry_kind):
    entry = graph_case["entry"]
    if entry_kind != "original":
        bundle = tmp_path / "portable"
        export_research_handoff(plan_research_handoff(entry), bundle)
        entry = bundle / "entry.json"
        assert json.loads(entry.read_text(encoding="utf-8"))["workspace"] == "workspace.json"
    if entry_kind == "locator":
        entry = tmp_path / "locator.json"
        entry.write_text(json.dumps({"entry": "portable/entry.json"}), encoding="utf-8")
    workspace, checkpoint, _, _ = resolve_entry(entry)
    expected = resume_research(workspace, checkpoint)
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    before = {p: file_identity(p) for p in tmp_path.rglob("*") if p.is_file()}
    env = {**os.environ, "PYTHONPATH": str(Path(etalon.__file__).parent.parent), "PYTHONDONTWRITEBYTECODE": "1"}
    example = Path(__file__).resolve().parents[1] / "examples/research_continuation.py"
    result = subprocess.run([sys.executable, "-B", str(example), "resume", "--entry", str(entry)],
                            cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    actual = json.loads(result.stdout)
    assert actual == expected
    assert actual["execution_permission"] == "not_granted" and not actual["automatic_retry"]
    assert before == {p: file_identity(p) for p in tmp_path.rglob("*") if p.is_file()}
