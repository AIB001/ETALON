"""Readiness checks for distinct execution layers; never launch scientific jobs."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import shutil
import sys
from typing import Any

_PACKAGES = {
    "numpy": ("numpy", (1, 26), None),
    "scipy": ("scipy", (1, 10), None),
    "rdkit": ("rdkit.Chem", (2024, 9), None),
    "pydantic": ("pydantic", (2, 10), (3,)),
    "pyarrow": ("pyarrow.parquet", (17,), (26,)),
    "PyYAML": ("yaml", (6,), (7,)),
    "openpyxl": ("openpyxl", (3, 1), (4,)),
}


def _package(name: str, module: str, lower: tuple[int, ...],
             upper: tuple[int, ...] | None) -> dict[str, Any]:
    try:
        version = importlib.metadata.version(name)
        match = re.match(r"^(\d+(?:\.\d+)*)", version)
        release = tuple(int(p) for p in match[1].split(".")) if match else ()
        if release < lower or (upper is not None and release >= upper):
            return {"ready": False, "version": version, "problem": "unsupported dependency version"}
        importlib.import_module(module)  # A discoverable package can still fail to import.
        return {"ready": True, "version": version}
    except Exception as error:
        return {"ready": False, "problem": f"{type(error).__name__}: {error}"}


def diagnose(*, prism_python: str | None = None) -> dict[str, Any]:
    from etalon.boundary.infra import describe, load

    packages = {name: _package(name, *details) for name, details in _PACKAGES.items()}
    infrastructure = describe()
    # The numeric controller's numpy floor is lower than the molecular extra's.
    # A machine that cannot run MolCascade may still run valid numerical campaigns.
    active_packages = {"numpy": _package("numpy", "numpy", (1, 24), None),
                       "scipy": packages["scipy"]}
    active = all(row["ready"] for row in active_packages.values())
    cascade = all(row["ready"] for row in packages.values())
    cascade = cascade and infrastructure["molcascade"].get("pinned", False)
    components: dict[str, Any] = {"ready": False}
    if cascade:
        try:
            load("molcascade")
            from molcascade.plugins import create_builtin_registry

            components = {"ready": True, "plugin_count": len(list(create_builtin_registry()))}
        except Exception as error:
            cascade = False
            components["problem"] = f"{type(error).__name__}: {error}"
    try:
        from etalon.mcp._common import require_mcp

        require_mcp()
        mcp = {"ready": True}
    except Exception as error:
        mcp = {"ready": False, "problem": str(error)}
    simulation: dict[str, Any] = {"ready": False, "status": "not_probed",
                                  "hint": "use --prism-python /path/to/prism-env/bin/python"}
    if prism_python is not None:
        from etalon.boundary.simulate import discover

        try:
            simulation = discover(prism_python).as_dict()
            simulation["scope"] = "executables only; not a successful PRISM build or affinity calculation"
        except Exception as error:
            simulation = {"ready": False, "problem": str(error)}
    readiness = {"planning": True, "active": active, "cascade": bool(cascade),
                 "mcp": mcp["ready"], "prism-build-tools": simulation["ready"]}
    return {
        "python": sys.executable, "python_version": sys.version.split()[0],
        "readiness": readiness, "packages": packages, "active_dependencies": active_packages,
        "infrastructure": infrastructure,
        "components": components, "mcp": mcp, "simulation": simulation,
        "advisors": {"claude_cli_installed": shutil.which("claude") is not None,
                     "api_key_present": {provider: bool(os.environ.get(key, "").strip())
                                         for provider, key in (("openai", "OPENAI_API_KEY"),
                                                               ("anthropic", "ANTHROPIC_API_KEY"),
                                                               ("deepseek", "DEEPSEEK_API_KEY"))}},
        "install_hint": "From the complete checkout: python -m pip install -e '.[cascade,active,mcp,llm]'",
        "scope": "Readiness probes only. Docking backends/weights are checked against a specific screen plan. "
                 "Asset provenance records manifest identities; use tools/verify_assets.py --deep for content verification.",
    }
