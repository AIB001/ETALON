# Prepare the model the host chose

Use PRISM's existing model registry, config loader and weight manifest. Do not
install every model or merge generation environments with MD. Start with the
current interpreter, source checkout, hardware and available weights; reuse an
applicable recorded profile. Consult prior environment lessons by model and
runtime conditions, not by target name alone.

`etalon_generation_runtime(python, config, model, prism_source=None)` inspects
the actual control interpreter, declared weights and model capability. A missing
weight remains a gap. The SDK is `etalon.generate.runtime.inspect_runtime`.
`prism_source` explicitly binds a checkout root when the interpreter has no
installed PRISM; it is not permission to substitute a different source snapshot.
Optional `environment` binds PATH, LD_LIBRARY_PATH, CUDA_VISIBLE_DEVICES and
PRISM_MODELS_DIR overrides needed by this launch. Credentials are not profile fields.

For missing weights use the selected installation's
`prism.generation.weights.download_models([model])` and its manifest. For missing
dependencies use the corresponding `prism/generation/environments/<model>` recipe
in an isolated environment. A compatibility patch belongs in a versioned model
adapter/environment recipe, not hidden in an unrelated task script. Never put
credentials in profiles or memory. Leave active jobs bound to their old runtime.

Inspection returns a profile with observed paths/capabilities, source/config/
weight identities and `readiness=inspected_not_executed`. Keep it as an ordinary
artifact and a note resource of `kind=method, form=runtime_profile`. A real first
selected generation batch establishes operational readiness; import success alone
does not. Preserve its handle and result, including failure. Do not repeat it as
an extra fixed readiness stage.

The control interpreter can differ from the model's Conda/container runtime.
In particular, distinguish physical GPU assignment from the logical `cuda:0`
inside a masked process. Inspect the selected wrapper's convention before adding
a mask. Record a working combination and move durable fixes into the adapter.

The host chooses generation settings. SDK `generation_command(profile, protein=...,
pocket=..., output=..., num_samples=..., seed=..., device=..., qc=...)` returns argv
and environment overrides without launching. `pocket_kind` preserves the chosen
conditioning interpretation; `add_hydrogens` controls the native hydrogen artifact.
These remain host choices. For the existing durable `method.run`,
use command `[etalon_python, "-m", "etalon.generate.runtime"]` with inputs
`{"profile": "/absolute/profile.json", "generation": {"protein": "...",
"pocket": "...", "num_samples": 100, "seed": 17, "device": "cuda:0"}}`
and output `{"manifest": "generated/manifest.json"}`. Declare the profile, input
structures, config and implementation files through the normal method contract.
The native handle provides recovery; do not launch a replacement on an uncertain
outcome. PRISM retains the generated structure files beside its manifest.

Store the successful runtime recipe separately from the scientific method's
parameters. A hardware workaround is conditional operating knowledge; a change
to pocket conditioning or chemical-state preparation is a scientific method change.
