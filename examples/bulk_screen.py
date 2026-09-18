"""Submit a real, small CPU filtering cascade. No target affinity is measured.

python examples/bulk_screen.py --workspace runs/bulk-screen
python -m etalon screen status --workspace runs/bulk-screen --run-id cpu-example

Supply --library for your own CSV with id,smiles columns. Review these illustrative
property bounds before using them for any scientific selection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.campaign.design import component, compose
from etalon.screening import plan_screen, submit_screen


def write_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"existing example input differs: {path}; choose a fresh workspace")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = compose("illustrative-property-screen", [{"id": "filter", "title": "Illustrative bounds",
        "criteria": [component("window", "chemistry.rdkit_property_range_gate@0.1.0",
                               settings={"mw_min": 100, "mw_max": 500})]},
        {"id": "measure", "title": "Survivor descriptors",
         "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])
    config_path = root / "cpu.cascade.json"
    write_once(config_path, json.dumps(config, indent=2) + "\n")
    library = args.library.resolve() if args.library else root / "library.csv"
    if args.library is None:
        write_once(library, "id,smiles\naspirin,CC(=O)Oc1ccccc1C(=O)O\n"
                   "caffeine,Cn1c(=O)c2c(ncn2C)n(C)c1=O\nethanol,CCO\n"
                   + "long-alkane," + "C" * 60 + "\ninvalid,not_a_smiles\n")
    plan = plan_screen(config_path, library, root)
    job = submit_screen(config_path, library, root, run_id="cpu-example", expected_plan_id=plan["plan_id"],
                        devices=("cpu",))
    print(json.dumps(job, indent=2))


if __name__ == "__main__":
    main()
