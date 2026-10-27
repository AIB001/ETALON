# Selected installation

`skills.snapshot("research-context")` reads one exact version; it is not a
standalone host installation. Use the existing host exporter or installer for
selected skills and their required file dependencies:

```python
from etalon import host

host.export(new_directory, selected_skills=["research-context"])
# With an explicit entry and a new portable host directory:
host.install(new_portable_directory, research_entry=entry,
             selected_skills=["research-context"])
```

The equivalent CLI options are `etalon.host export --skills research-context`
and `etalon.host install --skills research-context`, with their normal target and
entry arguments. This selection exports only context. Decision reasoning and
other optional skills are not included; required dependency closure remains
available for bundles whose capabilities actually depend on another bundle's files.
Omitting `--skills` preserves the legacy all-skills export; supplying `--skills`
with no names exports no skills. Rebinding a portable project keeps its saved
selection; choose a new adapter directory for a different selection.

If an optional skill is useful but absent, request its exact member through
`etalon_skill_read(slug="research-decision", member="SKILL.md")`, then only the
relevant reference. A missing tool or member is an explicit unavailable option;
use the current task's available methods without a whole-system skill search.
