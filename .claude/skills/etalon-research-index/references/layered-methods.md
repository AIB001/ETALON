# Retain the useful form of a method

A method can live as component code, a callable program, a runtime/parameter
configuration, an improved usage skill, or conditional scientific knowledge.
These are related forms, not compulsory stages or copies of one another.

Keep code in its component or task method directory. A mature builtin needs no
wrapper just to become a method record. Keep how to choose/tune in a skill;
keep task-specific findings and exceptions in memory. A failed expectation can
improve knowledge without producing an algorithm or a new skill.

## Publish, find and directly call an executable

For a host-written program using the existing `method.run` ABI, call
`etalon_method_publish(entry, resource_id, arguments, summary, conditions,
evidence=[...], counterevidence=[...], publication_id=...)`. `arguments` contains
`method_id`, absolute `command` argv, declared source/model `files`, default
`inputs` and optional `outputs` (names to relative paths). The program receives
`--request` and `--result`; request JSON contains inputs and resolved output paths.
Return `{"schema":"etalon-method-result/1","summary":"...","outputs":{...}}`
and optional structured `feedback`. Large arrays belong in declared output files.
Do not wrap a working component merely to register it; retain its direct API.

Publication merges one resource into the existing note and pins its sources.
Resource bodies are archived independently in the same journal. Progress notes
keep a compact index reference; changing a summary or handle does not republish
every method body. Legacy inline notes remain readable. Do not manage these index
files manually or copy them into SKILL.md.
Publication preserves other knowledge and contrary evidence. It does not claim that the
method improved science. Keep measured effects, unknowns and scope in the summary
and cited results. Local methods are useful without cross-target generalization.
Keep published source versions at stable paths; edit a new version instead of
overwriting a retained one. Hashes detect a changed file but cannot recover its
old bytes. The existing entry handoff copies these method sources with the note
and resolves them after relocation, without creating a second method registry.

Find it with `etalon_research_context(view="resources")` or
`research resources --entry ENTRY --kind method`. Follow the returned
`resolve` call, using its exact `content_ref` and `pointer`, then supply
`invocation={"request_id":"...","limits":{"wall_second":60},"inputs":{...}}`
to `etalon_method_resolve`. It returns a request for `etalon_action_submit`;
call that with the returned `plan_id` and an execution workspace. Immediately save
the returned handle with `etalon_research_progress` in the current task's
`pending_handles`, before waiting or doing another long activity. On completion,
link its result and review decision and clear only that pending handle. Reuse that handle
on recovery; do not create a new request ID for the same already-running work.
An optional invocation `executable` selects the actual interpreter on a new host;
source files still resolve through their original identities. Check applicability
before selecting the method; retrieval alone does not activate it.

CLI equivalents: `research method-save --entry ENTRY --resource-id NAME
--descriptor FILE --publication-id ID`, then `research method --entry ENTRY
--pointer /resources/NAME --invocation CALL.json`. The descriptor holds
arguments/summary/conditions and optional evidence/counterevidence paths. These
commands return JSON; use existing action submit/result commands for execution.

An ordinary progress note may declare a `resources` mapping, for example:

```json
{
  "resources": {
    "generation-window-analysis": {
      "kind": "method", "form": "executable",
      "summary": "Analyze native generation yield with explicit property windows",
      "entrypoint": "python -m etalon.generate.feedback",
      "parameters": {"manifests": "native PRISM manifest paths", "criteria": "chosen property min/max bounds"},
      "source": {"path": "/absolute/feedback.py", "identity": {"sha256": "...", "bytes": 1}},
      "links": ["generation-use", "yield-lesson"]
    },
    "generation-use": {
      "kind": "skill", "form": "guidance", "summary": "Select generation changes and downstream actions",
      "slug": "generation-planning", "links": ["generation-window-analysis"]
    },
    "yield-lesson": {
      "kind": "experience", "form": "conditional_judgment",
      "summary": "The observed size window explains part of this run's attrition",
      "conditions": {"target": "selected target", "protocol": "current property definition"},
      "links": ["generation-window-analysis"]
    }
  }
}
```

Use real source identities (`research.notes.original(path)`), not the illustrative
hash above. A configuration uses `kind=method, form=configuration` or
`form=runtime_profile`. `links` are local resource names within that pinned note;
cross-note evidence uses existing exact references. Link relevant support and
counterevidence in the note. Resource discovery does not establish applicability.

Catalog results provide exact note pointers, form and a bounded next read. Use
`research method --entry ENTRY --pointer /resources/NAME` to resolve a selected
source, or detail to read the chosen skill/knowledge. Invoke an existing component
directly or use `method.run` for a custom program. Do not load every resource body
or write another registry. Current heads change; exact selected notes stay pinned.

Retain a new skill only when the procedure for choosing/composing/tuning has
improved. Update an existing skill when sufficient. A fresh context should be
able to locate the method, apply the conditions and actually use it without an
author explaining hidden details. That is more meaningful than creation count.

## Select and learn without growing the active context

New inputs produce runs, parameter changes usually produce configurations, and
algorithm changes produce method versions. Declare an optional `family` and
`parts` when they clarify real reuse boundaries. Parts use the existing resource
contract: `part_id`, `conditions`, `requires`, `irrelevant`, `available_at`,
`dependencies`, and optional definition identities. Do not declare a condition
irrelevant merely to make a match. SND1 residue settings and a general geometry
kernel can be separate parts; a reusable kernel does not validate old thresholds
on another target or validate a whole unmodified invocation.

`research resources --entry ENTRY --query PROFILE.json --resource-id NAME`
or the MCP resources view with `view_options={query: PROFILE, resource_ids:[NAME]}`
returns compact declarations and associated feedback. Omit resource_ids for
broader discovery; `--family NAME` / `view_options.families` scopes a known
operation family without requiring exact method names. Broad note catalogs show
family navigation; unclassified records remain in the broad view. A profile contains `conditions` and optional
`critical_conditions`, `definitions`, `available_stages`; missing required facts
remain unknown. Known parts can be reused while other parts need adaptation or
a compatible template. Inspect the selected resource/feedback through its exact
`next_read` pointer; query matching alone is no claim of improved science.

When normal execution adds useful knowledge, use the existing progress tool:
`etalon_research_progress(entry, patch={}, resource_feedback={resource_id,
resource_version, part_ids, usage_id, conditions, outcome, summary, cost?},
evidence=[...], counterevidence=[...])`. CLI is
`research progress --entry ENTRY --resource-feedback FEEDBACK.json`.
`resource_version` is the catalog descriptor digest, not the changing note head;
`usage_id` names this observation and makes retries idempotent. Outcomes are
`positive`, `negative`, `unknown`, `failed`, `no_change`. Keep measured complete
cost units and unknown values distinct. For an older selected version, retain
its exact `resource_entry`. Feedback summarizes conditions and outcomes; it does
not change method defaults or establish benefits by assertion. Later success
does not erase a negative case. Reopen only when new information matters.

Mechanical deduplication/versioning happens at publication. Ask a side advisor
to consolidate a related set only when duplicate choices or unclear conditions
impede research. No full-library review, mandatory skill creation or routine
counter reset is needed. Keep one current choice in the task entry and reuse it
in a fresh context until the problem, resource version or pertinent feedback changes.
