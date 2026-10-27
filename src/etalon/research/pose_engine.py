"""Compatibility import for the panel-specific execution recipe."""
import sys
from etalon.experiments import posebusters
sys.modules[__name__] = posebusters
