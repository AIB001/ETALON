"""Generate a self-contained local run report without embedding molecule rows."""

from __future__ import annotations

import base64
import hashlib
import html
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from molcascade.decisions import DecisionDigest, build_decision_digest
from molcascade.io.atomic import atomic_write_bytes
from molcascade.runtime import LocalRunner
from molcascade.ui.assets import png_data_uri

#: Reason codes carried per stage into the document.  Deliberately far below
#: :mod:`molcascade.decisions`'s own default: ``rd_filters_alerts`` mixes a rule
#: hash into its ``reason_code`` and the medchem adapters hash rule
#: *combinations*, so the number of distinct codes is a property of the library
#: being screened.  The CLI can afford that because ``--json`` is its escape
#: hatch; a single file somebody emails cannot, so the document fixes its own
#: size instead of inheriting one.  Overflow is not hidden -- it lands in
#: ``untracked_rows`` and the page says so.
_REPORT_BUCKETS = 24


def _asset(name: str) -> str:
    return files("molcascade.ui.assets").joinpath(name).read_text(encoding="utf-8")


def _csp_hash(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def _decision_summary(digest: DecisionDigest) -> dict[str, Any]:
    """Project a decision digest down to what this document is allowed to carry.

    Written as an explicit allow-list rather than ``as_dict()`` minus a key, so
    that a field added to :class:`~molcascade.decisions.DecisionBucket` later
    cannot arrive here by default.  Two things are withheld and each has its own
    reason.

    ``sample_entity_ids`` is dropped, and the digest is built with
    ``sample_size=0`` so it is never materialised in the first place.  A parent
    id is a one-way digest and leaks no structure, so this is not about secrecy:
    it is that ``molecule_rows_embedded`` is 0 and the page says every number
    here is an aggregate.  A list of sample ids is a per-molecule row projection
    -- and, worse, a join key, whose whole value is that it can be exchanged for
    a whole molecule through ``molcascade explain``.  The reader at a terminal
    has the store; the reader of an emailed file may have neither.

    ``unavailable`` is reduced to the exception's type name.  The digest's own
    message quotes the underlying error, which for a filesystem failure contains
    an absolute path from the machine that generated the report.
    """

    stages = []
    for stage in digest.stages_with_decisions:
        stages.append(
            {
                "stage_id": stage.stage_id,
                "rows_total": stage.rows_total,
                "untracked_rows": stage.untracked_rows,
                "unavailable": (
                    None
                    if stage.unavailable is None
                    else stage.unavailable.split(":", 1)[0]
                ),
                "outcome_totals": [
                    {"entity_kind": kind, "outcome": outcome, "rows": rows}
                    for kind, outcome, rows in stage.outcome_totals
                ],
                "buckets": [
                    {
                        "entity_kind": bucket.entity_kind,
                        "outcome": bucket.outcome,
                        "reason_code": bucket.reason_code,
                        "rows": bucket.rows,
                    }
                    for bucket in stage.buckets
                ],
            }
        )
    return {
        "rejected_rows": digest.rejected_rows,
        "warned_rows": digest.warned_rows,
        "reason_codes_per_stage": _REPORT_BUCKETS,
        "stages": stages,
    }


def build_run_report_payload(runner: LocalRunner, run_id: str) -> dict[str, Any]:
    """Collect control-plane summaries only; molecular datasets remain on disk."""

    state = runner.load_run(run_id)
    revision = runner._load_revision(state.revision_id)  # Internal first-party boundary.
    configured = {stage.id: stage for stage in revision.config.stages}
    stage_rows: list[dict[str, Any]] = []
    for stage in state.stages:
        config = configured[stage.stage_id]
        response_metadata: dict[str, Any] = {}
        row_counts: dict[str, Any] = {}
        outputs: list[dict[str, Any]] = []
        if stage.output_ref is not None:
            manifest = runner.store.verify(stage.output_ref.artifact_id)
            raw_response = manifest.metadata.get("response_metadata", {})
            raw_counts = manifest.metadata.get("row_counts", {})
            if isinstance(raw_response, dict):
                response_metadata = dict(raw_response)
            if isinstance(raw_counts, dict):
                row_counts = dict(raw_counts)
            # Source adapters use record-specific names to keep their own
            # provenance unambiguous.  Add report-only aliases so the same UI
            # can compare every stage without mutating the artifact manifest.
            aliases = {
                "input_count": "input_record_count",
                "output_count": "accepted_record_count",
                "reject_count": "rejected_record_count",
                "warning_count": "warning_record_count",
            }
            for generic, source_name in aliases.items():
                if generic not in response_metadata and source_name in response_metadata:
                    response_metadata[generic] = response_metadata[source_name]
            outputs = [output.model_dump(mode="json") for output in manifest.outputs]
        stage_rows.append(
            {
                "stage_id": stage.stage_id,
                "slot": config.slot,
                "plugin_key": stage.plugin_key,
                "status": stage.status.value,
                "attempts": stage.attempts,
                "started_at": None if stage.started_at is None else stage.started_at.isoformat(),
                "finished_at": None if stage.finished_at is None else stage.finished_at.isoformat(),
                "artifact_id": None if stage.output_ref is None else stage.output_ref.artifact_id,
                "response_metadata": response_metadata,
                "row_counts": row_counts,
                "outputs": outputs,
                "error": None if stage.error is None else stage.error.model_dump(mode="json"),
            }
        )
    return {
        # Bumped for the "decisions" key below.  The payload is a published
        # artifact -- it is serialised into the document's textarea, saved and
        # diffed -- so a consumer needs to be able to tell the shapes apart
        # without probing for keys.
        "report_schema_version": 2,
        "run": state.model_dump(mode="json"),
        "pipeline": revision.config.model_dump(mode="json"),
        "stages": stage_rows,
        # ``sample_size=0`` is where ``molecule_rows_embedded`` below is
        # enforced rather than merely asserted: the digest never materialises an
        # entity id, so the projection has nothing to drop.  See
        # :func:`_decision_summary`.
        "decisions": _decision_summary(
            build_decision_digest(
                runner, run_id, sample_size=0, max_buckets=_REPORT_BUCKETS
            )
        ),
        "next_config_filename": f"{state.run_id}-next-config.json",
        "molecule_rows_embedded": 0,
    }


def render_run_report(payload: dict[str, Any]) -> str:
    css = _asset("report.css")
    javascript = _asset("report.js")
    mark = png_data_uri("molcascade-mark.png")
    payload_text = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    escaped = html.escape(payload_text, quote=True)
    csp = (
        "default-src 'none'; "
        f"script-src '{_csp_hash(javascript)}'; "
        f"style-src '{_csp_hash(css)}'; "
        "img-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    run = payload["run"]
    run_id = html.escape(str(run["run_id"]), quote=True)
    revision = html.escape(str(run["revision_id"]), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}">
  <title>MolCascade run {run_id}</title>
  <style>{css}</style>
</head>
<body>
  <header class="hero"><div class="hero-row"><div class="hero-brand"><img class="brand-mark" src="{mark}" alt="" width="189" height="120"><div><div class="eyebrow">MolCascade local screening report</div><h1>Run {run_id}</h1><p>revision {revision}</p></div></div><div class="actions"><button id="copy-command" class="button ghost" type="button">Copy next-run command</button><button id="download-next" class="button" type="button">Download next configuration</button></div></div></header>
  <main class="content">
    <section id="summary" class="summary-grid" aria-label="Run summary"></section>
    <div class="section-head"><div><h2>Stage audit</h2><p>This offline report contains aggregate counts and provenance; molecule rows remain on disk.</p></div></div>
    <section id="timeline" class="timeline"></section>
    <div class="section-head"><div><h2>Why molecules left</h2><p>Read back from the reason code each gate recorded while the run executed. These are decision <em>rows</em>, not molecules: one molecule can fail two rules in one stage and is counted twice, so the per-stage counts above stay the ones to quote. Entity ids stay on disk &mdash; run <code>molcascade decisions &lt;run&gt;</code> for samples, and <code>molcascade explain</code> to follow one molecule.</p></div></div>
    <section id="decisions" class="timeline"></section>
    <div class="section-head"><div><h2>Continue screening</h2><p>Adjust stage parameters and download JSON; the CLI will validate it strictly and create a new revision.</p></div></div>
    <section class="panel config-grid"><div><textarea id="next-config" spellcheck="false" aria-label="Next-run configuration"></textarea><div id="config-notice" class="notice" aria-live="polite"></div></div><aside class="guide"><strong>Safety boundary</strong><p>This page is fully offline. It cannot publish artifacts or execute commands, and it cannot restore a terminal hard rejection.</p><p>Run <code>molcascade validate &lt;config&gt;</code> first, followed by <code>molcascade run</code> or the compatible <code>molcascade --vs</code> command.</p><button id="restore-config" class="button" type="button">Restore this run's pipeline</button></aside></section>
  </main>
  <textarea id="report-payload" hidden>{escaped}</textarea>
  <script>{javascript}</script>
</body>
</html>
"""


def generate_run_report(
    runner: LocalRunner,
    run_id: str,
    output: str | Path,
    *,
    overwrite: bool = True,
) -> Path:
    destination = Path(output).expanduser()
    if destination.suffix.casefold() not in {".html", ".htm"}:
        raise ValueError("run report output must end in .html or .htm")
    document = render_run_report(build_run_report_payload(runner, run_id)).encode("utf-8")
    return atomic_write_bytes(destination, document, overwrite=overwrite)


__all__ = ["build_run_report_payload", "generate_run_report", "render_run_report"]
