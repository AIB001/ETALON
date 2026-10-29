---
name: etalon-install
description: Set up or diagnose ETALON host access and selected scientific backends, checking interpreter paths, vendored sources and platform limits.
---

# Set up only the capabilities needed

Use this capability for installation, missing dependencies or shadowed imports.
Start from the intended task; a read-only MCP query does not require every GPU
engine, model weight or simulation package.

For selected PRISM generation models, use [runtime preparation](references/prism-runtime.md).
Deliver a callable runtime profile, not just installation prose. Reuse compatible
environments and shared weights; keep setup strategy here, concrete profiles in
task resources, and conditional operating lessons in memory.

- Use an isolated environment. The MCP entrypoint is `python -m etalon.mcp`;
  install the `mcp` extra for that server and only the extras needed by the task.
- Bind the actual interpreter and source location. `etalon_capabilities` reports
  the server's interpreter, source and skill digests. Match those to the files
  being developed instead of inferring a version from Git HEAD alone.
- `python -m etalon.host export --target NEW_DIRECTORY --host codex` (or `claude`)
  exports native skill folders, a short role and a project MCP configuration from
  the current installation. The destination must not exist. The generated manifest
  binds skill bundles and usage guidance separately; `python -m etalon.host verify`
  checks a previously exported directory. Configuration is not proof of host access.
- Check actual host discovery and a read-only MCP call. Report an SDK handshake
  separately from a native Codex or Claude Code invocation.
- For portable research hooks, use `python -m etalon.host install --target NEW_DIRECTORY
  --host codex --research-entry /absolute/CURRENT.json` (or `--host claude`).
  This creates native MCP hook definitions and an explicit server-side binding.
  After moving an unchanged owned export, repeat install with the destination
  runtime and entry. Retry interrupted installation with the same request;
  preserve its intent and previous bytes. Arbitrary existing projects and modified
  owned files require reconciliation. `host doctor --target DIRECTORY` checks
  configuration, while the actual host still owns project/hook review.
- On first continuation, MCP may not yet be available for SessionStart. Give the
  chat its entry and verify the first ETALON read's PreToolUse delivery. For
  compaction, require a real host compaction event plus subsequent entry reading;
  a constructed event or saved hook receipt alone is insufficient. Hook bindings
  do not copy remote originals or authorize paused computation.
- Durable workers require Linux `/proc` and POSIX locks; use Linux/WSL for them.
  Windows can serve read-only tools but does not establish worker compatibility.
- Preserve vendored source identity and inspect `etalon doctor` and asset
  verification results before relying on scientific backends. Do not install
  shadow copies of MolCascade, MolQuarry or PRISM or change an active job's environment.

For exact infrastructure/doctor/device SDK calls and full-library host export,
read [local diagnosis and export](references/local-diagnostics.md).

Read [historical backend installation notes](references/legacy-workflow.md) only
for the backend or hardware in question. Its absolute paths, CUDA versions,
validation counts and large environment layout describe a previous machine.
