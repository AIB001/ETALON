"""Serve domain tools and optional capability resources over stdio.

The host owns research decisions. Tool registration and cost discovery share a
single path; skills, references and short host guidance are loaded on demand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from etalon.mcp._common import Cost, require_mcp
from etalon.skills import ROLE_PATH, bundle_root, catalog, read_member
from etalon.skills import WORKFLOWS as WORKFLOWS  # Preserve the public catalog import.

#: Stable entrypoint URI. Full capability bundles also ship in installed wheels.
SKILL_RESOURCE = "etalon://skills/campaign"
RUNTIME_RESOURCE = "etalon://runtime"


def _skill_path(slug: str = "campaign") -> Path:
    """Compatibility accessor for the entrypoint of a complete capability bundle."""
    return bundle_root(slug) / "SKILL.md"


def register_tools(server: Any) -> None:
    """One registration path for the actual server and its cost index."""
    from etalon.mcp import active, data, execution, governance, host, planning, runtime, sweep

    for module in (planning, governance, active, execution, data, runtime, sweep, host):
        module.register(server)


def build() -> Any:
    """Construct the server with every tool and resource registered."""

    server_class = require_mcp()
    mcp = server_class("etalon")
    register_tools(mcp)

    @mcp.resource("etalon://skills/molquarry")
    def molquarry_workflows() -> str:
        """Discover all pinned acquisition workflows and their reference resource URIs."""
        from etalon.boundary.infra import load

        load("molquarry")
        from molquarry.skills import list_skills

        rows = []
        for entry in list_skills()["skills"]:
            slug = entry["name"].removeprefix("molquarry-")
            uri = "etalon://skills/molquarry/" + slug
            rows.append({**entry, "uri": uri, "references": [
                uri + "/" + path for path in entry["resources"]
                if path.startswith("references/")
            ]})
        return json.dumps({"skills": rows, "execution": "Use ETALON data tools for sealed acquisition."},
                          ensure_ascii=False)

    @mcp.resource("etalon://skills/molquarry/{skill}")
    def molquarry_workflow(skill: str) -> str:
        """Read a pinned workflow; discover names at etalon://skills/molquarry.

        Use ETALON's data tools for sealed acquisition and campaign ingress. These documents
        also describe advanced MolQuarry SDK workflows, not additional ETALON MCP tools.
        """
        from etalon.boundary.infra import load

        load("molquarry")
        from molquarry.skills import read_skill

        return read_skill("molquarry-" + skill)["content"]

    @mcp.resource("etalon://skills/molquarry/{skill}/references/{reference}")
    def molquarry_reference(skill: str, reference: str) -> str:
        """The reference document linked by each pinned MolQuarry workflow."""
        from etalon.boundary.infra import load

        load("molquarry")
        from molquarry.skills import read_skill

        return read_skill("molquarry-" + skill, "references/" + reference)["content"]

    @mcp.resource(SKILL_RESOURCE)
    def campaign_workflow() -> str:
        """Optional capability for expensive execution and scientific evidence admission."""
        return read_member("campaign")["content"]

    @mcp.resource("etalon://skills")
    def workflows() -> str:
        """Optional skills, supporting files and independently versioned host guidance."""
        report = catalog()
        # Retain the old index shape for existing readers, without a mandatory route.
        report["workflows"] = [
            {"uri": f"etalon://skills/{row['slug']}", "skill": row["name"],
             "regime": row["description"]} for row in report["skills"]]
        return json.dumps(report, ensure_ascii=False)

    @mcp.resource("etalon://skills/{slug}")
    def workflow(slug: str) -> str:
        """One workflow document; discover the slugs at ``etalon://skills``."""

        return read_member(slug)["content"]

    @mcp.resource("etalon://skills/{slug}/references/{name}")
    def skill_reference(slug: str, name: str) -> str:
        """Read one supporting reference; exact members are listed in the skill catalog."""
        return read_member(slug, "references/" + name)["content"]

    @mcp.resource("etalon://host")
    def host_guidance() -> str:
        """Short research responsibilities and skill usage guidance for the host."""
        return ROLE_PATH.read_text(encoding="utf-8")

    @mcp.resource(RUNTIME_RESOURCE)
    def runtime_workflow() -> str:
        """Durable workflow and executor protocol, resource budgets and verified completion rules.

        Read before workflow submission or external controller decisions. The same document
        ships with the runtime package and describes how to inspect and recover saved work.
        """

        return (Path(__file__).resolve().parents[1] / "runtime" / "README.md").read_text(encoding="utf-8")

    @mcp.resource("etalon://findings")
    def findings() -> str:
        """Every measurement this project has recorded, as one document.

        Each is a JSON file under ``findings/`` carrying what was measured, why it matters, and a
        "how this could be wrong" section. A model justifying a decision to an operator should quote
        from here rather than paraphrase: the numbers are the argument.
        """

        root = Path(__file__).resolve().parents[3] / "findings"
        if not root.is_dir():
            return "No findings directory in this installation."
        parts = [
            f"## {path.name}\n\n```json\n{path.read_text(encoding='utf-8')}\n```"
            for path in sorted(root.glob("*.json"))
        ]
        return "# ETALON findings\n\n" + "\n\n".join(parts)

    @mcp.resource("etalon://decisions")
    def decisions() -> str:
        """Every architectural decision, with the measurement that forced it.

        Read ``0003`` before acting as an advisor inside a campaign, and ``0005`` before planning one
        that ends in relative FEP.
        """

        root = Path(__file__).resolve().parents[3] / "docs" / "adr"
        if not root.is_dir():
            return "No ADR directory in this installation."
        return "\n\n---\n\n".join(
            path.read_text(encoding="utf-8") for path in sorted(root.glob("*.md"))
        )

    return mcp


def costs() -> dict[str, str]:
    """Every tool's spend classification, for a caller that wants it as data.

    Derived from the decorator rather than from a list, so a tool cannot be added without one.
    """

    found: dict[str, str] = {}

    class Collector:
        def tool(self) -> Any:
            def decorate(function: Any) -> Any:
                cost = getattr(function, "etalon_cost", None)
                if not isinstance(cost, Cost):
                    raise ValueError(f"tool {function.__name__} has no explicit cost classification")
                if function.__name__ in found:
                    raise ValueError(f"duplicate tool name: {function.__name__}")
                found[function.__name__] = cost.value
                return function

            return decorate

    collector = Collector()
    register_tools(collector)
    return found


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
