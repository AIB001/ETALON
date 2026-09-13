"""The generation stage, and the only feedback loop a CADD pipeline normally has none on.

Five models producing 150,000 molecules each is a decision about where to spend generation, and it
is normally made once: the campaign runs the models it has, screens the union, and reports the
survivors. :mod:`audit` makes the per-model question answerable and says where the answer is
usable -- which is not where it is normally read.

``findings/0005``: on counts of this shape, a model's share of the final hits distinguishes none of
five models from each other, while the QC rate measured on 150,000 molecules distinguishes all ten
pairs. Four of ten final hits carries a 95% interval from 17% to 69%.
"""

from etalon.generate.audit import Audit, Comparison, GeneratorYield, audit, compare, wilson

__all__ = ["Audit", "Comparison", "GeneratorYield", "audit", "compare", "wilson"]
