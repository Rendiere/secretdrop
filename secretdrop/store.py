"""Pending drop requests.

A pending request records where a secret should go, never the secret itself.
State is one JSON file rewritten whole under an exclusive lock; the volume is a
handful of links at a time, so anything more would be overbuilding.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ID_BYTES = 24  # 192 bits, 32 URL-safe characters


class StoreCorrupt(RuntimeError):
    """The state file exists but cannot be parsed."""


@dataclass(frozen=True)
class DropRequest:
    id: str
    dest: str
    label: str
    mode: str
    key: str | None
    created_at: float
    expires_at: float
    # Which OpenClaw session to wake. `None` leaves it to the callback's own
    # default, so a drop made before this field existed still loads and behaves
    # exactly as it did.
    session: str | None = None

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    # --- disk -------------------------------------------------------------

    def _ensure_parent(self) -> None:
        """Create the state directory private. Never re-mode one we did not make.

        Pointing `state_path` at, say, `~/pending.json` must not chmod `$HOME`.
        """
        parent = self.path.parent
        if parent.exists():
            return
        parent.mkdir(parents=True, mode=0o700)
        os.chmod(parent, 0o700)

    def _read(self) -> dict[str, dict]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            # Treating this as empty would silently bin every pending link.
            raise StoreCorrupt(f"{self.path} is not readable JSON: {exc}") from exc

    def _write(self, data: dict[str, dict]) -> None:
        self._ensure_parent()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def _lock(self):
        self._ensure_parent()
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    @staticmethod
    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    @staticmethod
    def _purge(data: dict[str, dict]) -> dict[str, dict]:
        now = time.time()
        return {k: v for k, v in data.items() if v.get("expires_at", 0) > now}

    # --- api --------------------------------------------------------------

    def create(
        self,
        *,
        dest: str,
        label: str,
        mode: str,
        key: str | None,
        ttl_seconds: float,
        session: str | None = None,
    ) -> DropRequest:
        now = time.time()
        req = DropRequest(
            id=secrets.token_urlsafe(ID_BYTES),
            dest=dest,
            label=label,
            mode=mode,
            key=key,
            created_at=now,
            expires_at=now + ttl_seconds,
            session=session,
        )
        fd = self._lock()
        try:
            data = self._purge(self._read())
            data[req.id] = asdict(req)
            self._write(data)
        finally:
            self._unlock(fd)
        return req

    def get(self, drop_id: str) -> DropRequest | None:
        record = self._read().get(drop_id)
        if record is None:
            return None
        req = DropRequest(**record)
        return None if req.expired else req

    def consume(self, drop_id: str) -> DropRequest | None:
        """Take a request, atomically. A second caller gets `None`."""
        fd = self._lock()
        try:
            data = self._read()
            record = data.pop(drop_id, None)
            if record is None:
                return None
            self._write(self._purge(data))
            req = DropRequest(**record)
            return None if req.expired else req
        finally:
            self._unlock(fd)

    def revoke(self, drop_id: str) -> bool:
        fd = self._lock()
        try:
            data = self._read()
            existed = data.pop(drop_id, None) is not None
            if existed:
                self._write(self._purge(data))
            return existed
        finally:
            self._unlock(fd)

    def list_pending(self) -> list[DropRequest]:
        requests = [DropRequest(**r) for r in self._read().values()]
        live = [r for r in requests if not r.expired]
        return sorted(live, key=lambda r: r.created_at)
