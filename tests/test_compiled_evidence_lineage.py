"""Compiled dataflow wiring must not become fictitious Journal references."""
from etalon.research.evidence import _references
from etalon.research.evidence_refs import make_ref


def test_compiled_inputs_preserve_nested_evidence_but_not_ports():
    evidence = make_ref("published", "artifact-real", {"fixture": True})
    payload = {"compiled_stages": [{"inputs": [{"port": "rows", "stage_id": "standardize",
        "source_port": "out", "contract": "molecules/1", "evidence_refs": [evidence]}]}],
        "parents": ["legacy-record"]}
    assert _references(payload) == [evidence, "legacy-record"]


def test_malformed_explicit_reference_is_not_silently_discarded():
    malformed = {"source_id": "published", "record_id": "missing-digest"}
    assert _references({"inputs": [malformed]}) == [malformed]
    assert _references({"evidence_refs": [{"bad": True}]}) == [{"bad": True}]
