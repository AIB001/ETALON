"""ETALON as a tool surface a language model can drive a whole campaign through.

``python -m etalon.mcp`` runs the server over stdio. Every tool declares what it spends -- free, cheap,
spends GPU-days, or cannot be completed by a model at all -- and the classification is in the tool's
own description so it is visible before a choice rather than after it.

The governance is not per-action confirmation, because a campaign has 750,000 actions. It is that the
expensive steps are guarded by cheap checks the model is expected to call, and that the guards refuse
rather than warn. One tool, ``etalon_recommend_waiver``, cannot be completed by a model at all: a
waiver's value is that a named person accepted a consequence, and that cannot be delegated to
something which cannot be held to it.
"""

from etalon.mcp.server import SKILL_RESOURCE, build, costs, main

__all__ = ["SKILL_RESOURCE", "build", "costs", "main"]
