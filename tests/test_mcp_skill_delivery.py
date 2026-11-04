"""Native MCP discovery reaches the same optional guidance as host exports."""
import asyncio
import json
import os
import sys
from pathlib import Path

from etalon import host, skills


def test_stdio_instructions_and_selected_skill_match_host_exports(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    selected = ["research", "method-optimization", "skill-improvement"]
    exports = {}
    for consumer in ("codex", "claude"):
        root = tmp_path / consumer
        host.export(root, host=consumer, selected_skills=selected)
        checked = host.verify(root)
        assert checked["export_matches_manifest"] and checked["matches_current_source"]
        exports[consumer] = root

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "etalon.mcp"],
            env={**os.environ, "PYTHONPATH": str(skills.ROLE_PATH.parent.parent), "PYTHONUTF8": "1"})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            entry = skills.ROLE_PATH.parent / "host-guides" / "mcp-entry.md"
            assert initialized.instructions == entry.read_text(encoding="utf-8")
            for slug in selected:
                expected = skills.read_member(slug)["content"]
                result = await session.call_tool("etalon_skill_read", {"slug": slug})
                assert not result.isError
                assert json.loads(result.content[0].text)["content"] == expected
                resource = await session.read_resource("etalon://skills/" + slug)
                assert resource.contents[0].text == expected
                for consumer, directory in (("codex", ".agents/skills"), ("claude", ".claude/skills")):
                    path = exports[consumer] / directory / skills.WORKFLOWS[slug] / "SKILL.md"
                    assert path.read_bytes().decode("utf-8") == expected
            result = await session.call_tool("etalon_skill_read", {
                "slug": "method-optimization", "member": "references/decision-quality.md"})
            assert not result.isError
            assert json.loads(result.content[0].text)["content"] == skills.read_member(
                "method-optimization", "references/decision-quality.md")["content"]

    asyncio.run(asyncio.wait_for(check(), timeout=45))
