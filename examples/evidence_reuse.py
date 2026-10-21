"""Optional ordinary-record publication and bounded reads through the actual SDK.

Reading never creates a fixture, writes a source or registers a method. Supply
an existing workspace manifest; a known ref bypasses query. Explicit publication
copies named journals with SQLite's backup API and writes a new workspace. It
never inserts a semantic projection or changes the original records. The host
chooses what to publish, query, read, use or skip; none is an execution gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3


def record_claim(journal, claim, directory, *, source_kind, role="diagnostic"):
    """Optionally save a caller's claim as an ordinary, immutable JSON Artifact.

    Claim content/refs come from the caller, not this example. This validates
    the content contract, not its truth, scientific admission or hidden-data
    isolation. For revisions use method_revision.record_method_revision directly.
    """
    from etalon.active.schema import canonical, digest
    from etalon.boundary.prism_v1 import file_identity
    from etalon.research import Artifact
    from etalon.research.evidence_refs import validate_claim

    content = validate_claim(claim)
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest(content) + ".json")
    data = (canonical(content) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("existing claim bytes conflict with immutable content") from None
    artifact = Artifact(str(path), **file_identity(path), source_kind=source_kind, role=role,
                        bindings={"kind": "research_claim", "schema": content["schema"]})
    # File first, then the ordinary journal record. A failed DB write leaves the
    # verifiable file for recovery; this helper does not delete or overwrite it.
    return journal.record(artifact)


def publish_journals(directory, journals, *, workspace_id):
    """Explicitly snapshot caller-named published journals into a new workspace.

    journals maps stable source IDs to existing local databases. Only snapshot
    locations/identities are written to the manifest, never record text, theme
    hints or expected answers. Original Artifact paths/roles/costs/IDs stay intact.
    Originals must remain accessible (or use an explicit verified location_map).
    This publishes entire named journals; use only sources safe to expose. Role
    filtering does not isolate sealed files from a process that can read them.
    """
    from etalon.boundary.prism_v1 import file_identity
    from etalon.research.evidence_refs import load_workspace

    if not isinstance(journals, dict) or not journals:
        raise ValueError("explicit source_id to journal-path mapping required")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ValueError("workspace_id is required")
    inputs = {}
    filenames = set()
    for source_id, value in journals.items():
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", source_id):
            raise ValueError("source_id must be a safe, stable filename component")
        if source_id.casefold() in filenames:
            raise ValueError("source IDs must have distinct case-insensitive snapshot filenames")
        filenames.add(source_id.casefold())
        path = Path(value).absolute()
        if not path.is_file():
            raise FileNotFoundError(path)
        inputs[source_id] = path
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=False)
    sources = []
    for source_id, original in inputs.items():
        target = directory / (source_id + ".sqlite")
        source = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
        try:
            source.execute("PRAGMA query_only=ON")
            # Check the existing journal tables without constructing CampaignStore.
            source.execute("SELECT key, body FROM metadata LIMIT 0")
            source.execute("SELECT sequence FROM events LIMIT 0")
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        sources.append({"source_id": source_id, "kind": "journal", "location": target.name,
                        "identity": {"kind": "snapshot_sha256", "value": file_identity(target)["sha256"]}})
    path = directory / "etalon-workspace.json"
    manifest = {"schema": "etalon-evidence-workspace/1", "workspace_id": workspace_id,
                "sources": sources, "location_map": [], "status_refs": []}
    with path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    load_workspace(path)
    return path


def inspect_evidence(workspace, *, query="", filters=None, cursor=None, ref=None,
                     view="record", member=None, selector=None, offset=0, limit=10):
    """Return one SDK response; the caller decides whether to read anything else."""
    from etalon.research.evidence import query_evidence, read_evidence

    if ref is not None:
        if query or filters is not None or cursor is not None:
            raise ValueError("known-ref reading cannot also specify query/filter/cursor")
        return read_evidence(workspace, ref, view=view, member=member,
                             selector=selector, offset=offset, limit=limit)
    if view != "record" or member is not None or selector is not None or offset:
        raise ValueError("view/member/selector/offset require a known ref")
    return query_evidence(workspace, query=query, filters=filters, limit=limit, cursor=cursor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--workspace", type=Path,
                        help="Existing published etalon-workspace.json")
    action.add_argument("--publish-to", type=Path, help="Create a new snapshot/workspace directory")
    parser.add_argument("--journal", action="append", default=[], metavar="SOURCE_ID=PATH",
                        help="Explicit journal to publish; repeat for multiple sources")
    parser.add_argument("--workspace-id", help="Stable project workspace ID for publication")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--query", default="", help="Optional text match; empty lists one page")
    mode.add_argument("--ref", type=Path, help="JSON file containing one known EvidenceRef")
    parser.add_argument("--filters", type=json.loads, help="SDK filter object as JSON")
    parser.add_argument("--cursor", help="Explicit continuation from a prior query")
    parser.add_argument("--view", choices=("record", "summary", "lineage", "artifact"),
                        default="record")
    parser.add_argument("--member")
    parser.add_argument("--selector", choices=("bytes",))
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    if args.publish_to is not None:
        if (args.query or args.ref or args.filters is not None or args.cursor or args.view != "record"
                or args.member or args.selector or args.offset or args.limit != 10):
            parser.error("publication and query/read are separate optional operations")
        journals = {}
        for entry in args.journal:
            source_id, separator, location = entry.partition("=")
            if not separator or not location or source_id in journals:
                parser.error("--journal requires unique SOURCE_ID=PATH entries")
            journals[source_id] = location
        workspace = publish_journals(args.publish_to, journals, workspace_id=args.workspace_id)
        print(json.dumps({"workspace": str(workspace), "scope": "explicit journal snapshots; no semantic projection written"}))
        return 0
    if args.journal or args.workspace_id is not None:
        parser.error("--journal/--workspace-id apply only to --publish-to")
    ref = json.loads(args.ref.read_text(encoding="utf-8")) if args.ref else None
    if args.ref and not isinstance(ref, dict):
        parser.error("--ref must contain an EvidenceRef JSON object")
    response = inspect_evidence(
        args.workspace, query=args.query, filters=args.filters, cursor=args.cursor,
        ref=ref, view=args.view, member=args.member, selector=args.selector,
        offset=args.offset, limit=args.limit,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
