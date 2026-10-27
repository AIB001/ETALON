"""Compatibility import for the scientific implementation."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("etalon.rescore.poses")
