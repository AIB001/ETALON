"""Resumable, request-keyed public query evidence shared by batch workflows."""

import hashlib
import json
import threading
from pathlib import Path

from ..errors import MolQuarryError
from ..models import utcnow


def digest(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class QueryJournal:
    def __init__(self, quarry, directory, *, retry_errors=False):
        self.quarry = quarry
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.retry_errors = retry_errors
        self._guard = threading.Lock()
        self._locks = {}

    def query(self, source, operation, **parameters):
        request = {"source": source, "operation": operation, "parameters": parameters}
        key = digest(request)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            path = self.directory / f"{source}-{key}.json"
            if path.exists():
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["request"] != request:
                    raise ValueError("Query journal request identity differs")
                if row["status"] in {"ok", "not_found"} or not self.retry_errors:
                    return row
            row = {"request": request, "query_id": key, "checked_at": utcnow()}
            try:
                result = self.quarry.query(source, operation, **parameters)
                row.update(status="ok", result=result.model_dump())
            except MolQuarryError as exc:
                row.update(status=exc.code, error=exc.as_dict()["error"])
            temporary = path.with_suffix(".part")
            temporary.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            return row

    def records(self):
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.directory.glob("*.json"))
        ]
