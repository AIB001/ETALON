"""The feedback: what may teach the screen, what it predicts, and where to spend next.

Four modules and one direction of flow. :mod:`admissible` rules on which measurements may become
evidence at all. :mod:`calibrate` scores a ranking with its standard error and refuses a change
the panel cannot resolve. :mod:`surrogate` is a cheap model of the expensive number.
:mod:`conformal` turns that model's own guess about its uncertainty into an interval with a
stated coverage, and :mod:`acquire` spends the next batch of compute against those intervals.

The measurement that shapes the last two is ``findings/0003``: on the real 231-molecule panel,
conformal marginal coverage came out at 90.5% against a nominal 90% -- essentially perfect -- while
the scaffold group holding the panel's most potent compound was covered 37.5%. The model
regressed an unseen chemotype to the panel mean, missed a 0.26 nM inhibitor by 3.13 pIC50, and
gave it the *narrowest* interval in its series. So coverage is reported twice here, and the
acquisition layer does not trust an uncertainty estimate to find the unfamiliar.
"""

from etalon.learn.acquire import Batch, Pick, acquire
from etalon.learn.admissible import (
    Admission,
    AdmissionReport,
    Measurement,
    Ruling,
    admissible,
    rule,
    teachable,
)
from etalon.learn.bundle import Bundle, BundleError
from etalon.learn.bundle import export as export_bundle
from etalon.learn.calibrate import (
    Decision,
    Score,
    Verdict,
    auc,
    decide,
    hanley_mcneil_se,
    scaffold_groups,
    score,
)
from etalon.learn.conformal import Calibration, Interval, intervals
from etalon.learn.conformal import calibrate as conformal_calibrate
from etalon.learn.surrogate import Features, Surrogate, featurize, pic50

__all__ = [
    "Admission",
    "AdmissionReport",
    "Decision",
    "Surrogate",
    "Pick",
    "Interval",
    "Features",
    "Calibration",
    "Batch",
    "Bundle",
    "BundleError",
    "Measurement",
    "Ruling",
    "Score",
    "Verdict",
    "admissible",
    "auc",
    "pic50",
    "intervals",
    "featurize",
    "conformal_calibrate",
    "acquire",
    "export_bundle",
    "decide",
    "hanley_mcneil_se",
    "rule",
    "scaffold_groups",
    "score",
    "teachable",
]
