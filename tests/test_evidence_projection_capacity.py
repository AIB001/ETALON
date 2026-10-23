"""A normal multi-step journal must keep recent typed records readable."""
from etalon.research import evidence
from etalon.research.evidence_refs import load_workspace
from test_research_graph_memory import graph_case


def test_source_index_larger_than_graph_view_retains_every_published_step(graph_case):
    c=graph_case
    refs=[c['save']({**c['step'],'reason':f'Fixture normal execution progress {i}'}) for i in range(160)]
    records,coverage,warnings=evidence._collect(load_workspace(c['publish']()))
    assert all(records['science'][r['record_id']]['projection']['status']=='verified' for r in refs)
    assert coverage[0]['status']=='complete'


def test_late_corrupt_artifact_is_not_hidden_by_a_large_index(graph_case):
    c=graph_case
    refs=[c['save']({**c['step'],'reason':f'Fixture progress {i}'}) for i in range(140)]
    ws=load_workspace(c['publish']())
    records,_,_=evidence._collect(ws)
    from pathlib import Path
    damaged=refs[-1]['record_id']
    Path(records['science'][damaged]['record']['path']).write_text('corrupt',encoding='utf-8')
    records,coverage,_=evidence._collect(ws)
    assert coverage[0]['status']=='partial'
    assert records['science'][damaged]['projection']['status']=='unavailable'


def test_projection_still_reports_exhaustion_at_the_explicit_bound(graph_case,monkeypatch):
    c=graph_case
    monkeypatch.setattr(evidence,'MAX_PROJECTED_CONTENTS',1)
    records,coverage,warnings=evidence._collect(load_workspace(c['publish']()))
    assert coverage[0]['status']=='partial'
    assert any(r['reason']=='projection read budget exceeded' for r in coverage[0]['projection_failures'])
    assert sum(r.get('projection',{}).get('status')=='verified' for r in records['science'].values())<=1
