import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .catalog import CATEGORIES
from .client import MolQuarry
from .errors import MolQuarryError
from .models import DownloadPlan
from .workflows import InhibitorSearch, collect_inhibitor_evidence
from .workflows.review import Curation, integrate_review


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise MolQuarryError("invalid_arguments", message)


def build_parser():
    parser = Parser(description="MolQuarry: discover, query and download CADD data as JSON")
    parser.add_argument(
        "--home", help="Cache/download root (default: MOLQUARRY_HOME or .molquarry)"
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable response cache")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    sub.add_parser("categories", help="List data responsibility categories")
    sources = sub.add_parser("sources", help="Discover implemented/planned sources")
    sources.add_argument("--category", choices=list(CATEGORIES))
    sources.add_argument("--implemented", action="store_true")
    sources.add_argument("--query", help="Filter catalog metadata by text")
    describe = sub.add_parser("describe", help="Get operation schemas and examples")
    describe.add_argument("source")
    inhibitors = sub.add_parser(
        "inhibitors",
        aliases=["collect"],
        help="Collect public target/structure/activity/literature evidence",
    )
    inhibitors.add_argument("targets", nargs="*", help="Gene symbols or UniProt accessions")
    inhibitors.add_argument(
        "--config", type=Path, help="Strict InhibitorSearch JSON; excludes target arguments"
    )
    inhibitors.add_argument("--taxon", type=int, default=None)
    inhibitors.add_argument("--until", help="Literature FIRST_PDATE cutoff, YYYY-MM-DD")
    inhibitors.add_argument(
        "--output-dir", type=Path, help="New directory (default: timestamped folder under cwd)"
    )
    inhibitors.add_argument("--max-pages", type=int, default=None)
    inhibitors.add_argument("--max-details", type=int, default=None)
    inhibitors.add_argument("--mode", choices=["inhibitor", "agonist", "modulator", "ligand"])
    sourcing = sub.add_parser(
        "source-sdf", help="Check SDF identities, catalogs and synthesis evidence"
    )
    sourcing.add_argument("input_sdf", type=Path)
    sourcing.add_argument("--output-dir", type=Path)
    sourcing.add_argument("--max-mcule-queries", type=int, default=20)
    sourcing.add_argument("--workers", type=int, default=4)
    sourcing.add_argument("--local-catalog", type=str, action="append", default=[])
    sourcing.add_argument("--resume", action="store_true")
    sourcing.add_argument("--retry-errors", action="store_true")
    sourcing.add_argument("--no-unichem", action="store_true")
    inventory = sub.add_parser(
        "ligand-table", help="Export every collected compound and measurement"
    )
    inventory.add_argument("dossier", type=Path)
    inventory.add_argument("--output-dir", type=Path, required=True)
    inventory.add_argument("--sourcing", type=Path)
    inventory.add_argument("--highlights", type=Path)
    inventory.add_argument("--conformers", type=Path, help="Optional computed 3D index.json")
    conformers = sub.add_parser(
        "inventory-3d", help="Generate labeled 3D conformers for an inventory"
    )
    conformers.add_argument("inventory", type=Path)
    conformers.add_argument("--output-dir", type=Path, required=True)
    conformers.add_argument("--workers", type=int, default=2)
    bundle = sub.add_parser(
        "bundle", help="Export organized PDB/alignment, Excel, SDF and AF3 job files"
    )
    bundle.add_argument("dossier", type=Path)
    bundle.add_argument("curation", type=Path)
    bundle.add_argument("--output-dir", type=Path)
    bundle.add_argument("--no-purchasing", action="store_true")
    bundle.add_argument("--max-purchase-queries", type=int, default=20)
    bundle.add_argument("--max-structures", type=int)
    bundle.add_argument("--max-total-bytes", type=int, default=1000000000)
    af3_import = sub.add_parser(
        "af3-import", help="Import an actual downloaded AF3 ZIP and export PDB"
    )
    af3_import.add_argument("archive", type=Path)
    af3_import.add_argument("--output-dir", type=Path, required=True)
    af3_local = sub.add_parser(
        "af3-run", help="Run an explicitly configured local AF3 installation"
    )
    af3_local.add_argument("job_json", type=Path)
    af3_local.add_argument("--output-dir", type=Path, required=True)
    af3_local.add_argument("--python-executable", type=Path, required=True)
    af3_local.add_argument("--run-script", type=Path, required=True)
    af3_local.add_argument("--model-dir", type=Path, required=True)
    af3_local.add_argument("--database-dir", type=Path, required=True)
    review = sub.add_parser(
        "review-inhibitors", help="Validate and integrate explicit inhibitor curation"
    )
    review.add_argument("dossier", type=Path)
    review.add_argument("curation", type=Path)
    review.add_argument(
        "--output", type=Path, required=True, help="New JSON file; refuses overwrite"
    )
    for command in ("query", "plan", "download"):
        action = sub.add_parser(command)
        action.add_argument("source")
        action.add_argument("operation")
        inputs = action.add_mutually_exclusive_group()
        inputs.add_argument("--params", default="{}", help="Operation parameters as a JSON object")
        inputs.add_argument(
            "--params-file", type=Path, help="Read JSON parameters from a UTF-8 file"
        )
        if command == "download":
            action.add_argument("--output-dir", type=Path)
            action.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
        else:
            action.add_argument("--output", type=Path, help="Also save JSON (refuses overwrite)")
    fetch = sub.add_parser(
        "fetch", help="Download an existing saved plan without resolving it again"
    )
    fetch.add_argument("plan_file", type=Path)
    fetch.add_argument("--output-dir", type=Path)
    fetch.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    ingest = sub.add_parser("import-catalog", help="Import an authorized local CSV/TSV/SDF export")
    ingest.add_argument("source")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source-version", required=True)
    ingest.add_argument("--max-bytes", type=int, default=104857600)
    ingest.add_argument("--max-records", type=int, default=100000)
    sub.add_parser("local-catalogs", help="List imported snapshots")
    local = sub.add_parser(
        "local-search", help="Search a local snapshot by text or exact field value"
    )
    local.add_argument("snapshot_id")
    local.add_argument("--query", default="")
    local.add_argument("--field")
    local.add_argument("--value")
    local.add_argument("--limit", type=int, default=20)
    local.add_argument("--offset", type=int, default=0)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        with MolQuarry(home=args.home, cache=not args.no_cache) as quarry:
            if args.command == "categories":
                result = {"ok": True, "categories": CATEGORIES}
            elif args.command == "sources":
                result = {
                    "ok": True,
                    "sources": quarry.sources(
                        category=args.category, implemented_only=args.implemented, query=args.query
                    ),
                }
            elif args.command == "describe":
                result = {"ok": True, **quarry.describe(args.source)}
            elif args.command in {"inhibitors", "collect"}:
                if args.config and args.targets:
                    raise MolQuarryError(
                        "invalid_parameters", "Use --config or target arguments, not both"
                    )
                parameters = (
                    json.loads(args.config.read_text(encoding="utf-8"))
                    if args.config
                    else {"targets": args.targets}
                )
                for key, value in [
                    ("taxon", args.taxon),
                    ("literature_until", args.until),
                    ("max_pages", args.max_pages),
                    ("max_details", args.max_details),
                    ("mode", args.mode),
                ]:
                    if value is not None:
                        parameters[key] = value
                config = InhibitorSearch.model_validate(parameters)
                if args.output_dir is None:
                    from .workflows.bundle import default_output

                    args.output_dir = default_output(
                        [{"input": t} for t in config.targets], config.mode
                    )
                result = collect_inhibitor_evidence(
                    quarry,
                    config,
                    args.output_dir,
                    progress=lambda message: print(message, file=sys.stderr, flush=True),
                )
            elif args.command == "review-inhibitors":
                dossier = json.loads(args.dossier.read_text(encoding="utf-8"))
                curation = Curation.model_validate_json(args.curation.read_text(encoding="utf-8"))
                result = integrate_review(dossier, curation)
            elif args.command == "ligand-table":
                from .workflows.ligands import export_ligand_inventory

                result = export_ligand_inventory(
                    args.dossier,
                    args.output_dir,
                    sourcing_path=args.sourcing,
                    highlights=json.loads(args.highlights.read_text()) if args.highlights else None,
                    conformer_index_path=args.conformers,
                )
            elif args.command == "inventory-3d":
                from .workflows.conformers import export_inventory_conformers

                result = export_inventory_conformers(
                    args.inventory,
                    args.output_dir,
                    workers=args.workers,
                    progress=lambda message: print(message, file=sys.stderr),
                )
            elif args.command == "source-sdf":
                from .workflows.sourcing import SourcingConfig, screen_sdf

                result = screen_sdf(
                    quarry,
                    args.input_sdf,
                    args.output_dir,
                    config=SourcingConfig(
                        workers=args.workers,
                        max_mcule_queries=args.max_mcule_queries,
                        unichem=not args.no_unichem,
                        local_catalogs=args.local_catalog,
                    ),
                    resume=args.resume,
                    retry_errors=args.retry_errors,
                    progress=lambda message: print(message, file=sys.stderr),
                )
            elif args.command == "bundle":
                from .workflows.bundle import build_target_bundle

                result = build_target_bundle(
                    quarry,
                    args.dossier,
                    args.curation,
                    args.output_dir,
                    purchasing=not args.no_purchasing,
                    max_purchase_queries=args.max_purchase_queries,
                    max_structures=args.max_structures,
                    max_total_bytes=args.max_total_bytes,
                )
            elif args.command == "af3-import":
                from .workflows.af3 import import_af3_results

                result = import_af3_results(args.archive, args.output_dir)
            elif args.command == "af3-run":
                from .workflows.af3 import run_local_af3

                result = run_local_af3(
                    args.job_json,
                    args.output_dir,
                    python_executable=args.python_executable,
                    run_script=args.run_script,
                    model_dir=args.model_dir,
                    database_dir=args.database_dir,
                )
            elif args.command == "import-catalog":
                result = quarry.import_catalog(
                    args.source,
                    args.path,
                    source_version=args.source_version,
                    max_bytes=args.max_bytes,
                    max_records=args.max_records,
                )
            elif args.command == "local-catalogs":
                result = quarry.local_catalogs()
            elif args.command == "local-search":
                result = quarry.local_search(
                    snapshot_id=args.snapshot_id,
                    query=args.query,
                    field=args.field,
                    value=args.value,
                    limit=args.limit,
                    offset=args.offset,
                ).model_dump()
            elif args.command == "fetch":
                plan = DownloadPlan.model_validate_json(args.plan_file.read_text(encoding="utf-8"))
                result = quarry.download(
                    plan, output_dir=args.output_dir, max_bytes=args.max_bytes
                ).model_dump()
            else:
                parameters = json.loads(
                    args.params_file.read_text(encoding="utf-8")
                    if args.params_file
                    else args.params
                )
                if not isinstance(parameters, dict):
                    raise MolQuarryError("invalid_parameters", "Parameters must be a JSON object")
                if args.command == "query":
                    result = quarry.query(args.source, args.operation, **parameters).model_dump()
                else:
                    plan = quarry.plan_download(args.source, args.operation, **parameters)
                    result = (
                        quarry.download(plan, output_dir=args.output_dir, max_bytes=args.max_bytes)
                        if args.command == "download"
                        else plan
                    ).model_dump()
            output = json.dumps(result, indent=2, ensure_ascii=False)
            if getattr(args, "output", None):
                with args.output.open("x", encoding="utf-8") as file:
                    file.write(output + "\n")
            print(output)
        return 0
    except MolQuarryError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except ImportError as exc:
        error = MolQuarryError(
            "missing_dependency",
            f"Install the requested extra (e.g. molquarry[deliverables]): {exc}",
        )
        print(json.dumps(error.as_dict()), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, ValidationError) as exc:
        error = MolQuarryError(
            "invalid_input" if not isinstance(exc, OSError) else "filesystem_error", str(exc)
        )
        print(json.dumps(error.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
