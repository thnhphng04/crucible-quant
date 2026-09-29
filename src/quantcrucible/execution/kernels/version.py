"""Which kernel build produced a result (ADR-0038).

``KERNEL_VERSION`` hashes the kernel sources, so an audit that refuses a build refuses exactly
that code: any edit to a kernel is a new version that has to earn trust again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_SOURCES = ("_numba.py", "program.py", "features.py", "signals.py", "replay.py", "backend.py")


def _digest() -> str:
    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in _SOURCES:
        h.update(name.encode())
        h.update(b"\0")
        h.update((here / name).read_bytes().replace(b"\r\n", b"\n"))  # same on Windows and Linux
        h.update(b"\0")
    return h.hexdigest()[:16]


KERNEL_VERSION = _digest()
