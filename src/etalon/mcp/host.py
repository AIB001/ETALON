"""Read-only host discovery; does not create another planner or scientific journal."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from etalon.mcp._common import Cost, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_capabilities() -> str:
        """FREE. Inspect installed tools, cost classes, skill versions and runtime source.

        The host chooses its next research action. Skill digests include supporting
        files; discovery does not prove host invocation or scientific validation.
        """
        from etalon.mcp.server import costs
        from etalon.runtime.operations import describe
        from etalon.skills import catalog

        return ok(**catalog(), tools=costs(), operations=describe(),
                  runtime={"python": sys.executable, "module": str(Path(__file__).resolve()),
                           "durable_workers": "Linux /proc and POSIX locks required"})

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_skill_read(slug: str, member: str = "SKILL.md") -> str:
        """FREE. Read one text member of an installed skill, with its file and bundle digests.

        Discover slugs and members with etalon_capabilities. References are loaded
        only when relevant; no experiment is dispatched or skill use inferred.
        """
        from etalon.skills import read_member

        return ok(**read_member(slug, member))
