"""Process lock so only one collector writes to a data directory."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class CollectorLocked(RuntimeError):
    pass


class CollectorLock:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "collector.lock"
        self._fd: int | None = None

    def __enter__(self) -> CollectorLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.read(fd, 32).decode().strip() or "unknown"
            os.close(fd)
            raise CollectorLocked(f"another collector (pid {holder}) is using {self.path.parent}") from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
