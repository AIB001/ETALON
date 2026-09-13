"""Documents an agent should read before calling a tool, and one prompt.

MCP resources are for things a client reads rather than invokes.  The two here
exist because the tool docstrings can say what a tool does but not how the
pieces fit, and an agent that calls ``screen`` without understanding that a
cascade's survivors are a biased sample, or that a shortlist carries no
coordinates, will produce work that looks finished and is not.
"""

from __future__ import annotations

from typing import Any

_OVERVIEW = """\
# MolCascade

Auditable, modular, hierarchical molecular screening. A configuration describes a
funnel; running it narrows a library from millions of molecules to a shortlist,
and records why every molecule that left did so.

## The two configuration shapes

A **cascade** (`schema_version: 2`) is what people author. It is tier-first: a
tier has a title, a mode, and criteria; a criterion names a backend and its
thresholds. A tier's mode says how its criteria combine -- `all` means every
criterion must pass, `any` means one is enough. `any` is used where two models
disagree and neither should be allowed to delete a molecule alone.

A **pipeline** (`schema_version: 1`) is the flat stage list a cascade lowers into,
with explicit port-to-port bindings. It is what executes. You will rarely author
one.

## Evidence and policy are separate, and this is the central design decision

A predictor does not decide. It publishes *evidence* -- a docking score, an ADMET
probability, a demerit total, a nearest-reference similarity -- under a typed
contract. A separate gate stage consumes that evidence and publishes a
*decision* with a stable reason code.

The consequence for an agent: a threshold is not a property of a model, so
changing one does not invalidate the model's output, and the same evidence can be
gated two ways in two runs and compared. It also means a number and the verdict
derived from it are separately auditable.

## What a run records, and why it can be trusted

Artifacts are content-addressed and immutable. Each stage's cache key hashes its
full plugin descriptor, its configuration, its seed, its contracts and its input
artifact ids -- so a parameter change is an identity change, and a stored
checkpoint whose configuration differs is refused rather than reused. Run state,
revisions and an append-only audit log are written to the workspace as the run
proceeds, so everything survives process death.

Shard boundaries are a function of the input rather than of the machine. A
four-core laptop and a sixty-four-core node commit identical bytes.

## Five things to know before trusting a result

**A cascade's survivors are a biased sample.** Every threshold is a claim about
what is not worth looking at. Before believing one, screen a panel of molecules
already known to bind and call `measure_recall`. Recall multiplies: twenty-two
gates each keeping 98% keep 64% between them, and no single tier looks wrong.

**Stages that run after the last tier also remove molecules.** The shortlist
selector caps each Murcko scaffold. A panel of measured compounds is a congeneric
series, so that cap can delete more than every gate combined while each tier
truthfully reports keeping everything. `measure_recall` counts it separately.

**A shortlist is SMILES.** No coordinates, no hydrogens, no chosen protonation
state. Docked poses are in a sidecar, reported by `export_shortlist`. Handing a
coordinate-free record to a force-field build produces a flat, hydrogen-free
ligand at the origin, and every downstream validator will pass it.

**Stereochemistry may have been chosen for you.** Conformer generation enforces
the chirality a SMILES specifies and settles the rest by distance geometry from a
seeded hash. Call `audit_stereochemistry` to find out which molecules those were.

**A missing backend shortens the funnel silently.** A criterion whose backend is
unavailable is dropped when defaults are built. Call `doctor` and `plan_screen`
before a campaign; the funnel they report is the one that will run.

## The order to call things in

1. `doctor` and `environment` -- can this machine do it
2. `validate_config` or `plan_screen` -- does the configuration compile, and what
   funnel does it actually build
3. `screen` -- execute; on a client timeout, call again with the same `run_id` and
   `resume=true`, which is safe here
4. `run_status` -- what happened, with the audit log
5. `summarise_decisions`, `explain_molecule`, `audit_stereochemistry`,
   `measure_recall` -- why
6. `export_shortlist`, `trace_run_stages`, `generate_report` -- the product
"""

