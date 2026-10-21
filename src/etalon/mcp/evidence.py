"""Read-only evidence access; validation and paging belong to the shared SDK."""
from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
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
