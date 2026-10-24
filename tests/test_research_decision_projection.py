"""Actual ordinary records remain attributable judgments in existing graph views."""
from etalon.research.research_decision import build_decision_update
from etalon.research.evidence_window import build_evidence_window
from etalon.research.graph_memory import build_research_graph, node_identity, _project_node
from test_research_continuation import case, publish, save, checkpoint, step
from test_research_decision import prepared, build, store, observed


def test_decision_update_and_window_reachable_from_ordinary_checkpoint(case, prepared):
    window=save(case,build_evidence_window(publish(case),task_id='case',branch_id='normal',workset_ref=None,
        visible_refs=[case[1]],timing_mode='retrospective',coverage_scope='Known synthetic fixture'))
    decision=store(prepared,build(case,prepared,evidence_window_ref=window))
    args,*_=observed(case,prepared,decision,'contradicted')
    update=store(prepared,build_decision_update(publish(case),**args))
    progress=save(case,step(case[1],output_refs=[update]))
    cp=save(case,checkpoint(case,progress))
    graph=build_research_graph(publish(case),checkpoint_ref=cp,max_nodes=64)
    nodes={n['node_id']:n for n in graph['nodes']}
    for ref in (decision,update):
        node=nodes[node_identity(ref)]
        assert node['home']=='Judge' and node['kind']=='J'
        assert node['research_decision']['benefit']=='not_inferred'
    assert nodes[node_identity(update)]['research_decision']['counter_refs']
    assert nodes[node_identity(window)]['evidence_timing']['timing_mode']=='retrospective'


def test_release_projection_distinguishes_window_ack_from_result_delivery():
    ref={'schema':'etalon-evidence-ref/1','source_id':'fixture','record_id':'artifact-'+'a'*64,'record_digest':'a'*64}
    node=_project_node(ref,{'record_type':'artifact','record':{},'projected_content':{
        'schema':'etalon-outcome-release/1','window_delivery':'host_acknowledged',
        'outcome_delivery':'prepared_for_return','consumption':'not_attested'}},task_id='fixture',home_overrides={})
    assert node['evidence_timing']['window_delivery']=='host_acknowledged'
    assert node['evidence_timing']['outcome_delivery']=='prepared_for_return'
    assert node['evidence_timing']['consumption']=='not_attested'
