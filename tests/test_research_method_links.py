"""Attribution is inspectable, but selection alone must never imply execution."""
import pytest

from etalon.research import MethodUse
from etalon.research.composition import record_evaluation
from etalon.research.evidence import query_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.graph_memory import build_research_graph, node_identity
from etalon.research.research_context import build_research_context
from test_research_continuation import case, save, checkpoint, publish, step
from test_research_composition import evaluation_plan, node, ref


def test_evaluation_raw_resource_id_reaches_exact_same_source_receipt(case):
    j,evidence,_,root=case
    j.store.reserve_resource_execution('fixture-job','a'*64,0,unit='wall_second',provenance={'fixture':True})
    j.store.settle_resource_execution('fixture-job',measured_cost=0,receipt={'fixture':True})
    plan=save(case,evaluation_plan(case))
    evaluated=record_evaluation(j,plan,directory=root/'content',source_id='research',source_kind='fixture',
        result_refs=[evidence['record_id']],accounting_refs=['fixture-job'],conclusion='Synthetic accounting linkage only',scope='fixture')
    cp=save(case,checkpoint(case,evaluated['step_ref']))
    graph=build_research_graph(publish(case),checkpoint_ref=cp,max_nodes=128)
    assert not any(i.get('relation')=='evaluation_cost' for i in graph['issues'])
    costs=[n for n in graph['nodes'] if n['ref']['record_id']=='budget:execution:fixture-job']
    assert len(costs)==1 and costs[0]['ref']['source_id']=='research'
    assert any(e.get('relation')=='evaluation_cost' and e['to']==costs[0]['node_id'] for e in graph['edges'])
    from etalon.research.continuation import resume_research
    resumed=resume_research(publish(case),cp)
    assert not any(i.get('record_id')=='fixture-job' and i['reason']=='legacy_reference_unavailable' for i in resumed['issues'])


def test_use_node_evaluation_and_choice_form_explicit_links(case):
    j, evidence, _, root = case
    plan = evaluation_plan(case)
    candidate, baseline = plan["detail"]["subjects"][0], plan["detail"]["comparators"][0]
    use = ref(case, MethodUse(({"kind": "task", "id": "case", "scope": str(j.store.path)},),
        ({"method_version_ref": candidate["record_id"], "adoption": "partial",
          "used_parts": ["identity-check"], "omitted_parts": ["new-query"]},),
        visible_evidence_refs=(evidence["record_id"],), reason="Use only the applicable part"))
    executed = save(case, node(case, method_refs=[candidate], use_refs=[use]))
    plan_ref = save(case, plan)
    evaluation = record_evaluation(j, plan_ref, directory=root / "content", source_id="research",
        source_kind="fixture", result_refs=(evidence["record_id"],), conclusion="No improvement", scope="fixture",
        use_refs=[use], node_refs=[executed])
    decision = step(evidence, output_refs=[], method_refs=[baseline])
    decision["detail"]["basis_refs"] = [evaluation["evaluation_ref"]]
    choice = save(case, decision)
    cp = save(case, checkpoint(case, executed, step_refs=[executed, plan_ref, evaluation["step_ref"], choice]))
    ws = publish(case)
    before = file_identity(j.store.path)
    graph = build_research_graph(ws, checkpoint_ref=cp)
    relations = {(e["from"], e["to"], e.get("relation")) for e in graph["edges"]}
    for a,b,kind in [(executed,use,"applied_use"), (use,candidate,"selected_method"),
                     (evaluation["step_ref"],use,"evaluated_use"), (choice,evaluation["evaluation_ref"],"selection_basis")]:
        assert (node_identity(a), node_identity(b), kind) in relations
    used = next(n for n in graph["nodes"] if n["ref"] == use)
    assert used["method_use"]["selected_methods"][0]["used_parts"] == ["identity-check"]
    assert used["method_use"]["basis"] == "host_reported"
    context = build_research_context({"workspace": ws, "checkpoint_ref": cp}, view="node",
                                     node_id=node_identity(executed), max_bytes=65536)
    assert {node_identity(evaluation["evaluation_ref"]), node_identity(choice)} <= {n["node_id"] for n in context["items"]}
    assert use in [x["ref"] for x in query_evidence(ws, filters={"task_ids": ["case"], "record_types": ["methoduse"]})["items"]]
    assert file_identity(j.store.path) == before and not j.store.actions()
    # An explicit external workspace supports append-only attribution after
    # deployment. It does not allow unbound local-looking IDs or wrong task use.
    from etalon.research.continuation import read_content, record_content
    from etalon.research import ResearchJournal
    from etalon.active import CampaignStore, CampaignSpec, Endpoint
    other = CampaignStore(root / "other.sqlite")
    other.configure(CampaignSpec("audit",0,"wall_second","fixture"),
                    [Endpoint("audit","fixture","unmeasured","count","fixture",0,queryable=False)])
    target = ResearchJournal(other)
    attribution = read_content(ws, evaluation["step_ref"])
    attribution["supersedes"] = [evaluation["step_ref"]]
    with pytest.raises(EvidenceError, match="external workspace"):
        record_content(target, attribution, root/"external", source_id="new", source_kind="fixture")
    record_content(target, attribution, root/"external", source_id="new", source_kind="fixture", external_workspace=ws)
    assert file_identity(j.store.path) == before
    attribution["detail"]["use_refs"] = []
    with pytest.raises(EvidenceError, match="both use_refs"):
        record_content(target, attribution, root/"external", source_id="new", source_kind="fixture", external_workspace=ws)


def test_wrong_task_evaluation_link_rejected_before_append(case):
    plan = evaluation_plan(case)
    use = ref(case, MethodUse(({"kind": "task", "id": "other", "scope": str(case[0].store.path)},), reason="Other task"))
    plan["detail"]["use_refs"] = [use]
    before = file_identity(case[0].store.path)
    with pytest.raises(EvidenceError, match="same task"):
        save(case, plan)
    assert file_identity(case[0].store.path) == before


def test_partial_use_needs_parts_and_does_not_require_full_adoption():
    with pytest.raises(ValueError, match="used_parts"):
        MethodUse(({"kind": "task", "id": "x", "scope": "fixture"},),
                  ({"method_version_ref": "method-a", "adoption": "partial"},))
