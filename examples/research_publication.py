"""Archive existing outputs; inspect/resume an interrupted publication.

Run with ETALON installed. Prepare consumes a JSON object containing the explicit
prepare_publication keyword arguments (including expected_entry_identity).
No command starts scientific computation or supplies execution authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.research.publication import (
    discover_publications, inspect_publication, prepare_publication, resume_publication,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("request", type=Path)
    commands.add_parser("inspect").add_argument("intent", type=Path)
    commands.add_parser("resume").add_argument("intent", type=Path)
    commands.add_parser("discover").add_argument("entry", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_publication(**json.loads(args.request.read_text("utf-8-sig")))
    elif args.command == "inspect":
        result = inspect_publication(args.intent)
    elif args.command == "resume":
        result = resume_publication(args.intent)
    else:
        result = discover_publications(args.entry)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
