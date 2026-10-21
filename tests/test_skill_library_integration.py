"""Original MolQuarry sources join the public catalog without a second copy."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from etalon import skills


def test_every_existing_product_bundle_is_registered_once():
    repo = Path(__file__).resolve().parents[1]
    originals = list((repo / '.claude/skills').glob('*/SKILL.md'))
    originals += list((repo / 'asset/molquarry/skills').glob('*/SKILL.md'))
    resolved = [skills.bundle_root(slug) / 'SKILL.md' for slug in skills.WORKFLOWS]
    assert set(resolved) == set(originals)
    assert len(resolved) == len(set(resolved))
    assert all('life-healthy-' not in str(path) for path in resolved)


def test_molquarry_summary_reads_only_selected_source_and_versions_all_members(monkeypatch):
    slug = 'molquarry-target-modulators'
    original = skills.bundle_root
    seen = []
    def selected(value):
        seen.append(value)
        assert value == slug
        return original(value)
    monkeypatch.setattr(skills, 'bundle_root', selected)
    summary = skills.catalog(detail='summary', skills=[slug])
    assert seen == [slug]
    manifest, contents = skills.snapshot(slug)
    assert summary['skills'][0]['manifest_sha256'] == manifest['manifest_sha256']
    assert 'references/workflow.md' in contents and 'agents/openai.yaml' in contents
    assert 'files' not in summary['skills'][0]
    assert skills.read_member(slug, 'references/workflow.md')['content'].encode() == contents['references/workflow.md']


def test_molquarry_discovery_uses_standard_library_without_loading_acquisition(tmp_path):
    environment = {**os.environ, 'PYTHONPATH': str(Path(skills.__file__).parents[1])}
    code = "from etalon.skills import catalog; import json,sys; print(json.dumps(catalog(skills=['molquarry-target-modulators']))); assert 'molquarry' not in sys.modules"
    run = subprocess.run([sys.executable, '-S', '-c', code], cwd=tmp_path,
                         env=environment, capture_output=True, text=True,
                         encoding='utf8', timeout=30, check=True)
    assert json.loads(run.stdout)['skills'][0]['slug'] == 'molquarry-target-modulators'


def test_legacy_molquarry_uris_and_new_skill_tool_return_identical_original_bytes():
    pytest.importorskip('mcp')
    from etalon.mcp.server import build
    server = build()
    async def read(uri):
        result = await server.read_resource(uri)
        return next(iter(result)).content
    async def exercise():
        listing = json.loads(await read('etalon://skills/molquarry'))
        expected = {slug for slug in skills.WORKFLOWS if slug.startswith('molquarry-')}
        assert {row['name'] for row in listing['skills']} == expected
        for row in listing['skills']:
            slug = row['name']
            manifest, contents = skills.snapshot(slug)
            assert await read(row['uri']) == contents['SKILL.md'].decode()
            assert await read('etalon://skills/' + slug) == contents['SKILL.md'].decode()
            assert set(row['resources']) == set(contents)
            for member in row['resources']:
                if member.startswith('references/'):
                    assert await read(row['uri'] + '/' + member) == contents[member].decode()
            assert manifest['name'] == row['name']
    asyncio.run(exercise())
