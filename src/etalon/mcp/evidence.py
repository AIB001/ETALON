"""Read-only evidence access; validation and paging belong to the shared SDK."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from etalon.mcp._common import Cost, absolute_path, ok, tool


def _entry_argument(entry):
    if isinstance(entry, str):
        return absolute_path(entry, label="entry")
    if isinstance(entry, dict):
        for key in ("entry", "workspace"):
            if key in entry:
                absolute_path(entry[key], label=key)
    return entry


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_context(entry: str | dict, node_id: str | None = None,
                                question: Annotated[str | None, Field(max_length=4096)] = None,
                                purpose: str = "research", policy: dict | None = None,
                                max_bytes: Annotated[int, Field(ge=4096, le=65536)] = 16384,
                                conditions: dict | None = None,
                                view: Literal["summary", "node", "full"] = "full") -> str:
        """FREE. Build bounded decision context from a published research entry.

        Includes source selection, counterevidence coverage and existing branch/pause
        state. Policy can narrow the published scope; it is not an authorization grant.
        Does not write memory, submit science, adopt a method or trigger RSI.
        summary is a compact continuation card; node requires node_id and follows
        explicit dependencies; full preserves the original bounded graph selection.
        max_bytes is 4096..65536. Expand returned refs with evidence_read; question
        is descriptive and does not perform semantic ranking.
        entry may be a server path, a one-hop {entry: server_path} locator, or the
        returned read_context for a pinned view. Follow-up evidence calls accept
        read_context directly; never guess a workspace or open server files locally.
        """
        from etalon.research.research_context import build_research_context
        return ok(**build_research_context(_entry_argument(entry),
            node_id=node_id, question=question, purpose=purpose, policy=policy,
            max_bytes=max_bytes, conditions=conditions, view=view))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_graph(workspace: str, checkpoint_ref: dict,
                              purpose: str = "research", policy: dict | None = None,
                              max_nodes: Annotated[int, Field(ge=1, le=128)] = 64,
                              max_bytes: Annotated[int, Field(ge=4096, le=262144)] = 65536) -> str:
        """FREE. Project Search/Judge/Quick/Deep from existing research records.

        Nodes keep evidence identity and cross-workspace references; failed and skipped
        branches remain visible. A historical projection is not a new execution trace.
        Bounds: 1..128 nodes, 4096..262144 serialized bytes.
        """
        from etalon.research.graph_memory import build_research_graph
        return ok(**build_research_graph(absolute_path(workspace, label="workspace"),
            checkpoint_ref=checkpoint_ref, purpose=purpose, policy=policy,
            max_nodes=max_nodes, max_bytes=max_bytes))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_evidence_query(workspace: str | None = None, query: Annotated[str, Field(max_length=4096)] = "",
                              filters: dict | None = None,
                              limit: Annotated[int, Field(ge=1, le=50)] = 10,
                              cursor: str | None = None, entry: str | dict | None = None,
                              read_context: dict | None = None) -> str:
        """FREE. Query an explicit released-evidence manifest; no database is created.

        Reading known refs directly is also allowed. Coverage and warnings distinguish
        missing sources from no matches. This does not dispatch or select a method.
        limit is 1..50 records per page; use next_cursor when present.
        Supply exactly one of workspace, entry or the returned read_context.
        read_context preserves the selected scope and checkpoint across remote reads.
        """
        from etalon.research.evidence import query_evidence

        return ok(**query_evidence(absolute_path(workspace, label="workspace") if workspace is not None else None,
                                  query=query, filters=filters, limit=limit, cursor=cursor,
                                  entry=_entry_argument(entry), read_context=read_context))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_evidence_read(workspace: str | None = None, ref: dict | None = None,
                             view: Literal["record", "summary", "lineage", "artifact"] = "record",
                             member: str | None = None, selector: str | None = None,
                             offset: Annotated[int, Field(ge=0, le=2**63 - 1)] = 0,
                             limit: Annotated[int, Field(ge=1, le=32768)] = 100,
                             entry: str | dict | None = None, read_context: dict | None = None) -> str:
        """FREE. Read a known evidence ref with bounded record/summary/lineage/artifact views.

        Metadata access is not original-byte verification. Original identities, roles,
        and costs are retained; no model, network or scientific execution is started.
        limit is 1..32768; artifact offset/limit are byte counts. Page with the
        returned fragment.next_offset rather than requesting a larger limit.
        Supply ref and exactly one of workspace, entry or the returned read_context.
        Prefer read_context after research_context, including on a remote server.
        """
        from etalon.research.evidence import read_evidence

        return ok(**read_evidence(absolute_path(workspace, label="workspace") if workspace is not None else None, ref,
                                 view=view, member=member, selector=selector,
                                 offset=offset, limit=limit, entry=_entry_argument(entry), read_context=read_context))
