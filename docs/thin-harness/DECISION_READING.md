# Optional decision-directed reading

`etalon.research.decision_reading.build_decision_reading` reuses the existing
entry, scope graph, original validators, policy and publication/workset readers.
It creates no store, journal record, execution, semantic relevance classifier or
automatic adoption. The producer is the published archive; the consumer is a
host deciding which originals matter to a particular question.

```python
build_decision_reading(
    entry, *, question, scope=None, reading_mode="decision", purpose="research",
    policy=None, conditions=None, relevance=None, decision_changed=None,
    max_bytes=16384, cursor=None, limit=20, as_of=None, home_overrides=None,
)
```

`scope` is an existing exact branch or root-ref graph scope, with 0/1 ordinary
hops. Default selection starts from the task's published tail at zero ordinary
hops, preserving its dependency boundary as explicit optional detail. This
avoids expanding every historical step just to answer a status question.
Same-task or explicitly shared-capability experiences join the exact roots;
negative outcomes, condition conflicts and superseded experiences are retained.
The existing transitive counter incidence is checked over every permitted
published source, independently of branch or ordering. It is never hidden by
top-k selection. Bounds that cannot retain required counter context fail.

`status` permits early stopping once requested progress and controls are known.
`decision` requires relevant originals before a changed decision. `diagnosis`
follows exact execution, input, output and check links; absent links leave cause
unknown. These obligations guide the host; the reader neither infers which
opaque passage matters nor certifies comprehension. `decision_changed` is host
intent. Conditions accept explicit strings and null unknowns. Relevance
annotations require a verified selected ref, a reason and the affected decision;
they cannot smuggle a new reference into the selected scope.

Original obligations attach to selected roots and their exact basis/input/check
references, plus counters challenging those objects. A checkpoint's broad
counter list is not itself a reason to read every branch's original. Discovered
same-task experiences and other context remain conditional host relevance
reviews; unresolved relevance is an explicit gap. Control metadata is mandatory,
while a checkpoint body is conditional unless explicitly selected. Applicability
matches cover only declared dimensions: empty conditions, null values and host
dimensions missing from the experience remain unknown.
Counter coverage describes incidence of included nodes within permitted sources.
A required basis left beyond the ordinary-hop boundary is an explicit unresolved
counter-closure gap: select its exact root before treating that basis as covered.

Every read item has its original ref, correctly typed record/artifact navigation,
source scope, why it was selected, what it may change, attribution of selection,
and a required/conditional obligation. Exact source receipts, branch alternatives,
counter directions and applicability conditions are retained in lossless pages.
The header repeats current controls and counter coverage. `next_cursor` is a
transport continuation, never an omitted-evidence score. Large rows use the
existing base64 UTF-8 byte-fragment convention; reassemble by index/offset before
decoding. The reading cap is 4096..16384 bytes; originals retain the existing
32768-byte reader limit. Pages bind all source watermarks, scope, mode, question,
conditions and host annotations; changed or added sources invalidate the cursor.

The read_context pins policy and purpose. Reading rejects a mismatched purpose
or policy instead of silently coercing it; an evaluation pin must also be called
with `purpose="evaluation"`. Sealed and dependent hidden
records remain unavailable. The context is a read receipt, never an execution
grant. Cross-workspace evidence needs its explicitly published locator and a
separate context. Identity checks, plan delivery, original delivery, host
comprehension and scientific validity remain separate.

The view intentionally does not certify global evidence closure. It reports
counter-source restrictions, ordinary-hop boundaries, unknown publication status,
changed task-workset conditions and uninspected opaque-body relationships.
No embedding retrieval, causal diagnosis, scientific gain, speedup, independent
replication or permission to resume is inferred.

Integration uses optional context `view="reading"` with
`view_options.reading_mode`; existing summary/card/full defaults remain valid.
Focused validation lives in `tests/test_decision_reading.py`. Local historical
SND1 and PoseBusters read demonstrations are read-only engineering checks, not a
new scientific experiment or a relabelled holdout.
