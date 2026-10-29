# Retain the useful form of a method

A method can live as component code, a callable program, a runtime/parameter
configuration, an improved usage skill, or conditional scientific knowledge.
These are related forms, not compulsory stages or copies of one another.

Keep code in its component or task method directory. A mature builtin needs no
wrapper just to become a method record. Keep how to choose/tune in a skill;
keep task-specific findings and exceptions in memory. A failed expectation can
improve knowledge without producing an algorithm or a new skill.

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
