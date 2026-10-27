"""Synthetic source fixture; validates delivery, not literature conclusions."""
import asyncio
import json
from pathlib import Path
import pytest


def fixture():
    dossier=dict(targets=[],papers=[{'source':'MED','id':'123'}],fulltexts={},
        chembl_details={'assay':[],'document':[],'molecule':[]},activities=[],
        structures=[],ccd_ligands=[],bindingdb=[],review_queue=[{'source':'MED','id':'123'}],
        coverage={'scope':'fixture only'})
    curation=dict(schema_version='1',reviewed_at='fixture',candidates=[],
        papers_reviewed=[{'pmid':'123','decision':'included','reason':'fixture'}],
        limitations=['Fixture does not establish activity'],knowledge=[dict(
            statement='Endpoint difference matters',conditions={'endpoint':'fixture'},
            implications=['Compare like assays'],limitations=['Different conditions untested'],
            basis='agent_inference',references=[dict(source='europepmc',record_id='123',
                locator='abstract',url='https://example.org/123')])])
    return dossier,curation


def test_existing_curation_preserves_scope_and_exposes_knowledge_without_network(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'asset/molquarry/src'))
    from molquarry.workflows.review import Curation,integrate_review,knowledge_context
    from molquarry.mcp_server import create_server
    dossier,curation=fixture()
    full=integrate_review(dossier,Curation.model_validate(curation))
    brief=knowledge_context(full)
    assert brief['knowledge'][0]['conditions']=={'endpoint':'fixture'}
    assert brief['knowledge'][0]['basis']=='agent_inference'
    assert brief['limitations'] and 'references' not in brief['knowledge'][0]
    assert full['knowledge'][0]['references']
    (tmp_path/'dossier.json').write_text(json.dumps(dossier))
    monkeypatch.setenv('MOLQUARRY_WORKSPACE',str(tmp_path))
    class NoNetwork:
        def __getattr__(self,name):raise AssertionError('knowledge synthesis must not query: '+name)
    server=create_server(NoNetwork())
    fn=server._tool_manager.get_tool('build_target_bundle').fn
    response=asyncio.run(fn('dossier.json',curation,'knowledge',view='knowledge'))
    assert not response.isError
    body=response.structuredContent
    details=json.loads(Path(body['details']['path']).read_text())
    assert details['knowledge']==full['knowledge']
    assert len(list((tmp_path/'knowledge').iterdir()))==1
    again=asyncio.run(fn('dossier.json',curation,'knowledge',view='knowledge'))
    assert again.structuredContent==body
    curation['knowledge'][0]['references'][0]['record_id']='not-fetched'
    with pytest.raises(ValueError,match='Unfetched knowledge'):integrate_review(dossier,Curation.model_validate(curation))
