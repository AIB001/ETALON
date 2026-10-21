"""Bounded synthetic skill/usage revision; no native host or science benefit claim.

Run with the actual ETALON interpreter and a new output directory:
    python examples/skill_improvement.py /absolute/new-directory
Uses existing snapshots, journal and one-page hook. No new registry or service.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from etalon import host, skills
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, MethodEvaluation, MethodVersion, ResearchJournal, TaskSplit
from etalon.research.method_revision import build_method_revision, journal_ref, record_method_revision


def run(directory: str | Path) -> dict:
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    store = CampaignStore(directory / "campaign.sqlite")
    store.configure(CampaignSpec("unmeasured", 1, "fixture_credit", "method-revision-fixture"),
                    [Endpoint("unmeasured", "fixture", "not_measured", "count", "fixture/1", 1,
                              queryable=False)])
    journal = ResearchJournal(store)

    def artifact(name, payload, *, accounting=False):
        path = directory / (name + ".json")
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        item = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
                        bindings={"kind": "accounting"} if accounting else {})
        journal.record(item)
        return item.id

    problem = artifact("problem", {
        "origin": "synthetic development example, not a production timing trace",
        "problem": "Method gives no exact member for paired-model input construction",
        "hypothesis": "An exact member pointer may avoid unrelated source reads",
        "causal_latency_effect": None,
    })

    def version(name, body, role, *, parent=None):
        root = directory / name
        root.mkdir()
        (root / "SKILL.md").write_text(
            "---\nname: local-lookup\ndescription: Find paired-model input guidance\n---\n"
            "Use only the relevant part of references/lookup.md.\n", encoding="utf-8")
        (root / "references").mkdir()
        (root / "references" / "lookup.md").write_text(body + "\n", encoding="utf-8")
        content = skills.persist_snapshot("local-lookup", directory / (name + "-saved"),
                                          bundle_ref={"root": str(root)})
        identifier = journal.record(MethodVersion(
            name, (role,), content, parents=(parent,) if parent else (),
            evidence_refs=(problem,), verification_basis="content_verified",
            change_summary="Synthetic lookup candidate; effect unknown"))
        return identifier, content

    old_s, old_content = version("content-old", "Find the paired-model documentation.", "domain_skill")
    new_s, new_content = version("content-new",
        "Read campaign member references/paired-models-and-segments.md for empirical_model inputs.",
        "domain_skill", parent=old_s)
    old_u, _ = version("usage-old", "Inspect the relevant SDK source when the entrypoint is unknown.", "usage_method")
    new_u, _ = version("usage-new",
        "When an exact relevant member is known, read it directly; otherwise inspect scoped discovery or SDK source.",
        "usage_method", parent=old_u)
    g0, _ = version("improvement-old", "Retain problem and distinguish evidence from a benefit claim.",
                    "improvement_method")
    g1, _ = version("improvement-new", "Also distinguish selection gaps from content defects.",
                    "improvement_method", parent=g0)
    producer_use = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "fixture-generation", "scope": str(store.path)},),
        selected_methods=({"method_version_ref": g0, "scope": "problem and generation relationship"},),
        reason="Synthetic attribution only; this example did not run a native model")
    # This is an actual SDK member read, not evidence that an LLM adopted a method.
    returned = skills.read_member("campaign", "references/paired-models-and-segments.md")
    trial = artifact("trial", {"operation": "skills.read_member", "slug": "campaign",
        "member": "references/paired-models-and-segments.md", "returned": returned,
        "native_host_adoption": "not_run", "quality_effect": None})
    use = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "bounded-lookup-trial", "scope": str(store.path)},),
        selected_methods=({"method_version_ref": new_s, "scope": "exact reference pointer only"},),
        usage_method_ref=new_u, visible_evidence_refs=(problem, trial),
        host_conditions={"host": "synthetic SDK example", "model": None},
        reason="Example author attributed partial use; native adoption remains unmeasured")
    protocol = artifact("protocol", {"role": "development diagnostic",
        "criterion": "Exact member resolves; no claim about host quality or time improvement",
        "later_independent_comparison": "not_run", "stop": "one lookup; retain both versions"})
    initial = artifact("initial", {"skill_old": old_s, "usage_old": old_u,
        "python": sys.executable, "module": skills.__file__, "science": "not_run"})
    split = TaskSplit(("lookup-dev",), ("future-validation",), ("future-transfer",),
                     {"lookup-dev": "synthetic-fixed", "future-validation": "unallocated",
                      "future-transfer": "unallocated"},
                     "future quality and total cost; not measured here")
    journal.record(split)
    costs = artifact("accounting", {"measured_sdk_wall_seconds_lower_bound": time.perf_counter() - started,
        "coverage": "through initial artifact; excludes later records, hook and delivery",
        "native_input_tokens": None, "native_output_tokens": None, "monetary_cost": None,
        "science_executions": 0}, accounting=True)
    evaluation = journal.record(MethodEvaluation((new_s, new_u), (old_s, old_u), protocol, split.id,
        initial, (trial,), (costs,), "diagnostic", "Exact member readable; independent benefit not_run",
        "One synthetic SDK development trial; S and usage effects not separately measured"))
    ref = lambda identifier: journal_ref(journal, identifier, source_id="revision-example")
    revisions = {}
    for kind, base, candidates in (("S", old_s, [new_s]), ("U", old_u, [new_u]),
                                   ("G", g0, [g1]), ("no_change", old_s, [])):
        payload = build_method_revision(
            problem_refs=[ref(problem)], target_kind="S" if kind == "no_change" else kind,
            base_method_ref=ref(base), producer_method_ref=ref(g0), producer_use_ref=ref(producer_use),
            candidate_version_refs=[ref(c) for c in candidates],
            change_summary="Keep old method: no evidence for forcing adoption" if not candidates else "Fixture candidate",
            mechanism_hypothesis="Exact pointers may reduce lookup; effect unmeasured",
            scope="Synthetic engineering example only", counterexamples=["Direct SDK use succeeds without adoption"],
            trial_refs=[ref(trial)], accounting_refs=[ref(costs)])
        revisions[kind] = record_method_revision(journal, payload, directory / "revisions",
            source_id="revision-example", source_kind="fixture")
        assert revisions[kind] == record_method_revision(journal, payload, directory / "revisions",
            source_id="revision-example", source_kind="fixture")
    decisions = []
    params = dict(log_ref={"kind": "campaign", "database": str(store.path)},
        state_path=directory / "cursor.json", handler_method_version="example-bounded-handler-v1",
        handler=lambda event, request_id: decisions.append({"event": event["event_ref"],
            "request_id": request_id, "decision": "stop; keep both versions pending independent evidence"}),
        event_types=["research_recorded"], record_types=["etalon-method-evaluation/1"], limit=100)
    hook = host.consume_events(**params)
    replay = host.consume_events(**params)
    stop = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "bounded-lookup-trial", "scope": str(store.path)},),
        selected_methods=(), parents=(use,), visible_evidence_refs=(evaluation,),
        reason="Stop after actual member return; no independent benefit or default promotion")
    rollback = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "future-fixture-use", "scope": str(store.path)},),
        selected_methods=({"method_version_ref": old_s, "scope": "prior lookup guidance"},),
        parents=(stop,), reason="Select old version again; candidate is retained")
    after_notes = host.consume_events(**params)
    report = {"scope": "synthetic engineering only", "database": str(store.path),
        "python": sys.executable, "module_paths": {"skills": skills.__file__, "host": host.__file__},
        "versions": {"old_s": old_s, "new_s": new_s, "old_usage": old_u, "new_usage": new_u},
        "content_refs": {"old_s": old_content, "new_s": new_content},
        "use": use, "stop": stop, "rollback": rollback, "producer_use": producer_use,
        "generator_versions": {"g0": g0, "g1": g1}, "revisions": revisions,
        "evaluation": evaluation, "hook": hook, "replay": replay, "after_notes": after_notes,
        "decisions": decisions, "observations": len(store.observations()),
        "independent_benefit": "not_run", "default_changed": False}
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1]), indent=2))
