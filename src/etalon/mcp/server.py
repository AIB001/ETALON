"""Assemble ETALON's MCP server, and serve the workflow a model needs to drive it.

Run it as ``python -m etalon.mcp`` over stdio.

Two halves. The tools are the surface a model acts through, and they are classified by what they
spend so the classification is visible before a choice rather than after it. The resources are the
documents a model should read first: the campaign workflow, and the findings the workflow's decisions
rest on.

Serving the workflow as a resource rather than trusting it to a system prompt is deliberate. A model
driving 750,000 molecules through five tiers makes decisions whose justification lives in
measurements -- that one MM-PBSA trajectory is not reproducible to 12 kcal/mol, that 110 of 120 pairs
in a diverse shortlist are not alchemical edges, that eight engineer-hours can equal a GPU-year -- and
a workflow summarised from memory loses exactly those numbers. The resource is the same file the
Claude Code skill uses, so a model reaching ETALON through either path reads the same procedure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etalon.mcp._common import Cost, require_mcp

#: The skill, shipped inside the package so an installed ETALON serves the same text the repository's
#: .claude/skills directory holds. Kept as one file with one copy on disk; see ``_skill_path``.
SKILL_RESOURCE = "etalon://skills/campaign"


def _skill_path() -> Path:
    """Where the campaign workflow lives, preferring the repository copy when running from it.

    One document, two consumers: a Claude Code skill reads ``.claude/skills/etalon-campaign/SKILL.md``
    and an MCP client reads this resource. A second copy would drift, so the package looks for the
    repository file first and falls back to a copy installed beside the code.
    """

    here = Path(__file__).resolve()
    repository = here.parents[3] / ".claude" / "skills" / "etalon-campaign" / "SKILL.md"
    if repository.is_file():
        return repository
    installed = here.parent / "skills" / "campaign.md"
    if installed.is_file():
        return installed
    raise FileNotFoundError(
        "no campaign workflow document found. It should be at "
        ".claude/skills/etalon-campaign/SKILL.md in the repository, or packaged at "
        "etalon/mcp/skills/campaign.md."
    )


def build() -> Any:
    """Construct the server with every tool and resource registered."""

    from etalon.mcp import active, data, execution, governance, planning

    server_class = require_mcp()
    mcp = server_class("etalon")

    planning.register(mcp)
    governance.register(mcp)
    active.register(mcp)
    execution.register(mcp)
    data.register(mcp)

    @mcp.resource("etalon://skills/molquarry/{skill}")
    def molquarry_workflow(skill: str) -> str:
        """Pinned upstream workflows: target-modulators or compound-sourcing.

        Use ETALON's data tools for sealed acquisition and campaign ingress. These documents
        also describe advanced MolQuarry SDK workflows, not additional ETALON MCP tools.
        """
        from etalon.boundary.infra import asset_directory

        if skill not in {"target-modulators", "compound-sourcing"}:
            raise ValueError("unknown MolQuarry workflow")
        return (asset_directory() / "molquarry" / "skills" / ("molquarry-" + skill)
                / "SKILL.md").read_text(encoding="utf-8")

    @mcp.resource("etalon://skills/molquarry/{skill}/references/{reference}")
    def molquarry_reference(skill: str, reference: str) -> str:
        """The reference document linked by each pinned MolQuarry workflow."""
        from etalon.boundary.infra import asset_directory

        known = {"target-modulators": "workflow.md", "compound-sourcing": "results.md"}
        if known.get(skill) != reference:
            raise ValueError("unknown MolQuarry workflow reference")
        return (asset_directory() / "molquarry" / "skills" / ("molquarry-" + skill)
                / "references" / reference).read_text(encoding="utf-8")

    @mcp.resource(SKILL_RESOURCE)
    def campaign_workflow() -> str:
        """The complete CADD campaign workflow: the order, the decision points, and the refusals.

        Read this before calling any tool. It covers generation through relative free energy, says
        which steps are free and which spend GPU-days, and carries the measurements its decisions rest
        on -- because a workflow recalled without its numbers is a workflow whose refusals look
        arbitrary.
        """

        return _skill_path().read_text(encoding="utf-8")

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

    from etalon.mcp import active, data, execution, governance, planning

    found: dict[str, str] = {}

    class Collector:
        def tool(self) -> Any:
            def decorate(function: Any) -> Any:
                cost = getattr(function, "etalon_cost", Cost.SPENDS)
                found[function.__name__] = cost.value
                return function

            return decorate

    collector = Collector()
    planning.register(collector)
    governance.register(collector)
    active.register(collector)
    execution.register(collector)
    data.register(collector)
    return found


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
