"""Read-only evidence access; validation and paging belong to the shared SDK."""
from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_context(entry: str, node_id: str | None = None,
                                question: str | None = None, purpose: str = "research",
                                policy: dict | None = None, max_bytes: int = 16384,
                                conditions: dict | None = None) -> str:
        """FREE. Build bounded decision context from a published research entry.

        Includes source selection, counterevidence coverage and existing branch/pause
        state. Policy can narrow the published scope; it is not an authorization grant.
        Does not write memory, submit science, adopt a method or trigger RSI.
        """
        from etalon.research.research_context import build_research_context
        return ok(**build_research_context(absolute_path(entry, label="entry"),
            node_id=node_id, question=question, purpose=purpose, policy=policy,
            max_bytes=max_bytes, conditions=conditions))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_graph(workspace: str, checkpoint_ref: dict,
                              purpose: str = "research", policy: dict | None = None,
                              max_nodes: int = 64, max_bytes: int = 65536) -> str:
        """FREE. Project Search/Judge/Quick/Deep from existing research records.

        Nodes keep evidence identity and cross-workspace references; failed and skipped
        branches remain visible. A historical projection is not a new execution trace.
        """
        from etalon.research.graph_memory import build_research_graph
        return ok(**build_research_graph(absolute_path(workspace, label="workspace"),
            checkpoint_ref=checkpoint_ref, purpose=purpose, policy=policy,
            max_nodes=max_nodes, max_bytes=max_bytes))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_evidence_query(workspace: str, query: str = "",
                              filters: dict | None = None, limit: int = 10,
                              cursor: str | None = None) -> str:
        """FREE. Query an explicit released-evidence manifest; no database is created.

        Reading known refs directly is also allowed. Coverage and warnings distinguish
        missing sources from no matches. This does not dispatch or select a method.
        """
        from etalon.research.evidence import query_evidence

        return ok(**query_evidence(absolute_path(workspace, label="workspace"),
                                  query=query, filters=filters, limit=limit, cursor=cursor))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_evidence_read(workspace: str, ref: dict, view: str = "record",
                             member: str | None = None, selector: str | None = None,
                             offset: int = 0, limit: int = 100) -> str:
        """FREE. Read a known evidence ref with bounded record/summary/lineage/artifact views.

        Metadata access is not original-byte verification. Original identities, roles,
        and costs are retained; no model, network or scientific execution is started.
        """
        from etalon.research.evidence import read_evidence

        return ok(**read_evidence(absolute_path(workspace, label="workspace"), ref,
                                 view=view, member=member, selector=selector,
                                 offset=offset, limit=limit))
