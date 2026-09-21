"""Create a campaign from explicit scientific endpoints without launching experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etalon.active.schema import CampaignSpec, Endpoint
from etalon.active.store import CampaignStore


def create_campaign(database: Path, spec: dict[str, Any], endpoints: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate before creating a new journal. Existing paths are never overwritten."""
    if not isinstance(spec, dict) or not isinstance(endpoints, list):
        raise ValueError("campaign setup requires a spec mapping and an endpoint list")
    configuration = CampaignSpec(**spec)
    definitions = [Endpoint(**endpoint) for endpoint in endpoints]
    ids = {endpoint.id for endpoint in definitions}
    if len(ids) != len(definitions) or configuration.objective not in ids:
        raise ValueError("endpoint ids must be unique and include the objective")
    if len({endpoint.target for endpoint in definitions}) != 1:
        raise ValueError("a campaign models one target; create a separate campaign per target")
    requested = Path(database).expanduser()
    if any(Path(str(requested) + suffix).exists() or Path(str(requested) + suffix).is_symlink()
           for suffix in ("", "-wal", "-shm", "-journal")):
        raise FileExistsError("campaign journal or SQLite sidecar already exists; inspect it before creating a new campaign")
    path = requested.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    store = CampaignStore(path)
    store.configure(configuration, definitions)
    return {"database": str(path), "spec": configuration.as_dict(),
            "endpoints": [endpoint.as_dict() for endpoint in definitions],
            "state": "configured", "candidates": 0, "executed_actions": 0}
