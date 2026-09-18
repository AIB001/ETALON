"""The campaign's memory: append-only, addressable, and unable to forget.

A campaign that tunes itself needs a record, and the obvious design -- keep the current
configuration and overwrite it each round -- destroys the only thing that makes the tuning
reviewable. Three rounds later the question is not "what is the configuration" but "which
measurements moved this threshold, and were they admissible". A file holding the present
state cannot answer it.

So the ledger is append-only. Every round is one JSON line carrying what was screened,
what was measured, what was admitted, what was refused and why, the waivers in force, and
the digests of the two infrastructure packages that produced all of it. Nothing is ever
edited. A round that turned out to be wrong is not removed; a later line says so.

Undoing works the same way, and this is the part that is deliberately awkward. There is no
delete. :meth:`Ledger.rewind` appends a line declaring which round the campaign has
returned to and why, and the rounds in between stay exactly where they are. Replaying the
ledger then yields the configuration as of the target, while the record still shows that
the intervening rounds happened and were abandoned. An agent that can erase its own
history can also erase the evidence that it was going in the wrong direction, and the
entire value of a self-tuning campaign rests on that evidence being there.

One consequence worth stating: the ledger is the authority and the configuration file is a
derivative. ``replay`` rebuilds the accepted parameter changes from the lines; if a config
on disk disagrees, the config is wrong. That ordering is what makes a campaign auditable
by someone who was not there.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from etalon.campaign.configuration import merge_changes


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant: {value}")


def _validate_decision(kind: str, body: dict[str, Any]) -> None:
    if kind != "round" or body.get("decision") is None:
        return
    decision = body["decision"]
    if not isinstance(decision, dict):
        raise ValueError("a round decision must be an object or null")
    if "accepted" in decision and type(decision["accepted"]) is not bool:
        raise ValueError("round decision.accepted must be an explicit boolean")


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of the ledger."""

    kind: str
    round_id: str
    at: str
    body: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {**self.body, "kind": self.kind, "round_id": self.round_id, "at": self.at}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Entry:
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(key), str) or not payload[key].strip() for key in ("kind", "round_id")
        ):
            raise ValueError("ledger kind and round_id must be nonempty strings")
        body = {k: v for k, v in payload.items() if k not in ("kind", "round_id", "at")}
        _validate_decision(payload["kind"], body)
        return cls(
            kind=str(payload["kind"]),
            round_id=str(payload["round_id"]),
            at=str(payload.get("at", "")),
            body=body,
        )


