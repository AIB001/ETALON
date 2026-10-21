---
name: etalon-skill-improvement
description: Revise a scientific method, its selection guidance, or this improvement method from observed evidence; preserve complete candidates, generation provenance and costs, including reasoned no-change.
---

# Improve a method from an observed problem

Use this when a trace, incorrect interpretation, repeated lookup, or missing input
example suggests a specific content or selection change. Ordinary SDK calls and
use of an unregistered local method remain valid without this procedure.

Choose the object of the change: **S** is a scientific method's description,
references or scripts; **U** is how the host chooses, combines or skips methods;
**G** is this improvement method. Keep their versions separate. G's generating
call is separate from the changed object's parent lineage. A new digest is not
evidence of improvement, and this method does not train model weights.

Keep the problem's actual input/output or event reference, the proposed causal
explanation, and its uncertainty. For example, a large irrelevant source search
can motivate adding an exact SDK/reference pointer; it does not prove that search
caused the entire task's latency. Make the smallest useful candidate change and
preserve the complete old and new bundles, including supporting files.
If a method was skipped, distinguish a selection or visibility issue from a
content defect: success through direct SDK use is legitimate, and its outcome
does not measure the skipped method's effect. A reasoned no-change is an output.

Try only the relevant part on an authorized local task. Record the version and
scope actually used, ignored parts, original results, failures/unknowns, and costs
when attribution is useful. Discovery, recording and trial are optional; neither
registration nor promotion is a prerequisite for using the candidate.

For concrete SDK calls, append-only records, a bounded hook, and how to reserve a
later independent comparison, read [the revision reference](references/revision.md).
The source distribution's `examples/skill_improvement.py` is an executable,
synthetic example; it creates its own evidence directory and does no science.
For selection, partial use or composition questions, use
[selection and composition](references/selection-composition.md). For changing
G itself or recording producer/target relationships with the runnable SDK helper,
use [self-revision](references/self-revision.md). These are optional references,
not stages required before ordinary work.

Decide to retain, revise, withdraw or stop from the actual returned evidence.
Keep both versions if the result is inconclusive. A later independent task with
frozen conditions and total-cost accounting is needed to claim benefit. Do not
relabel the development trial as that evaluation, force a full factorial study,
or repeatedly rewrite methods from their own new-version events.
