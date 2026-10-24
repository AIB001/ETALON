# Optional SDK contracts

Import `etalon.research.meta_context`. These helpers neither call an LLM nor
dispatch scientific work. The first implementation packs profile, index and
review metadata in one bounded workset Artifact. Original evidence stays referenced.

```python
profile = make_task_profile(
    task_id=task_id, question=question,
    conditions={"target": target, "objective": "pose_quality"},
    critical_conditions=["target", "objective"], available_stages=["input"])
```

Unknown values are `None`. A resource part has exactly:

```python
part = {
    "part_id": "SKILL.md",  # semantic part names for non-skill resources
    "conditions": {}, "requires": [],
    "irrelevant": ["target", "objective"],
    "available_at": "input", "dependencies": [],
}
```

Only declare irrelevance where the capability actually does not depend on that
dimension. Dependencies are `{resource_id, part_id}` pairs and must be enabled.
For skills use `content_ref={slug, manifest_sha256}` from `skills.snapshot`;
optional `bundle_ref={root, manifest_sha256}` pins an external complete bundle.
Other resources use existing exact evidence refs. `kind` is skill, method,
workflow, claim, evidence, experience, verifier or template.

`describe_resource(resource_id=..., kind=..., content_ref=..., summary=...,
parts=[...], support_refs=[...], counter_refs=[...], visibility="research")`
creates a descriptor. `index_resources(resources, workspace=restricted_workspace)`
checks original identities. Purpose restrictions must be applied to the workspace
before indexing. Evaluation-only descriptors are omitted from research indexes.

`prepare_adaptation_context(profile, index)` returns per-part condition checks.
Create decisions with `resource_id`, `part_id`, `disposition`, `reason`,
`reconsider_when`, then call
`review_adaptation(profile, index, decisions=decisions, reviewer=method_identity,
rationale=reason)`. Account for every considered part; excluded resources remain
auditable. These records are host-authored judgments, not independently validated
scientific applicability.

`build_task_workset(profile, index, review, template={resource_id, part_id},
mode="selected", parent_workset_ref=None)` freezes selection. For fallback use
`mode="template", fallback_reason=...`; only the template and its declared
dependency closure may be enabled. `adapt` and `reference` are not enabled parts.

`publish_task_workset(entry, workset, operation_id=stable_id, directory=new_dir,
expected_entry_identity=file_identity(entry))` returns a publication intent.
For a new destination in the same task supply `parent_entry` and expected identity
`None`. A different task needs its own initial checkpoint. Call the existing
`resume_publication(intent_path)` and verify with `read_task_workset(workspace,
checkpoint_ref)`. Repeat the same operation and directory to recover; do not invent
a new operation ID to conceal an unresolved publication.

`host.export(new_directory, task_workset=workset)` exports only enabled skill
members. Include `SKILL.md` explicitly, plus all referenced members needed for
that use. Omitted members are physically absent. Old `host.export` calls retain
full exports. Partial skill content may still contain prose mentioning other
parts; member selection does not redact paragraphs or erase host memory.

All meta payloads are limited to 32 KiB; indexes hold at most 64 resources. Reduce
the explicit scope or publish smaller task-specific metadata instead of silently
truncating required conditions. Missing originals, changed versions, conflicts or
incomplete evidence are errors to report or investigate, never implicit adoption.

## Resource use and subsequent selection

Import `etalon.research.resource_usage` when exact workset-to-use attribution is
needed. These helpers keep existing MethodUse and scientific records intact:

```python
payload = build_resource_usage(
    task_id=task_id, workset_ref=workset_ref,
    resource_id=resource_id, part_id=part_id,
    method_ref=method_ref, use_ref=use_ref, node_ref=node_ref,
    evaluation_ref=evaluation_step_ref, selection_ref=decision_ref,
    provenance={"mode": "retrospective", "note": reason,
                "source_refs": original_refs})
artifact_id = record_resource_usage(
    journal, payload, directory, source_id=source_id,
    source_kind="existing_real_artifact", workspace=restricted_workspace)
result = read_resource_usage(published_workspace, artifact_ref)
```

Optional refs default to `None`; absent phases and edges stay unknown.
`evaluation_ref` identifies the evaluation **step** with explicit node/use
attribution, rather than only MethodEvaluation. The decision must cite that
evaluation directly or through a matching existing method experience. Optional
`checkpoint_ref` verifies activation at that checkpoint. Leaving it out records
a snapshot association, not current activation.

Default `observations` separately sets `read`, `consumed`, and `executed` to
`{"status": "unknown", "evidence_refs": [], "note": ...}`. Contemporaneous host
reports may use `host_reported` with exact evidence refs. MethodUse must bind
`host_conditions.task_workset_ref` and name the exact `used_parts`; the report
cites that use. Executed reports also cite their node and require a receipt.
This remains a host report, not mechanical proof. Retrospective phase reports
cannot be upgraded from unknown. Returned `resource_read` describes current
verification, not historical delivery or comprehension. `chain_complete` only
verifies explicit associations.

The connected MCP names are `etalon_resource_usage_record(database, record,
directory, source_id, source_kind, workspace=None)` and
`etalon_resource_usage_read(ref, workspace=None, entry=None, read_context=None)`.
Use the entry's existing `read_context` to preserve its source restrictions. The
writer uses the normal configured Journal and appends one diagnostic Artifact.

## Bounded resource and branch discovery

Reuse `etalon_research_context(entry, view="resources", limit=20, cursor=None,
max_bytes=65536, view_options={...})`. Allowed options are `source_ids`, `kinds`,
`skill_refs` and `scan_limit`. Skill refs are explicit installed slugs or pinned
`{slug, manifest_sha256, bundle_ref}` selections. The inventory preserves original
refs, declared parts, supporting and opposing evidence; bare resources keep
unknown applicability. It never creates an enabled workset. An empty page with
a cursor is incomplete discovery, not absence of resources. Use the same options
when paging; source or version changes invalidate old cursors.

`view="branch"` additionally requires an exact checkpoint `branch_id`. Its
`view_options` accepts `usage_refs`, `max_nodes` and `as_of`. `conditions` supports
an explicitly changed task question. Read `task_workset`, usage observations,
experience categories, relations and coverage together. The usage-discovery
cursor belongs to `etalon_evidence_query`, separately from the branch view's
delivery cursor. Follow it if needed and supply additional exact `usage_refs`.
Task-wide enabled parts are not inferred to have been used on every branch.

Python equivalents are `research.resource_catalog.build_resource_catalog` and
`research.branch_context.build_branch_context`. Both are read-only. Detailed
schemas and counterexamples are in the source distribution's
`docs/thin-harness/RESOURCE_CATALOG.md` and `BRANCH_RESOURCE_VIEW.md`.
