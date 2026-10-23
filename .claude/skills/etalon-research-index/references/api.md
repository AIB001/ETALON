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