class Ledger:
    """Append-only JSONL record with cooperative, nonblocking POSIX file locks.

    A sidecar lock coordinates this API's readers and writers across threads/processes.
    Contention fails explicitly for the caller to retry; non-POSIX platforms fail closed.
    This is not protection against external editors that ignore or replace the lock file,
    nor a transaction spanning an entire legacy scientific execution.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        try:
            import fcntl
        except ImportError:
            raise OSError("legacy ledger concurrency requires POSIX flock; this platform is unsupported") from None
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise OSError("legacy ledger lock opening requires O_NOFOLLOW; this platform is unsupported")
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        # Never follow a sidecar symlink, including a dangling one that O_CREAT
        # would otherwise materialize elsewhere. Nonblocking open also lets us
        # reject FIFOs immediately before attempting the cooperative file lock.
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | nofollow, 0o666)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(f"legacy ledger lock must be a regular file: {lock_path}")
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BlockingIOError(f"legacy ledger is busy: {self.path}; retry after the active reader/writer finishes") from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    # -- writing ------------------------------------------------------------

    def append(self, kind: str, round_id: str, **body: Any) -> Entry:
        """Add one line. Flushed and fsynced before returning.

        The fsync is not caution for its own sake. A campaign round can be hours of GPU
        time, and a ledger line that was in a buffer when the machine went down records
        nothing about work that really happened -- which is worse than a missing round,
        because the next replay will believe the previous configuration is current.
        """

        if any(not isinstance(value, str) or not value.strip() for value in (kind, round_id)):
            raise ValueError("ledger kind and round_id must be nonempty strings")
        if "at" in body:
            raise ValueError("ledger timestamp is generated by append, not caller payload")
        _validate_decision(kind, body)
        entry = Entry(kind=kind, round_id=round_id, at=_now(), body=body)
        line = json.dumps(entry.as_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self._locked(exclusive=True):
            # Do not call entries()/rounds() here: they acquire a separate shared lock.
            # The uniqueness check and append must hold ONE exclusive lock throughout.
            entries = self._entries_unlocked()
            if kind == "round" and any(e.kind == "round" and e.round_id == round_id for e in entries):
                raise ValueError("completed round ids must be unique; record a new round")
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return entry

    def rewind(self, to_round_id: str, *, reason: str, by: str) -> Entry:
        """Declare a return to an earlier round without deleting anything.

        Raises:
            KeyError: If no such round exists. Rewinding to a round that was never
                recorded would silently produce a campaign state nothing can reconstruct.
        """

        if not any(entry.round_id == to_round_id for entry in self.rounds()):
            raise KeyError(
                f"no round {to_round_id!r} in {self.path.name}; a rewind to a round that "
                "was never recorded would leave a state nothing can replay"
            )
        if not isinstance(by, str) or not by.strip():
            raise ValueError("a rewind must name who requested it")
        if not isinstance(reason, str) or len(reason.strip()) < 16:
            raise ValueError(
                "a rewind needs a reason someone can read later. This is the one line "
                "explaining why a branch of the campaign was abandoned."
            )
        return self.append(
            "rewind",
            round_id=f"rewind->{to_round_id}",
            target=to_round_id,
            reason=reason,
            by=by,
        )

    # -- reading ------------------------------------------------------------

    def entries(self) -> tuple[Entry, ...]:
        with self._locked(exclusive=False):
            return self._entries_unlocked()

    def _entries_unlocked(self) -> tuple[Entry, ...]:
        """Read only while the caller holds this ledger's shared or exclusive lock."""
        if not self.path.is_file():
            return ()
        out: list[Entry] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            try:
                out.append(Entry.from_dict(json.loads(text, parse_constant=_reject_constant)))
            except (ValueError, TypeError, KeyError) as error:
                # Refused rather than skipped. A ledger with an unreadable line cannot be
                # replayed honestly, and silently ignoring one produces a configuration
                # that is missing a decision nobody knows about.
                raise ValueError(
                    f"{self.path}:{number} is not a readable ledger entry ({error}). The "
                    "ledger is the authority for the campaign's configuration, so a line "
                    "that cannot be read is a gap rather than a nuisance."
                ) from error
        return tuple(out)

    def rounds(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries() if entry.kind == "round")

    def effective(self) -> tuple[Entry, ...]:
        """The rounds that still count, after every rewind is applied.

        Each completed round remembers its own ancestry. Rewinding restores that branch,
        even if a later rewind had abandoned it; newly appended rounds continue from the
        restored target. Rewinds to partial, future or nonexistent rounds fail closed.
        """

        return self._effective(self.entries())

    @staticmethod
    def _effective(entries: tuple[Entry, ...]) -> tuple[Entry, ...]:
        live: list[Entry] = []
        history: dict[str, tuple[Entry, ...]] = {}
        for entry in entries:
            if entry.kind == "round":
                if entry.round_id in history:
                    raise ValueError(f"ambiguous duplicate completed round: {entry.round_id}")
                live.append(entry)
                history[entry.round_id] = tuple(live)
                continue
            if entry.kind != "rewind":
                continue
            target = str(entry.body.get("target", ""))
            if target not in history:
                raise ValueError(f"rewind target is not a preceding completed round: {target!r}")
            live = list(history[target])
        return tuple(live)

    def abandoned(self) -> tuple[Entry, ...]:
        """Rounds that happened and no longer count. Never deleted, always visible."""

        entries = self.entries()
        live = {entry.round_id for entry in self._effective(entries)}
        return tuple(entry for entry in entries if entry.kind == "round" and entry.round_id not in live)

    def replay(self) -> dict[str, Any]:
        """The campaign's state as the ledger defines it.

        The accepted parameter changes only. A round whose calibration verdict was
        ``within_noise`` or ``worse`` contributed nothing to the configuration, and that
        is the point of recording the verdict beside the change: the ledger holds every
        proposal and the state holds only what earned its place.
        """

        entries = self.entries()
        live = self._effective(entries)
        live_ids = {entry.round_id for entry in live}
        state: dict[str, Any] = {}
        accepted: list[str] = []
        refused: list[str] = []
        for entry in live:
            decision = entry.body.get("decision") or {}
            change = entry.body.get("change") or {}
            if decision.get("accepted") and change:
                state = merge_changes(state, change)
                accepted.append(entry.round_id)
            elif change:
                refused.append(entry.round_id)
        return {
            "rounds_live": [entry.round_id for entry in live],
            "rounds_abandoned": [entry.round_id for entry in entries
                                 if entry.kind == "round" and entry.round_id not in live_ids],
            "changes_accepted_in": accepted,
            "changes_refused_in": refused,
            "parameters": state,
        }

    def last_round(self) -> Entry | None:
        live = self.effective()
        return live[-1] if live else None


__all__ = ["Entry", "Ledger"]
