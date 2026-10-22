"""Authority relocation preserves checkpoint bytes and never grants execution."""
import json

import pytest

from etalon.research.continuation import resume_research
from etalon.research.evidence_refs import file_identity
from test_research_continuation import case, checkpoint, publish, save, step


@pytest.mark.parametrize("read_mode", ["fresh", "frozen_view"])
@pytest.mark.parametrize("fault", [None, "bytes", "declared_hash", "wrong_source"])
def test_relocated_pause_uses_checkpoint_source_and_exact_guard(case, read_mode, fault):
    journal, ref, authority, root = case
    original = "Z:/unavailable-old-machine/research/PAUSED.md"
    authority.write_text("Paused fixture; no scientific execution.", encoding="utf-8")
    content = checkpoint(case, save(case, step(ref)))
    content["status_guards"] = [{"location": original, **file_identity(authority), "state": "paused"}]
    cp = save(case, content)
    ws = publish(case)
    config = json.loads(ws.read_text())
    config["status_refs"] = [original]
    mapping = {"source_id": "research", "original": original,
               "location": authority.name, **file_identity(authority)}
    if fault == "bytes":
        authority.write_text("Changed after migration.", encoding="utf-8")
    elif fault == "declared_hash":
        mapping["sha256"] = "0" * 64
    elif fault == "wrong_source":
        config["sources"].append({**config["sources"][0], "source_id": "another-source"})
        mapping["source_id"] = "another-source"
    config["location_map"] = [mapping]
    ws.write_text(json.dumps(config), encoding="utf-8")
    before = file_identity(journal.store.path)
    result = resume_research(ws, cp, read_mode=read_mode)
    state = result["authority"]
    assert state["state"] == ("paused" if fault is None else "review_required")
    if fault is None:
        assert state["sources"][0]["resolved_location"] == str(authority.resolve())
        assert state["sources"][0]["location"] == original
        assert state["sources"][0]["status"] == "unchanged"
    assert result["checkpoint_ref"] == cp
    assert result["execution_permission"] == "not_granted"
    assert result["automatic_retry"] is False
    assert file_identity(journal.store.path) == before
    assert not journal.store.actions()
