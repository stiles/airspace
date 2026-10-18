"""This process's memory, for spotting slow growth in long runs. Standard library only."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from dataclasses import dataclass

# proc_pid_rusage flavor and the byte offsets of two uint64 fields in rusage_info_v2
# (after a 16-byte UUID: user/system time, wakeups x2, pageins, wired size, resident size, footprint)
_RUSAGE_INFO_V2 = 2
_RESIDENT_OFFSET = 64
_FOOTPRINT_OFFSET = 72


@dataclass(frozen=True)
class Memory:
    # Physical footprint: what macOS Activity Monitor calls Memory. RSS also counts
    # freed pages the OS hasn't reclaimed, so it overstates growth.
    footprint_bytes: int | None
    rss_bytes: int | None

    def as_dict(self) -> dict:
        mb = 1024 * 1024
        return {
            "footprint_mb": round(self.footprint_bytes / mb, 1) if self.footprint_bytes is not None else None,
            "rss_mb": round(self.rss_bytes / mb, 1) if self.rss_bytes is not None else None,
        }


def _darwin() -> Memory:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    buffer = ctypes.create_string_buffer(512)  # larger than rusage_info_v2
    if libc.proc_pid_rusage(os.getpid(), _RUSAGE_INFO_V2, buffer) != 0:
        return Memory(None, None)
    read = lambda offset: int.from_bytes(buffer.raw[offset : offset + 8], sys.byteorder)
    return Memory(read(_FOOTPRINT_OFFSET), read(_RESIDENT_OFFSET))


def _linux() -> Memory:
    fields = {}
    with open("/proc/self/status") as f:
        for line in f:
            key, _, value = line.partition(":")
            if key in ("VmRSS", "RssAnon"):
                fields[key] = int(value.split()[0]) * 1024
    # Anonymous resident memory is the closest Linux analogue to the macOS footprint
    return Memory(fields.get("RssAnon"), fields.get("VmRSS"))


def memory() -> Memory:
    try:
        if sys.platform == "darwin":
            return _darwin()
        if sys.platform.startswith("linux"):
            return _linux()
    except (OSError, AttributeError, ValueError):
        pass
    return Memory(None, None)
