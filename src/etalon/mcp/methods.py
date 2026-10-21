"""Thin access to method provenance in the existing scientific journal."""
from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
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
