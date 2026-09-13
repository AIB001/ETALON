"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("report-payload").value);
  const run = payload.run;
  const stages = payload.stages;
  const text = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  };
  const metric = (label, value) => {
    const node = text("div", "metric", "");
    node.append(text("span", "", label), text("strong", "", String(value)));
    return node;
  };
  const summary = document.getElementById("summary");
  const totalInput = stages.map((stage) => stage.response_metadata.input_count).find((v) => Number.isInteger(v));
  const finalOutput = [...stages].reverse().map((stage) => stage.response_metadata.output_count).find((v) => Number.isInteger(v));
  summary.append(
    metric("Run status", run.status),
    metric("Enabled stages", stages.length),
    metric("Initial input", totalInput ?? "—"),
    metric("Final output", finalOutput ?? "—"),
  );

  const timeline = document.getElementById("timeline");
  stages.forEach((stage, index) => {
    const card = text("article", "stage-card", "");
    const number = text("div", "stage-number", String(index + 1));
    const body = text("div", "", "");
    body.append(
      text("h3", "", `${stage.stage_id} · ${stage.slot}`),
      text("div", "stage-subtitle", stage.plugin_key),
    );
    const facts = text("div", "stage-facts", "");
    const metadata = stage.response_metadata || {};
    const rows = stage.row_counts || {};
    const values = [
      ["attempts", stage.attempts],
      ["input", metadata.input_count],
      ["output", metadata.output_count],
      ["reject", metadata.reject_count],
      ["clusters", metadata.cluster_count],
      ["cache", stage.status === "CACHED" ? "hit" : undefined],
    ];
    values.forEach(([label, value]) => {
      if (value !== undefined && value !== null) facts.append(text("span", "pill", `${label}: ${value}`));
    });
    Object.entries(rows).forEach(([port, count]) => facts.append(text("span", "pill", `${port}: ${count} rows`)));
    body.append(facts);
    if (stage.error) body.append(text("div", "error", `${stage.error.code}: ${stage.error.message}`));
    const status = text("div", `status ${stage.status.toLowerCase()}`, stage.status);
    card.append(number, body, status);
    timeline.append(card);
  });

  const editor = document.getElementById("next-config");
  const notice = document.getElementById("config-notice");
  const next = structuredClone(payload.pipeline);
  next.metadata = next.metadata || {};
  next.metadata.continued_from_run_id = run.run_id;
  next.metadata.continued_from_revision_id = run.revision_id;
  editor.value = JSON.stringify(next, null, 2);

  const validatedText = () => {
    try {
      const value = JSON.parse(editor.value);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("The root value must be an object");
      if (!Array.isArray(value.stages) || !value.stages.length) throw new Error("At least one stage is required");
      notice.className = "notice ok";
      notice.textContent = "Browser JSON checks passed; the CLI still performs authoritative schema and plugin validation.";
      return JSON.stringify(value, null, 2) + "\n";
    } catch (error) {
      notice.className = "notice bad";
      notice.textContent = `Invalid configuration: ${error.message}`;
      return null;
    }
  };
  editor.addEventListener("input", validatedText);
  validatedText();

  const download = (content, filename) => {
    const url = URL.createObjectURL(new Blob([content], {type: "application/json;charset=utf-8"}));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  document.getElementById("download-next").addEventListener("click", () => {
    const content = validatedText();
    if (content) download(content, payload.next_config_filename);
  });
  document.getElementById("restore-config").addEventListener("click", () => {
    editor.value = JSON.stringify(next, null, 2);
    validatedText();
  });
  document.getElementById("copy-command").addEventListener("click", async () => {
    const command = `molcascade run ${payload.next_config_filename} --workspace .molcascade`;
    try {
      await navigator.clipboard.writeText(command);
      notice.className = "notice ok";
      notice.textContent = `Copied: ${command}`;
    } catch (_error) {
      notice.className = "notice bad";
      notice.textContent = `Copy manually: ${command}`;
    }
  });

  /* Why molecules left.  Placed last on purpose: this is diagnostic rendering,
     and the IIFE aborts as a unit, so a throw here must not be able to leave
     the download, restore and copy buttons unwired. */
  const decisionsRoot = document.getElementById("decisions");
  const decisions = payload.decisions;
  const count = (value) => Number(value).toLocaleString("en-US");
  if (!decisions || !Array.isArray(decisions.stages)) {
    decisionsRoot.append(
      text("p", "empty", "This report predates the decision summary."),
    );
  } else if (!decisions.stages.length) {
    decisionsRoot.append(
      text("p", "empty", "No stage in this run published a decision dataset."),
    );
  } else {
    decisions.stages.forEach((stage) => {
      const card = text("article", "reason-card", "");
      card.append(text("h3", "", stage.stage_id));
      if (stage.unavailable) {
        card.append(text("p", "caveat", `Unreadable: ${stage.unavailable}`));
        decisionsRoot.append(card);
        return;
      }
      const facts = text("div", "stage-facts", "");
      (stage.outcome_totals || []).forEach((total) => {
        const pill = text(
          "span",
          `pill ${String(total.outcome).toLowerCase()}`,
          `${total.entity_kind} ${total.outcome}: ${count(total.rows)}`,
        );
        facts.append(pill);
      });
      card.append(facts);
      /* PASS buckets are the bulk of any healthy run and their totals are
         already on the line above; itemising them would bury the two outcomes
         somebody opened this section for. */
      const removals = (stage.buckets || []).filter(
        (bucket) => bucket.outcome === "REJECT" || bucket.outcome === "WARN",
      );
      if (removals.length) {
        const table = text("table", "reason-table", "");
        removals.forEach((bucket) => {
          const row = text("tr", "", "");
          row.append(
            text("td", `outcome ${bucket.outcome.toLowerCase()}`, bucket.outcome),
            text("td", "reason-code", bucket.reason_code),
            text("td", "reason-kind", bucket.entity_kind),
            text("td", "reason-rows", count(bucket.rows)),
          );
          table.append(row);
        });
        card.append(table);
      } else if (stage.rows_total) {
        card.append(text("p", "caveat", "Nothing was removed or warned about here."));
      }
      if (stage.untracked_rows) {
        card.append(
          text(
            "p",
            "caveat",
            `${count(stage.untracked_rows)} further row(s) fell outside the ` +
              `${count(decisions.reason_codes_per_stage)} reason codes this report carries; ` +
              "the totals above still count them. Run molcascade decisions for the full table.",
          ),
        );
      }
      decisionsRoot.append(card);
    });
  }
})();
