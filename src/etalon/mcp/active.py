"""Read-only learning-state inspection and bounded offline replay for agent clients."""

from __future__ import annotations

import json
from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_active_status(database: str) -> str:
        """FREE. Read a persistent learning journal: budget, admitted labels, rounds and pending jobs.

        Does not create a database, resolve a job, change a policy, or launch any calculation.
        A pending job needs its real result and actual cost before execution can resume.
        """
        from etalon.active.store import CampaignStore

        return ok(state=CampaignStore(absolute_path(database, label="database"), read_only=True).status())

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_active_plan(database: str) -> str:
        """CHEAP. Fit the small-data surrogate and propose the next batch without running or reserving it.

        Returns the learned endpoint correlations, model hash, uncertainty, quality discount and
        cost quote for each action. Read-only; not evidence that these calculations were executed.
        """
        from etalon.active.runner import ActiveCampaign
        from etalon.active.store import CampaignStore

        store = CampaignStore(absolute_path(database, label="database"), read_only=True)
        return ok(**ActiveCampaign(store).inspect())

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_active_replay(manifest_path: str, database: str, rounds: int = 1) -> str:
        """CHEAP. Run 1–100 rounds against an explicitly supplied OFFLINE oracle table, then persist results.

        Writes the campaign journal. Never launches docking/MD/FEP, grants waivers, or imports
        arbitrary executors. A replay result verifies orchestration, NOT prospective CADD success.
        """
        from etalon.active.replay import from_manifest

        if type(rounds) is not int or not 1 <= rounds <= 100:
            raise ValueError("rounds must be between 1 and 100")
        manifest = json.loads(absolute_path(manifest_path, label="manifest_path").read_text(encoding="utf-8"))
        campaign = from_manifest(manifest, absolute_path(database, label="database"))
        return ok(**campaign.run(max_rounds=rounds))
