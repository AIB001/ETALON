# Inspect a selected installation without upgrading it

Known read-only SDK entries can be called directly:

```python
import sys
import etalon
from etalon.boundary.infra import describe
from etalon.doctor import diagnose

print(sys.executable, etalon.__file__)
print(describe())
print(diagnose())
```

`etalon_infrastructure()` exposes `describe()`: actual asset paths, pinned source
identity and provenance. A commit label alone does not cover local modifications.
`etalon_doctor(prism_python="")` exposes `diagnose(prism_python=None)`; an explicit
absolute interpreter checks that optional PRISM build environment. Diagnosis
reports capability-specific dependency/import failures; it does not install,
download weights, call models or run a simulation. Missing optional backends do
not invalidate an unrelated working capability.

`etalon_devices()` calls the pinned MolCascade `detect_environment` and reports
accelerators, memory, cores and unknowns without importing torch. Equivalent SDK:

```python
from etalon.boundary.infra import load
infra = load("molcascade")
from molcascade.environment import detect_environment
environment = detect_environment()
print(infra.provenance(), environment)
```

Free memory is a point-in-time observation, not a reservation; device visibility
does not prove a selected framework/kernel will execute. Preserve unavailable
readings as unknown. Check activity and binding before changing any environment;
use a new isolated environment when an actual dependency change is needed.

## Install/export the available method content

Use the actual selected interpreter for package installation and MCP startup.
Do not install another MolQuarry/MolCascade copy over the vendored sources merely
to obtain its skills. The distribution contains the maintained product methods,
including the six MolQuarry sources; discovery and export need no source queries.

```bash
python -m etalon.host export --target NEW_CODEX_PROJECT --host codex
python -m etalon.host verify --target NEW_CODEX_PROJECT
```

Use `--host claude` with a different new destination for Claude Code. The default
export carries full native skill folders and a short usage role; installed
content/reference bytes have full versions even though they are read on demand.
An exported folder is not a new scientific method implementation or evidence of
native host adoption. Verify export bytes and actual host behavior separately.
External/local modified bundles remain usable; the built-in list is no whitelist.

Existing contract tests are `test_doctor.py`, `test_host_capabilities.py` and
`test_host_bundles_hooks.py`; this round's shared library integration tests cover
the enlarged packaged inventory. Hardware checks and successful exports do not
repair an unrelated host authentication failure.
