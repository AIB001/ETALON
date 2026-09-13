"""Which screening parameters are worth turning, with the published effect size for each.

findings/0004 says where the value is: improving the cheapest ranking tier's correlation by 0.15
outweighs ten times the compute. This package says what achieves that, how much each option is
published to be worth, and which options are not worth a campaign's attention.

The literature's ordering is not the one a configuration file suggests. The scoring function
dominates and the sampler is close to interchangeable; rescoring is the largest single lever; and
consensus scoring is conditional on a published precondition -- every member individually good and
the members diverse -- whose failure mode is also published.

findings/0006: on the operator's funnel, eight engineer-hours of rescoring buys the same number of
true actives as ten times the compute budget, and twenty-four hours of three-score consensus buys
65% more, both at zero added GPU cost.
"""

from etalon.tuning.advise import Advice, Option, advise, rho_for_multiplier
from etalon.tuning.knob import BY_ID, KNOBS, Effect, Knob, Lever, by_lever

__all__ = [
    "BY_ID",
    "KNOBS",
    "Advice",
    "Effect",
    "Knob",
    "Lever",
    "Option",
    "advise",
    "by_lever",
    "rho_for_multiplier",
]
