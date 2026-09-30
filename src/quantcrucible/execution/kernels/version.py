"""Which kernel build produced a result (ADR-0038).

``KERNEL_VERSION`` hashes the kernel sources, so an audit that refuses a build refuses exactly
that code: any edit to a kernel is a new version that has to earn trust again. The report
code the kernel engine shares with the sandbox is part of its answer, so it is hashed too.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_SOURCES = ("_numba.py", "program.py", "features.py", "signals.py", "replay.py", "backend.py")
_REPORT = ("backtest_report.py", "feature_stats.py")  # in quantcrucible/validation


def _digest() -> str:
    here = Path(__file__).resolve().parent
    validation = here.parent.parent / "validation"
    h = hashlib.sha256()
    for name, path in [(n, here / n) for n in _SOURCES] + [(n, validation / n) for n in _REPORT]:
        h.update(name.encode())
        h.update(b"\0")
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))  # same on Windows and Linux
        h.update(b"\0")
    return h.hexdigest()[:16]


KERNEL_VERSION = _digest()
