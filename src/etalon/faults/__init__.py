"""Causes of a wrong number, each carrying the magnitude it could account for.

The taxonomy lists them with a band in kcal/mol; the attribution layer uses the size
of an observed divergence to refuse the ones whose arithmetic cannot reach it. Refusal
is the direction that carries information -- on a real molecule several flags fire at
once, and a list ranked by nothing is not a diagnosis.
"""

from etalon.faults import postflight, preflight, stability
from etalon.faults.attribution import Attribution, Observation, Verdict, attribute
from etalon.faults.preflight import (
    blocking,
    check_population,
    check_record,
    unchecked,
    unverifiable,
    waived_blocking,
)
from etalon.faults.taxonomy import (
    BY_CODE,
    FAULTS,
    Consequence,
    Evidence,
    Exactness,
    Fault,
    Phase,
    postflight_faults,
    preflight_faults,
    unbounded,
    wrong_subject,
)

__all__ = [
    "BY_CODE",
    "FAULTS",
    "Attribution",
    "Consequence",
    "Evidence",
    "Exactness",
    "Fault",
    "Observation",
    "Phase",
    "Verdict",
    "attribute",
    "blocking",
    "check_population",
    "check_record",
    "postflight",
    "preflight",
    "stability",
    "postflight_faults",
    "preflight_faults",
    "unbounded",
    "unchecked",
    "unverifiable",
    "waived_blocking",
    "wrong_subject",
]
