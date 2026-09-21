"""``python -m etalon`` -- evidence, planning and execution boundaries for CADD campaigns.

``data`` acquires and freezes database evidence through MolQuarry; ``screen`` runs and exports
MolCascade pipelines. ``active`` creates/inspects campaign journals and runs offline benchmarks.
Live active experiments use explicitly configured Python executors. ``plan``, ``tune`` and ``fep``
assess scientific/compute choices, while ``infra`` and ``doctor`` identify the installed capabilities.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path


def _plan(arguments: argparse.Namespace) -> int:
    from etalon.campaign.pipeline import DEFAULT_FUNNEL, Pipeline
    from etalon.economics.stage import BY_ID

    stages = tuple(arguments.stages.split(",")) if arguments.stages else DEFAULT_FUNNEL
    overrides = {}
    for pair in arguments.measured or ():
        name, _, value = pair.partition("=")
        if name not in BY_ID:
            print(f"no stage {name!r}; the catalogue holds {', '.join(sorted(BY_ID))}")
            return 2
        try:
            overrides[name] = replace(BY_ID[name], spearman=float(value))
        except ValueError:
            print(f"--measured takes stage=correlation, e.g. docking=0.52; got {pair!r}")
            return 2

    pipeline = Pipeline(
        pool=arguments.pool,
        budget_gpu_hours=arguments.budget,
        active_fraction=arguments.active,
        final_count=arguments.deliver,
        resolvable_spearman=arguments.resolves,
        stages=stages,
    )
    plan = pipeline.dry_run(overrides=overrides)
    if arguments.json:
        print(json.dumps(plan.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(plan.render())
    # Exit 1 when the plan does not fit the budget, so a shell can branch on it. Not an error: a
    # campaign with a month and a million molecules has a real answer and this is it.
    return 0 if plan.funnel.feasible else 1


def _stages(arguments: argparse.Namespace) -> int:
    from etalon.economics.stage import STAGES

    if arguments.json:
        print(json.dumps([stage.as_dict() for stage in STAGES], indent=2, ensure_ascii=False))
        return 0
    print(
        f"{'stage':<22}{'answers':<20}{'GPU-h/mol':>11}{'rho':>7}{'run-run':>9}{'rep':>5}  evidence"
    )
    for stage in STAGES:
        rho = f"{stage.spearman:.3f}" if stage.spearman is not None else "-"
        spread = f"{stage.run_to_run_kcal_mol:g}" if stage.run_to_run_kcal_mol else "-"
        print(
            f"{stage.id:<22}{stage.answers.value:<20}{stage.gpu_hours:>11.2e}{rho:>7}"
            f"{spread:>9}{stage.replicas_for_a_measurement():>5}  {stage.evidence.value}"
        )
    print()
    print("Every number above has a source; `--json` prints it. The ones marked convention or")
    print("inferred are the ones to replace with your own measurements first.")
    return 0


def _infra(arguments: argparse.Namespace) -> int:
    from etalon.boundary.infra import describe

    report = describe()
    if arguments.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if all(entry.get("pinned") for entry in report.values()) else 1
    pinned = True
    for name, entry in report.items():
        if entry.get("pinned"):
            print(f"{name:<12}pinned   {str(entry['source_commit'])[:12]}  {entry['loaded_from']}")
        else:
            pinned = False
            print(f"{name:<12}REFUSED  {entry.get('problem')}")
    return 0 if pinned else 1


def _tune(arguments: argparse.Namespace) -> int:
    from etalon.campaign.pipeline import DEFAULT_FUNNEL, Pipeline
    from etalon.tuning.advise import advise

    pipeline = Pipeline(
        pool=arguments.pool,
        budget_gpu_hours=arguments.budget,
        active_fraction=arguments.active,
        final_count=arguments.deliver,
        resolvable_spearman=arguments.resolves,
        stages=tuple(arguments.stages.split(",")) if arguments.stages else DEFAULT_FUNNEL,
    )
    advice = advise(
        pipeline.pool,
        pipeline.funnel_stages(),
        budget_gpu_hours=pipeline.budget_gpu_hours,
        active_fraction=pipeline.active_fraction,
        final_count=pipeline.final_count,
        resolvable_spearman=pipeline.resolvable_spearman,
        established=set(arguments.established or ()),
    )
    if arguments.json:
        print(json.dumps(advice.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(advice.render())
    return 0


def _fep(arguments: argparse.Namespace) -> int:
    import csv

    from etalon.fep.network import design

    molecules: dict[str, str] = {}
    with Path(arguments.library).open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if arguments.limit and index >= arguments.limit:
                break
            smiles = row.get(arguments.smiles_column)
            if not smiles:
                continue
            molecules[row.get(arguments.id_column) or f"m{index:04d}"] = smiles
    if not molecules:
        print(
            f"no molecules read from {arguments.library}. Expected columns "
            f"{arguments.id_column!r} and {arguments.smiles_column!r}."
        )
        return 2

    network = design(
        molecules,
        references=tuple(arguments.reference or ()),
        min_core_fraction=arguments.core,
        cycle_edges=arguments.cycles,
    )
    if arguments.json:
        print(json.dumps(network.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(network.render())
    # Exit 1 when any molecule cannot be reached, so a pipeline script notices before it commits
    # GPU-days to a relative calculation that cannot include them.
    return 0 if not network.unreachable else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="etalon", description=__doc__.splitlines()[0] if __doc__ else ""
    )
    sub = parser.add_subparsers(dest="command", required=True)
    from etalon.active.cli import register

    register(sub)
    from etalon.execution_cli import register as register_execution

    register_execution(sub)
    from etalon.data.cli import register as register_data

    register_data(sub)

    plan = sub.add_parser("plan", help="render a funnel's shape, cost and refusals")
    plan.add_argument("--pool", type=int, default=750_000, help="molecules entering the funnel")
    plan.add_argument(
        "--budget", type=float, default=8760.0, help="GPU-hours available (8760 = one GPU-year)"
    )
    plan.add_argument(
        "--active", type=float, default=0.001, help="share of the pool that is truly active"
    )
    plan.add_argument("--deliver", type=int, default=10, help="molecules the funnel must deliver")
    plan.add_argument(
        "--resolves",
        type=float,
        default=0.10,
        help="smallest rank-correlation difference your panel can resolve; twice the "
        "Hanley-McNeil standard error of an AUC on it",
    )
    plan.add_argument("--stages", help="comma-separated stage ids, in order")
    plan.add_argument(
        "--measured",
        action="append",
        metavar="STAGE=RHO",
        help="substitute a correlation you measured yourself, e.g. docking=0.52. Repeatable, and "
        "the intended way to use this: every figure in the catalogue is a placeholder for one.",
    )
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(handler=_plan)

    stages = sub.add_parser("stages", help="print the priced stage catalogue")
    stages.add_argument("--json", action="store_true")
    stages.set_defaults(handler=_stages)

    tune = sub.add_parser(
        "tune", help="rank the screening knobs by the actives they would add to this funnel"
    )
    tune.add_argument("--pool", type=int, default=750_000)
    tune.add_argument("--budget", type=float, default=8760.0)
    tune.add_argument("--active", type=float, default=0.001)
    tune.add_argument("--deliver", type=int, default=10)
    tune.add_argument("--resolves", type=float, default=0.10)
    tune.add_argument("--stages")
    tune.add_argument(
        "--established",
        action="append",
        metavar="KNOB",
        help="a knob whose precondition you have checked. Repeatable. Without it the conditional "
        "knobs are refused with the check named, which is the default because turning them "
        "unchecked is a published failure mode.",
    )
    tune.add_argument("--json", action="store_true")
    tune.set_defaults(handler=_tune)

    fep = sub.add_parser(
        "fep", help="design the edge network a relative calculation needs, and name what it cannot reach"
    )
    fep.add_argument("library", help="CSV of candidate molecules")
    fep.add_argument("--smiles-column", default="smiles")
    fep.add_argument("--id-column", default="id")
    fep.add_argument(
        "--reference",
        action="append",
        metavar="ID",
        help="a molecule with a measured affinity. Repeatable. A component containing none yields "
        "differences and no absolute values.",
    )
    fep.add_argument(
        "--core",
        type=float,
        default=0.5,
        help="minimum share of the larger molecule the common core must cover",
    )
    fep.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="edges beyond a spanning forest. Each closes one independent cycle and buys one "
        "hysteresis check, which is the only error estimate here that is a measurement.",
    )
    fep.add_argument("--limit", type=int, default=0, help="read at most this many molecules")
    fep.add_argument("--json", action="store_true")
    fep.set_defaults(handler=_fep)

    infra = sub.add_parser("infra", help="identify pinned MolCascade, PRISM and MolQuarry")
    infra.add_argument("--json", action="store_true")
    infra.set_defaults(handler=_infra)

    arguments = parser.parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
