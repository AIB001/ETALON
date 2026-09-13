"""The tools a model should call before it spends anything, and they are all free.

A campaign is a budget decision, and the characteristic failure of an autonomous agent running one is
not a wrong calculation -- it is a funnel whose shape nobody examined until the GPU-months were gone.
So the planning tools cost nothing, return their reasoning, and name what they refuse.

Every one of them is designed to be called repeatedly with different numbers. That is how a model
should use them: plan, read the refusals, change an input, plan again. A campaign that was planned
once has not been planned.
"""

from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_plan_campaign(
        pool: int,
        budget_gpu_hours: float,
        active_fraction: float = 0.001,
        deliver: int = 10,
        resolvable_spearman: float = 0.10,
        stages: str = "",
        measured: str = "",
    ) -> str:
        """FREE. Shape a screening funnel for a pool and a budget, before spending anything.

        Returns the tiers that survive, how wide each should be, the total cost in GPU-years, how
        many of the pool's true actives are expected to reach the end, and -- the part to read first
        -- which tiers rest on numbers nobody measured.

        Call this before any other tool. It refuses three kinds of tier: one answering a different
        question from ranking (PMF answers mechanism), one whose accuracy the panel cannot distinguish
        from the tier above it, and one that ranks with no measured correlation. Each refusal names
        the remedy.

        Args:
            pool: Molecules entering the funnel. Five generative models at 150,000 each is 750,000.
            budget_gpu_hours: 8760 is one GPU-year.
            active_fraction: Share of the pool that truly binds. 0.001 is a plausible prior for a
                generated library against one target; it is a guess and the plan is sensitive to it.
            deliver: Molecules the funnel must deliver.
            resolvable_spearman: The smallest rank-correlation difference your panel can resolve,
                which is twice the Hanley-McNeil standard error of an AUC on it. About 0.10 for a
                231-molecule panel with 40 actives. Leave it unless you have measured your own.
            stages: Comma-separated stage ids in order. Empty uses the default funnel.
            measured: Comma-separated ``stage=correlation`` pairs you measured yourself, e.g.
                ``docking=0.52``. This is the intended way to use the tool: every figure in the
                catalogue is a placeholder for one of these, and docking's is the input the whole
                plan turns on.
        """

        from dataclasses import replace

        from etalon.campaign.pipeline import DEFAULT_FUNNEL, Pipeline
        from etalon.economics.stage import BY_ID

        overrides = {}
        for pair in (item for item in measured.split(",") if item.strip()):
            name, _, value = pair.partition("=")
            name = name.strip()
            if name not in BY_ID:
                raise KeyError(
                    f"no stage {name!r}; the catalogue holds {sorted(BY_ID)}. Call etalon_stages to "
                    "see what each one is."
                )
            overrides[name] = replace(BY_ID[name], spearman=float(value))

        pipeline = Pipeline(
            pool=pool,
            budget_gpu_hours=budget_gpu_hours,
            active_fraction=active_fraction,
            final_count=deliver,
            resolvable_spearman=resolvable_spearman,
            stages=tuple(s.strip() for s in stages.split(",") if s.strip()) or DEFAULT_FUNNEL,
        )
        plan = pipeline.dry_run(overrides=overrides)
        return ok(
            plan=plan.as_dict(),
            rendered=plan.render(),
            next_step=(
                "Read unmeasured_inputs first. If docking's correlation is still the catalogue "
                "placeholder, measuring it on your own panel is worth more than any other single "
                "action: call etalon_tune_screen to see what it is worth."
                if any(stage == "docking" for stage, _ in plan.conventions)
                else "Call etalon_tune_screen to see what changing the screen would be worth "
                "against this plan."
            ),
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_tune_screen(
        pool: int = 750_000,
        budget_gpu_hours: float = 8760.0,
        active_fraction: float = 0.001,
        deliver: int = 10,
        resolvable_spearman: float = 0.10,
        established: str = "",
    ) -> str:
        """FREE. Rank what you could change about the screen by the true actives it would add.

        Puts engineer-hours and GPU-years in the same units, which is the comparison a campaign
        actually faces. On a default funnel, eight engineer-hours of ML rescoring delivers the same
        gain as ten times the compute budget.

        Two kinds of refusal, both informative. A knob whose published effect is smaller than your
        panel can resolve is separated out -- turn it once on the strength of its publication, and do
        not expect to observe it working. A knob with an unestablished precondition is refused with
        the check named: consensus scoring improves enrichment only if each member is individually
        good AND the members are diverse, and that is the published condition, not a caution added
        here. On kinases where it held, Top-1% enrichment went from 6.4 to 23.5; on GPCR-Bench where
        it did not, MM/GBSA-containing combinations improved 32% and 19% of combinations.

        Args:
            established: Comma-separated knob ids whose precondition you have checked. Without them
                the conditional knobs stay refused, which is the default on purpose.
        """

        from etalon.campaign.pipeline import Pipeline
        from etalon.tuning.advise import advise

        pipeline = Pipeline(
            pool=pool,
            budget_gpu_hours=budget_gpu_hours,
            active_fraction=active_fraction,
            final_count=deliver,
            resolvable_spearman=resolvable_spearman,
        )
        advice = advise(
            pipeline.pool,
            pipeline.funnel_stages(),
            budget_gpu_hours=pipeline.budget_gpu_hours,
            active_fraction=pipeline.active_fraction,
            final_count=pipeline.final_count,
            resolvable_spearman=pipeline.resolvable_spearman,
            established={item.strip() for item in established.split(",") if item.strip()},
        )
        return ok(
            advice=advice.as_dict(),
            rendered=advice.render(),
            next_step=(
                "Establish a precondition before acting on its knob. For consensus scoring: score "
                "each candidate function on your panel separately, then correlate their rankings "
                "with each other. A member near chance contributes noise; two correlating above "
                "about 0.9 contribute one opinion at two prices."
                if advice.precondition_unmet
                else "The ranked knobs are worth turning. Each gain inherits the assumption that "
                "consecutive tiers' errors are independent, and a rescorer reading docking's own "
                "poses is the least independent improvement there is, so treat the top figure as "
                "optimistic."
            ),
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_stages() -> str:
        """FREE. The priced stage catalogue: cost, rank correlation, reproducibility, and sources.

        Read this to see which numbers in any plan are measurements and which are conventions. Two
        entries are worth reading in full before designing a pipeline. A single MM-PBSA trajectory is
        not reproducible to better than about 12 kcal/mol, so the honest price of a rankable number
        is five replicas. And PMF by umbrella sampling costs roughly 2.1 microseconds per complex --
        hundreds of GPU-hours against tens for an FEP edge -- while a head-to-head found it no more
        accurate, so it answers mechanism rather than rank and cannot be a tier.
        """

        from etalon.economics.stage import STAGES, needs_an_ensemble

        return ok(
            stages=[stage.as_dict() for stage in STAGES],
            single_run_is_not_a_measurement=[stage.id for stage in needs_an_ensemble()],
            next_step=(
                "Replace the entries marked convention or inferred with your own measurements. "
                "docking's correlation first: it is the input a funnel plan is most sensitive to."
            ),
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_infrastructure() -> str:
        """FREE. Which MolCascade and which PRISM would actually load, and whether they are pinned.

        Call this first in a new environment and whenever a tool fails unexpectedly. An editable
        install shadowing the vendored copy is the usual cause, and it is silent: the import succeeds,
        the version string is right, and every number afterwards cites a commit that did not produce
        it.
        """

        from etalon.boundary.infra import describe

        report = describe()
        pinned = all(entry.get("pinned") for entry in report.values())
        return ok(
            infrastructure=report,
            all_pinned=pinned,
            next_step=(
                "Both packages are pinned; results can cite their commits."
                if pinned
                else "A package is not pinned. Start a fresh interpreter with the asset root ahead "
                "of site-packages on sys.path, or results will cite a commit that did not produce "
                "them."
            ),
        )


__all__ = ["register"]
