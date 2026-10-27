"""Compatibility import; implementation belongs to execution, not research."""
import sys
from etalon.runtime import native as _implementation
if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
