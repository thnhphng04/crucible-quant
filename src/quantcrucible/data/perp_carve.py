"""The perpetual holdout carve (Architecture §4.2, ADR-0032, ADR-0034, P3-22) — P6, INV-94.

The spot carve splits one series per instrument. A perpetual one has to split **four** — trade
bars, mark bars, funding and the intrabar paths — and split them at exactly the same bar. A mark
series cut one bar differently from the trade series would mark every out-of-sample position at
its neighbour's price, and nothing anywhere would raise.

So this does not split the files. It slices the :class:`PerpBundle`, whose one windowing operation
moves all four together and renumbers ``bar_ix`` (ADR-0034 decisions 1 and 3), and then writes the
two halves. The failure mode is therefore not "remembered to keep them in step" but "cannot
express them out of step".

**The second holdout lives *inside* the first's directory** — ``<holdout>/perp/`` with
``<holdout>/perp.lock``. That is deliberate: the guard hook matches anything under the holdout
directory, so nesting protects the new carve with no change to the rail itself. A sibling
directory would not be matched, and `tests/tooling/test_guard_paths.py` pins both halves of that
reasoning so nobody moves it without the hook being widened in the same commit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from quantcrucible.core.perp_inputs import PerpBundle, write_bundle
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.holdout_split import (
    CarveSummary,
    HoldoutLockError,
    make_read_only,
    sha256_file,
)
from quantcrucible.data.perp_source import CoverageError
from quantcrucible.data.store import _day_start, file_name, write_bars


def carve_perp(
    data: Mapping[str, tuple[Bars, PerpBundle]],
    holdout_start: date,
    holdout_end: date,
    in_sample_dir: Path,
    holdout_dir: Path,
    lock_path: Path,
    harden: bool = True,
) -> CarveSummary:
    """Split every instrument's four series at ``holdout_start`` and lock the holdout half.

    ``data`` maps a symbol to its trade bars and the bundle aligned with them. The alignment is
    checked before anything is written, because a mismatch discovered afterwards would leave a
    write-once lock over a holdout that cannot be trusted.
    """
    if lock_path.exists():
        raise HoldoutLockError(f"{lock_path} exists — the holdout is write-once")
    if not holdout_start < holdout_end:
        raise ValueError("holdout_start must be before holdout_end")
    for symbol, (trades, bundle) in data.items():
        bundle.aligned_with(trades)
        _ = symbol

    cut, stop = _day_start(holdout_start), _day_start(holdout_end)
    holdout_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    in_sample_rows: dict[str, int] = {}

    for symbol, (trades, bundle) in data.items():
        inside = int(np.count_nonzero(trades.ts < cut))
        held = np.flatnonzero((trades.ts >= cut) & (trades.ts < stop))
        if not inside or not len(held):
            raise CoverageError(
                f"{symbol}: the holdout window {holdout_start}/{holdout_end} leaves "
                f"{inside} bars in sample and {len(held)} held out — one of them is empty, which "
                "is a mistake in the window rather than a result"
            )
        first, last = int(held[0]), int(held[-1]) + 1

        name = file_name(symbol, trades.timeframe)
        write_bars(in_sample_dir / name, trades.slice(0, inside))
        write_bundle(in_sample_dir, bundle.slice(0, inside))
        in_sample_rows[symbol] = inside

        target = holdout_dir / name
        write_bars(target, trades.slice(first, last))
        files[name] = sha256_file(target)
        make_read_only(target, harden)
        for written in write_bundle(holdout_dir, bundle.slice(first, last)).values():
            path = holdout_dir / written
            files[written] = sha256_file(path)
            make_read_only(path, harden)

    manifest: dict[str, Any] = {
        "range": f"{holdout_start.isoformat()}/{holdout_end.isoformat()}",
        "created_at": datetime.now(UTC).isoformat(),
        "files": dict(sorted(files.items())),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    lock_path.write_text(text, encoding="utf-8", newline="\n")  # same bytes on every OS
    make_read_only(lock_path, harden)
    return CarveSummary(manifest["range"], in_sample_rows, len(files), sha256_file(lock_path))
