"""Measurement artifacts: one immutable file per measurement (review finding 1, ADR-0013).

A ``trials`` row, a gate-④ result and a portfolio re-run all point at files under
``results/``. Keying those files by ``candidate_id`` let a later measurement of the same id
(calibration's confirmation run) overwrite the returns an earlier row points to — rewriting
history under an append-only ledger. Every measurement therefore gets its own path, and the file
is created exclusively: writing to an existing path raises instead of replacing it.
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pandas as pd

# Characters a file name may not hold on Windows. ``:`` comes from a perpetual scope
# (``BTC/USDT:USDT``) and is legal on Linux, so only a Windows run ever met it.
_UNSAFE = re.compile(r'[<>:"/\\|?*]')


def measurement_path(base: Path, candidate_id: str, suffix: str = ".parquet") -> Path:
    """A fresh path ``base/<candidate_id>/<unique id><suffix>`` for one measurement."""
    safe = _UNSAFE.sub("_", candidate_id)
    return base / safe / f"{uuid.uuid4().hex}{suffix}"


def write_once(path: Path, data: bytes) -> Path:
    """Create ``path`` with ``data``; raise ``FileExistsError`` if it already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(data)
    return path


def write_parquet_once(path: Path, frame: pd.DataFrame) -> Path:
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    return write_once(path, buf.getvalue())
