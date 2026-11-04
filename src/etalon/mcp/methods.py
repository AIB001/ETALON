"""Thin access to method provenance in the existing scientific journal."""
from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_resource_feedback(entry: str | dict, resource_id: str,
                                  query: dict | None = None,
                                  branch_id: str | None = None,
                                  resource_version: str | None = None,
                                  offset: int = 0, limit: int = 20,
                                  max_bytes: int = 8192) -> str:
        """FREE. Read conditional method-use feedback from one pinned note.

        Compare cases with query/current version; page with next_read. Costs,
        contrary observations and feedback on older versions remain visible.
        Counts cover returned cases only. No execution or adoption occurs.
        """
        from etalon.mcp.evidence import _entry_argument
        from etalon.research.resource_selection import read_resource_feedback
        return ok(**read_resource_feedback(_entry_argument(entry), resource_id,
            query=query, branch_id=branch_id, resource_version=resource_version,
            offset=offset, limit=limit, max_bytes=max_bytes))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_method_publish(entry: str, resource_id: str, arguments: dict,
                              summary: str, conditions: dict,
                              branch_id: str | None = None, publication_id: str | None = None,
                              evidence: list[str] | None = None,
                              counterevidence: list[str] | None = None, parts: list[dict] | None = None,
                              family: str | None = None, definitions: dict | None = None) -> str:
        """CHEAP. Save a callable method in the existing entry's resource catalog.

        arguments is the existing method.run contract (command argv, files,
        method_id, inputs, optional input_files/outputs/cwd). Pins declared files; preserves
        other resources and contrary evidence. No execution or adoption occurs.
        Programs accept --request/--result. Skills and judgments remain distinct.
        """
        from etalon.research.saved_methods import publish_method
        return ok(**publish_method(absolute_path(entry, label="entry"), resource_id, arguments,
            summary=summary, conditions=conditions, branch_id=branch_id,
            publication_id=publication_id, evidence=evidence or (), counterevidence=counterevidence or (),
            parts=parts, family=family, definitions=definitions))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_method_resolve(entry: str | dict, pointer: str, branch_id: str | None = None,
                              invocation: dict | None = None, max_bytes: int = 16384,
                              query: dict | None = None) -> str:
        """FREE. Read one catalog method; optionally return a ready action request.

        invocation={request_id, limits, inputs?, input_files?, executable?, max_seconds?}. Verifies source
        originals and uses ordinary action preflight; does not run or optimize.
        Send the returned request to etalon_action_submit with a workspace, then
        recover its handle. Use catalog content_ref to pin a historical version.
        etalon_method_call optionally combines resolution, submission and note bookkeeping.
        """
        from etalon.research.saved_methods import resolve_saved_method
        from etalon.mcp.evidence import _entry_argument
        return ok(**resolve_saved_method(_entry_argument(entry), pointer, branch_id=branch_id,
                   invocation=invocation, max_bytes=max_bytes, query=query))

    @mcp.tool()
    @tool(Cost.SPENDS)
    def etalon_method_call(entry: str, pointer: str, invocation: dict, workspace: str,
                           branch_id: str | None = None, query: dict | None = None) -> str:
        """SPENDS. Call a chosen saved method and remember its handle automatically.

        Optional convenience over resolve/submit. Uses the existing action budget
        and request identity; a repeated request recovers the same execution.
        Returns invocation_path for etalon_method_collect. No research graph,
        method evaluation or additional model call is required.
        """
        from etalon.research.saved_methods import call_saved_method
        return ok(**call_saved_method(absolute_path(entry, label="entry"), pointer,
            invocation=invocation, workspace=absolute_path(workspace, label="workspace"),
            branch_id=branch_id, query=query))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_method_collect(invocation_path: str) -> str:
        """CHEAP. Read an existing method call and save its result/cost references.

        Never submits or repeats computation. Updates only this call's pending
        handle and resource in its original note; preserves the host's judgment.
        An archival problem is reported separately from the execution outcome.
        output_summaries preserves the method's conclusion, compact feedback and
        real failure details even when bulk outputs require a separate read.
        """
        from etalon.research.saved_methods import collect_saved_method
        return ok(**collect_saved_method(absolute_path(invocation_path, label="invocation_path")))

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