_CONTRACTS = """\
# The contracts a stage can publish

A contract is an Arrow schema with a primary key, declared enums and written
invariants. A stage declares which it consumes and which it publishes, and the
compiler refuses a pipeline whose bindings do not match.

`parent/v1` -- the molecule population. Every stage that can remove molecules
republishes it with the survivors.

`decision/v1` -- one row per decision, keyed on entity kind, with an outcome
(PASS / WARN / REJECT), a stable `reason_code` and a free-text detail. A REJECT is
terminal: nothing downstream can restore the molecule. Note that a molecule can
produce several rows in one stage, so these are rows rather than molecules.

`prediction/v1` -- a model's output for an endpoint, with `prediction_mean`,
`prediction_std` and interval bounds. Uncertainty is part of the contract.

`docking_score/v1` -- a pose and its score, bound to a `receptor_id` that digests
the exact receptor bytes. Scores from different receptors are not comparable and
the contract says so.

`derived_metric/v1` -- a named numeric metric with units, a direction and a
status. A molecule the metric could not be computed for keeps a row with a null
value and `BACKEND_FAILED`, so a gate sees an explicit absence rather than a
missing row it might read as a pass.

`applicability/v1` -- distance from a reference set, keyed on
`(parent_id, model_id, method_id)`. Two reference sets can therefore coexist in
one run, which is how "near a potent molecule" and "near a molecule that was
measured and does not bind" become two separate numbers.

`ligand_conformer/v1` -- 3D structures as molblocks.

`selection_decision/v1` -- what the shortlist selector kept, with its basket and
reason code.

`synthesis_score/v1`, `drug_likeness/v1`, `molecule_property/v1`,
`fingerprint/v1`, `scaffold/v1`, `cluster_assignment/v1` -- the remaining evidence
shapes, each consumed by a gate of its own kind.
"""


def register(mcp: Any) -> None:
    @mcp.resource("molcascade://overview")
    def overview() -> str:
        """What MolCascade is, what it records, and the order to call tools in.

        Read this before the first tool call in a session. It carries the five
        properties of a screening result that an agent is most likely to get
        wrong, each of which produces work that looks finished.
        """

        return _OVERVIEW

    @mcp.resource("molcascade://contracts")
    def contracts() -> str:
        """The typed data contracts a stage can publish, and what each guarantees.

        Read this when interpreting a trace, deciding which evidence to gate on,
        or handing a result to another system.
        """

        return _CONTRACTS

    @mcp.prompt()
    def calibrate_a_funnel(target_name: str = "", panel_path: str = "") -> str:
        """Draft the workflow for calibrating a cascade against known actives.

        This is the measurement to run before trusting any threshold, and it is
        the one most often skipped. Supply a panel of molecules with measured
        activity against the target and this lays out the sequence.
        """

        target = target_name or "the target"
        panel = panel_path or "<absolute path to the panel CSV>"
        return f"""\
Calibrate the screening funnel for {target} against a panel of known actives.

The panel is at {panel}. It must be read with an identifier column, because
recall is measured by name and a run that records no names cannot be measured.

Do this in order, and report the numbers rather than a judgement:

1. `doctor` -- list which backends are available here, because an unavailable
   one shortens the funnel silently.
2. `plan_screen` on the cascade with the panel as the library -- report the
   funnel that will actually be built and its revision id.
3. `screen` -- run the panel through it.
4. `measure_recall` -- report the per-tier retention, the post-tier `finalize`
   block separately, and the end-to-end product. Name the molecules each tier
   lost.
5. `summarise_decisions` -- for every tier that lost molecules, report which
   reason codes fired and how often.
6. For each tier that cost recall, state two numbers: how many panel molecules
   it deleted, and how much of the screening library it rejects. A gate that
   deletes known actives without rejecting much library is pure loss; a gate
   that rejects a lot of library for few actives is earning its place.

Do not retune anything. Report the measurement and let the operator decide --
a threshold tuned by the same tool that measures it has no independent check.
"""


__all__ = ["register"]
