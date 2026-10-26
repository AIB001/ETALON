"""Bounded, request-local reuse of verified frozen sources.

Only decoding/projection and path resolution work is shared. Every outer read
revalidates original source/projected bytes and mapped locations before returning. Live sources are
never cached. An optional disposable cross-event cache must be checksum-bound by
its host receipt, and is revalidated against originals before any reuse.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
from threading import Lock

CURRENT = ContextVar("etalon_evidence_read_scope", default=None)
VERIFY_LOCATIONS = ContextVar("etalon_verification_locations", default=None)
MAX_SOURCE_VIEWS = 64
MAX_CACHE_BYTES = 32 * 1024 * 1024
CACHE_SCHEMA = "etalon-frozen-read-cache/1"
# Disposable process-local decode reuse for consecutive graph pages. No graph,
# visibility decision, counter closure, live state or hash verdict is retained.
_PAGE_SOURCES = None
_PAGE_LOCK = Lock()


def implementation_identity():
    """Bind disposable cached projections to the actual validation code."""
    root = Path(__file__).parent
    paths = sorted(root.glob("*.py")) + [root.parent / "active" / "schema.py"]
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in paths)).hexdigest()


def source_key(workspace, source):
    # Source projection depends on this source's allowlist and relocation only.
    # Cross-source visibility is recomputed by the ordinary collector each time.
    maps = workspace._source_mapping_payloads.get(source["source_id"], ())
    raw = json.dumps([str(workspace.base), source, maps], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class _LocationCache:
    def __init__(self, allowed=None):
        self.locations = {}
        self.bytes = 0
        self.allowed = allowed

    def remember_location(self, key, path):
        if self.allowed is not None and key not in self.allowed:
            return
        size = sum(len(str(value).encode("utf-8")) for value in (*key, path))
        if len(self.locations) < 8192 and self.bytes + size <= MAX_CACHE_BYTES:
            self.locations[key] = path
            self.bytes += size


class ReadScope(_LocationCache):
    def __init__(self, previous=None):
        super().__init__()
        self.workspaces = {}
        self.sources = {}
        self.collections = {}
        self.relation_indexes = {}
        self.relation_hits = 0
        self.relation_builds = 0
        self.deferred_frozen_checks = 0
        self.hits = 0
        self.reused_sources = 0
        self.fresh_sources = 0
        self.collection_hits = 0
        self.observed_files = {}
        self.previous = {}
        self.code_identity = implementation_identity()
        if (isinstance(previous, dict) and previous.get("schema") == CACHE_SCHEMA
                and previous.get("implementation") == self.code_identity
                and isinstance(previous.get("sources"), dict)
                and len(previous["sources"]) <= MAX_SOURCE_VIEWS):
            raw = json.dumps(previous["sources"], ensure_ascii=False)
            if len(raw.encode()) <= MAX_CACHE_BYTES:
                self.previous = previous["sources"]

    def remember_source(self, key, workspace, source, result):
        if result[1].get("status") != "complete":
            return
        raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        size = len(raw.encode("utf-8"))
        if len(self.sources) >= MAX_SOURCE_VIEWS or self.bytes + size > MAX_CACHE_BYTES:
            # The ordinary source reader still supplies its bounded result.
            # Refusing a cache admission must not suppress evidence or counters.
            return
        self.sources[key] = workspace, source, raw
        self.bytes += size

    def reuse_source(self, key, workspace, source):
        from .continuation import _check_frozen_view
        cached = self.previous.get(key)
        if cached is None:
            return None
        rows, coverage = json.loads(cached)
        # Never trust mtime, a previous check, or the projection cache as evidence.
        _check_frozen_view(workspace, {source["source_id"]: rows}, [coverage])
        self.reused_sources += 1
        return rows, coverage

    def snapshot(self):
        return {"schema": CACHE_SCHEMA, "implementation": self.code_identity,
                "sources": {key: value[2] for key, value in self.sources.items()}}

    def defer_frozen_check(self, workspace, records, coverage):
        """Defer a redundant nested check only for the exact registered view.

        This does not reuse a hash verdict. ``verify`` checks every registered
        original with CURRENT reset before the outer reader returns. Sources
        refused by the byte budget, live/partial views, and first cross-event
        cache admissions retain the ordinary immediate validation path.
        """
        if not coverage or any(c.get("status") != "complete" for c in coverage):
            return False
        for cov in coverage:
            source = workspace.source(cov["source_id"])
            if source["identity"]["kind"] != "snapshot_sha256":
                return False
            saved = self.sources.get(source_key(workspace, source))
            if saved is None:
                return False
            rows, observed = json.loads(saved[2])
            if (rows != records.get(cov["source_id"]) or any(observed.get(key) != cov.get(key)
                    for key in ("status", "snapshot", "read_watermark"))):
                return False
        self.deferred_frozen_checks += 1
        return True

    def stats(self):
        return {"fresh_sources": self.fresh_sources, "reused_sources": self.reused_sources,
                "within_read_hits": self.hits, "collection_hits": self.collection_hits, "cached_bytes": self.bytes,
                "relation_index_hits": self.relation_hits, "relation_index_builds": self.relation_builds,
                "deferred_frozen_checks": self.deferred_frozen_checks,
                "verification": "original_source_and_projection_bytes_rechecked",
                "scope": "source_local_decode_only_visibility_and_counter_search_recomputed"}

    def observe_file(self, path, identity):
        from .evidence_refs import EvidenceError
        key = str(path)
        if key in self.observed_files and self.observed_files[key] != identity:
            raise EvidenceError("entry, workspace or authority changed during read")
        self.observed_files[key] = identity

    def verify(self):
        from .evidence_refs import load_workspace
        from .evidence_refs import file_identity, EvidenceError, _local
        from .continuation import _check_frozen_view
        # CURRENT is reset: validation cannot accidentally consult this cache.
        # Several policy views can share the same mapped locations. Resolve
        # each once in a NEW final-validation scope, without reusing the paths
        # seen while building the result. This cache holds no file identities.
        # Paths declined by the original cache budget have no final path
        # comparison; leave their validation resolutions uncached as before.
        token = VERIFY_LOCATIONS.set(_LocationCache(allowed=self.locations))
        try:
            for workspace in self.workspaces.values():
                load_workspace(workspace)
            for workspace, source, raw in self.sources.values():
                rows, coverage = json.loads(raw)
                _check_frozen_view(workspace, {source["source_id"]: rows}, [coverage])
        finally:
            VERIFY_LOCATIONS.reset(token)
        for path, expected in self.observed_files.items():
            if file_identity(path) != expected:
                raise EvidenceError("entry, workspace or authority changed during read")
        for (base, location, confined), expected in self.locations.items():
            # A final uncached resolution still catches retargeted links.
            if _local(Path(base), location, confined=confined) != expected:
                raise EvidenceError("evidence location changed during read")


@contextmanager
def verified_read(previous=None):
    current = CURRENT.get()
    if current is not None:
        yield current
        return
    scope = ReadScope(previous)
    token = CURRENT.set(scope)
    try:
        yield scope
    finally:
        CURRENT.reset(token)
    scope.verify()


def bounded_read(function):
    @wraps(function)
    def read(*args, **kwargs):
        if CURRENT.get() is not None:
            return function(*args, **kwargs)
        with verified_read():
            result = function(*args, **kwargs)
        return result
    return read


def paged_read(function):
    """Reuse the existing frozen-source cache across scoped page calls.

    One bounded snapshot serves the process; eviction or a fresh host merely
    causes ordinary decoding. Originals are checked before source reuse and
    again before return, while policy, counter search and continuation state
    are rebuilt by the ordinary reader. Nothing is placed in the page cursor.
    """
    ordinary = bounded_read(function)

    @wraps(function)
    def read(*args, **kwargs):
        global _PAGE_SOURCES
        if CURRENT.get() is not None or kwargs.get("scope") is None:
            return ordinary(*args, **kwargs)
        with _PAGE_LOCK:
            previous = _PAGE_SOURCES
        with verified_read(previous) as scope:
            result = function(*args, **kwargs)
        # Only successful outer verification can seed a later page. Strings
        # are immutable and snapshot() never exposes mutable cached records.
        snapshot = scope.snapshot()
        if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_CACHE_BYTES:
            snapshot = None
        with _PAGE_LOCK:
            _PAGE_SOURCES = snapshot
        return result
    return read
