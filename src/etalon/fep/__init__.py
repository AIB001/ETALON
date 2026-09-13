"""The edge network a relative free energy calculation needs, and what it cannot reach.

Every other tier scores molecules; FEP scores differences, so a set of molecules is not an input to
it. :mod:`network` computes the maximum common substructure of every candidate pair, designs a
network over the pairs that can actually be related, and names the molecules that cannot.

findings/0007, measured on the real panel: of the 120 pairs among the 16 most potent STK17B
molecules, 110 map through a common core smaller than half the larger molecule, and 7 of the 16 have
no usable edge to anything. A campaign that selects for diversity and scaffold novelty delivers
exactly the molecules relative FEP cannot relate to each other.

It also makes F_FEP_MAPPING_DEGENERATE computable. That fault was reported unevaluable everywhere,
because a mapping is a property of an edge and the preflight layer sees one record at a time.
"""

from etalon.fep.network import (
    MIN_CORE_FRACTION,
    Edge,
    Network,
    design,
    mapping_quality,
    observations,
)

__all__ = [
    "MIN_CORE_FRACTION",
    "Edge",
    "Network",
    "design",
    "mapping_quality",
    "observations",
]
