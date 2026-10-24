"""Thin access to method provenance in the existing scientific journal."""
from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_method_evaluate(contract: dict | None = None, contract_ref: dict | None = None,
                               workspace: str | None = None, entry: str | dict | None = None,
                               read_context: dict | None = None) -> str:
        """FREE. Replay optional fixed evaluation rules over verified original results.

        Supply contract xor exact contract_ref, and one workspace/entry/read_context.
        Separate regression, retention, funnel and cost. Missing inputs and unequal
        protocol/check coverage cannot pass. No science, adoption, activation or
        memory write occurs; retrospective replay is not independent validation.
        """
        from etalon.mcp.evidence import _entry_argument
        from etalon.research.evaluation_contract import evaluate_contract
        return ok(**evaluate_contract(
            absolute_path(workspace, label="workspace") if workspace is not None else None,
            contract, contract_ref=contract_ref, entry=_entry_argument(entry),
            read_context=read_context))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_resource_usage_record(database: str, record: dict[str, Any], directory: str,
                                    source_id: str, source_kind: str,
                                    workspace: str | None = None) -> str:
        """CHEAP. Append validated resource-use attribution as an ordinary Artifact.

        The payload is built with research.resource_usage.build_resource_usage.
        Exact workset/resource part, method, use, node and evaluation references
        are verified before recording. Historical links cannot manufacture past
        consumption. Does not select a branch, run science or activate a skill.
        """
        from etalon.active.store import CampaignStore
        from etalon.research.journal import ResearchJournal
        from etalon.research.resource_usage import record_resource_usage, validate_resource_usage
        payload = validate_resource_usage(record)
        database = absolute_path(database, label="database")
        directory = absolute_path(directory, label="directory")
        workspace = absolute_path(workspace, label="workspace") if workspace is not None else None
        journal = ResearchJournal(CampaignStore(database))
        identifier = record_resource_usage(journal, payload, directory, source_id=source_id,
                                           source_kind=source_kind, workspace=workspace)
        return ok(record_id=identifier, record=journal.get(identifier))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_method_record(database: str, record: dict[str, Any]) -> str:
        """CHEAP. Append a validated method version, use or evaluation to the existing journal.

        Saving provenance does not execute a method, admit an observation or establish improvement.
        """
        from etalon.active.store import CampaignStore
        from etalon.research.journal import ResearchJournal
        from etalon.research.schema import method_record_from_dict

        value = method_record_from_dict(record)
        journal = ResearchJournal(CampaignStore(absolute_path(database, label="database")))
        identifier = journal.record(value)
        return ok(record_id=identifier, record=journal.get(identifier))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_method_read(database: str, record_id: str) -> str:
        """FREE. Read an immutable method record without inferring actual use or scientific benefit."""
        from etalon.active.store import CampaignStore
        from etalon.research.journal import ResearchJournal
        from etalon.research.schema import method_record_from_dict

        store = CampaignStore(absolute_path(database, label="database"), read_only=True)
        record = ResearchJournal(store).get(record_id)
        method_record_from_dict(record)
        return ok(record_id=record_id, record=record)
