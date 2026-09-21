"""MolQuarry evidence in the same tool surface as screening and active campaigns."""

from __future__ import annotations

from typing import Any

from etalon.data.artifacts import summary
from etalon.mcp._common import Cost, absolute_path, ok, threaded_tool, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_data_sources(source: str = "") -> str:
        """FREE. List database coverage, or describe a source's exact operation/parameter schemas.

        Discovery performs no HTTP query. Catalog presence does not imply public API access.
        """
        from etalon.boundary.quarry import describe

        return ok(**describe(source or None))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_data_plan(request: dict[str, Any], max_requests: int = 100,
                         max_bytes: int = 50_000_000, max_seconds: float = 300) -> str:
        """CHEAP. Validate query/collect/catalog/download/bundle/sourcing requests without HTTP.

        Return an input-hashed plan_id. Data allowances use requests/bytes, separate from GPU costs.
        """
        from etalon.boundary.quarry import DataBudget
        from etalon.data.service import plan_data

        return ok(**plan_data(request, budget=DataBudget(max_requests, max_bytes, max_seconds)))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_data_run(request: dict[str, Any], workspace: str, run_id: str,
                        max_requests: int = 100, max_bytes: int = 50_000_000,
                        max_seconds: float = 300, plan_id: str = "") -> str:
        """CHEAP. Run bounded MolQuarry acquisition and seal evidence, including partial coverage.

        Synchronous CPU/HTTP work; not a detached worker or a GPU action. Local input paths must
        be absolute. max_requests=0 forbids network. New run ids never overwrite previous evidence.
        Collection does not admit labels; sourcing does not establish stock or synthesis feasibility.
        """
        from etalon.boundary.quarry import DataBudget
        from etalon.data.service import run_data

        return ok(**summary(run_data(request, absolute_path(workspace, label="workspace"), run_id=run_id,
                            budget=DataBudget(max_requests, max_bytes, max_seconds),
                            expected_plan_id=plan_id or None)))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_data_status(snapshot: str, include_files: bool = False) -> str:
        """FREE. Verify a data snapshot and inspect coverage, usage and any interrupted run."""
        from etalon.data.artifacts import status

        return ok(**summary(status(absolute_path(snapshot, label="snapshot")), include_files=include_files))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_data_prepare(snapshot: str, workspace: str, run_id: str, id_field: str = "",
                            smiles_field: str = "", allow_partial: bool = False,
                            identity_policy: dict[str, Any] | None = None) -> str:
        """CHEAP. Export a frozen library.csv and auditable source-to-MolCascade identity map.

        Query/catalog records require explicit dotted id/smiles field mappings. Collect snapshots
        use their evidence inventory. Match identity_policy to the subsequent screen configuration.
        """
        from etalon.data.library import prepare_library

        return ok(**summary(prepare_library(absolute_path(snapshot, label="snapshot"),
            absolute_path(workspace, label="workspace"), run_id=run_id,
            id_field=id_field or None, smiles_field=smiles_field or None,
            allow_partial=allow_partial, identity_policy=identity_policy)))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_data_import_candidates(database: str, snapshot: str,
                                      candidate_ids: list[str] | None = None) -> str:
        """CHEAP. Add frozen molecular candidates, or an explicit subset, within the active pool limit."""
        from etalon.data.cli import existing_store
        from etalon.data.library import import_candidates

        return ok(**import_candidates(existing_store(absolute_path(database, label="database")),
                                      absolute_path(snapshot, label="snapshot"), candidate_ids=candidate_ids))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_data_review_template(snapshot: str, endpoint_id: str, protocol: str) -> str:
        """FREE. Draft an assay review with every measurement withheld until explicitly assessed."""
        from etalon.data.ingress import review_template

        return ok(review=review_template(absolute_path(snapshot, label="snapshot"),
                                        endpoint_id=endpoint_id, protocol=protocol))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_data_import_assays(database: str, snapshot: str, review: dict[str, Any]) -> str:
        """CHEAP. Atomically import reviewed exact assays with target/state/unit and duplicate checks.

        Requires a historical-only endpoint (queryable=false). Censored values stay outside the
        scalar GP. A supplied review records the reviewer's scientific assertions, not their proof.
        """
        from etalon.data.cli import existing_store
        from etalon.data.ingress import import_assays

        return ok(**import_assays(existing_store(absolute_path(database, label="database")),
                                  absolute_path(snapshot, label="snapshot"), review))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_data_attach_handoffs(database: str, workspace: str, artifact_id: str,
                                   rationale: str, candidate_ids: list[str] | None = None) -> str:
        """CHEAP. Attach a verified screening handoff to existing campaign chemical states.

        Geometry bindings are immutable and recorded between rounds. This grants no MD authority;
        PRISM execution still requires receptor-bound scientific preflight and spend authorization.
        """
        from etalon.data.cli import existing_store
        from etalon.data.ingress import attach_handoffs

        return ok(**attach_handoffs(existing_store(absolute_path(database, label="database")),
            absolute_path(workspace, label="workspace"), artifact_id,
            rationale=rationale, candidate_ids=candidate_ids))
