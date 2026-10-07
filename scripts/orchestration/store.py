"""Durable building blocks: atomic writes, canonical hashing, a cross-process lock and a journal.

Everything the executor decides is derived from artifacts on disk and from this
journal, never from memory.  A crash between two calls therefore loses nothing:
the next call recomputes the same answer from the same files.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from scripts.checks.common import InputError

MAX_JOURNAL_BYTES = 64 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 60.0


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def digest_text(text: str) -> str:
    return digest_bytes(text.encode("utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return digest_text(canonical(value))


def atomic_text(path: Path, text: str) -> str:
    """Write ``text`` so a reader sees the old file or the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows refuses while a reader, a scanner or the monitor still holds the file.
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return digest_file(path)


def atomic_json(path: Path, value: Any) -> str:
    return atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{path.name} is not valid JSON") from exc


class FileLock:
    """An operating-system lock, released automatically if the holder dies.

    A pid file would need a liveness probe, and ``os.kill(pid, 0)`` terminates the
    target on Windows.  An OS lock needs no probe: it simply disappears with its owner.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None
        self.guard = threading.RLock()

    def _try(self) -> bool:
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @contextmanager
    def held(self, timeout: float = LOCK_TIMEOUT_SECONDS, busy: str | None = None) -> Iterator[None]:
        with self.guard:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(self.path, "a+b")
            deadline = time.monotonic() + timeout
            try:
                while not self._try():
                    if time.monotonic() >= deadline:
                        raise InputError(busy or "another executor operation holds this swarm; try again shortly")
                    time.sleep(0.05)
                yield
            finally:
                try:
                    if os.name == "nt":
                        import msvcrt

                        self.handle.seek(0)
                        try:
                            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        import fcntl

                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self.handle.close()
                    self.handle = None


class Journal:
    """Append-only record of what the executor did, one JSON object per line."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.guard = threading.Lock()
        self._sequence: int | None = None

    def stamp(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        if self.path.stat().st_size > MAX_JOURNAL_BYTES:
            raise InputError("the executor journal exceeds the supported size")
        lines = self.path.read_text(encoding="utf-8").split("\n")
        events = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                # Only a torn final append is recoverable; corruption elsewhere is not hidden.
                if index == len(lines) - 1 or not any(rest.strip() for rest in lines[index + 1:]):
                    break
                raise InputError(f"the executor journal is corrupted at line {index + 1}") from exc
            if isinstance(item, dict):
                events.append(item)
        return events

    def append(self, event: str, **fields: Any) -> dict[str, Any]:
        with self.guard:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._repair()
            if self._sequence is None:
                self._sequence = len(self.events())
            self._sequence += 1
            record = {"seq": self._sequence, "at": self.stamp(), "event": event, **fields}
            with open(self.path, "a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return record

    def _repair(self) -> None:
        """Drop a half-written final line so the next append starts on a clean one."""
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return
        data = self.path.read_bytes()
        if data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1
        with open(self.path, "r+b") as stream:
            stream.truncate(cut)
        self._sequence = None

    def find(self, event: str, **match: Any) -> list[dict[str, Any]]:
        return [item for item in self.events()
                if item.get("event") == event and all(item.get(key) == value for key, value in match.items())]

    def count(self, event: str, **match: Any) -> int:
        return len(self.find(event, **match))
