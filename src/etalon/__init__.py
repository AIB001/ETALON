"""ETALON — a CADD agent that knows how much to trust its own numbers.

In metrology an etalon is the primary standard every other instrument is calibrated
against. In optics a Fabry-Perot etalon extracts information from the interference
between two beams -- that is, from their disagreement. The name carries both meanings
because both are what this system is for.

It runs two packages as infrastructure, pinned and verified under ``asset/``: MolCascade
for ligand triage and docking, PRISM for system building, MD and free energy. Neither is
modified. What ETALON adds is a campaign that is allowed to learn from its own expensive
measurements, under conditions it states.

The shape is four layers, and the order is the argument.

``boundary`` reaches the infrastructure and proves which copy it reached. ``faults`` asks,
of one handoff record, whether a number computed from it would be about the molecule it is
filed under -- cheaply, before a GPU-second is spent. ``learn`` decides which measurements
may update the screen and whether a proposed change beats the panel's own resolution.
``campaign`` is the loop, and it keeps an append-only ledger that cannot forget a round it
abandoned.

The feedback idea is not new and this project does not claim it is: DeepDriveMD, IMPECCABLE
and Colmena all put machine learning inside a CADD campaign, and an earlier draft of this
package claimed novelty over them and was wrong. What those loops assume, nowhere stated
because it is too obvious to state, is that a number arriving from the expensive stage is a
measurement of the molecule it is filed under. That assumption is false here and it has
been measured: the shortlist exporter rebuilds geometry from SMILES -- zero explicit
hydrogens, every z exactly 0.00 -- and across 41 gaff2 builds from such input the topology
carried no hydrogens in 41 of 41, with no warning at any stage. A loop without a gate there
does not record one bad number; it fits the policy applied to every later molecule to a
label from a molecule that was never simulated. So the contribution is the admissibility
criterion on the feedback, and the refusal to accept an update it cannot distinguish from
noise.

Every architectural decision here is recorded under ``docs/adr/`` with the measurement that
forced it. The first says a PRISM build is not reproducible and says so with two digests
rather than with an argument. The second says a magnitude band cannot decide whether to
refuse a molecule, and was written because the rule that said otherwise refused a molecule
that was entirely correct.
"""

__version__ = "0.1.0"
