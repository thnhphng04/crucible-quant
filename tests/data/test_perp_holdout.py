"""A second holdout, separate from the first (P3-10, §4.2, P6).

Perpetual research needs its own out-of-sample data, and it must not touch the spot holdout that
already exists: that one was written once, for a different market, and opening or overwriting it
would burn it (P6).

`carve` already takes its directory and lock path as arguments, so separation is a matter of
using different ones — and of proving that the existing lock is never read, never written and
never satisfied by the new carve. Every path here is under `tmp_path`; nothing in this file
touches the real holdout.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.data.holdout_split import HoldoutLockError, carve, read_holdout_lock
from tests.factories import make_bars

START = date(2021, 6, 1)
END = date(2021, 7, 1)


def bars() -> dict[str, object]:
    ts = np.arange("2021-01-01", 240, dtype="datetime64[D]").astype("datetime64[ns]")
    b = make_bars(240, seed=1, symbol="BTCUSDT")
    return {"BTCUSDT": type(b)(b.symbol, b.timeframe, ts, b.open, b.high, b.low, b.close, b.volume)}


def _carve(root: Path, name: str) -> Path:
    lock = root / f"{name}.lock"
    carve(
        bars(), START, END,  # type: ignore[arg-type]
        in_sample_dir=root / f"is-{name}",
        holdout_dir=root / name,
        lock_path=lock,
        harden=False,  # a test never hardens: the files must stay removable
    )  # fmt: skip
    return lock


def test_the_perpetual_lock_is_separate_from_the_spot_lock(tmp_path: Path) -> None:
    spot = _carve(tmp_path, "spot")
    before = spot.read_bytes()
    perp = _carve(tmp_path, "perp")
    assert spot != perp and spot.exists() and perp.exists()
    assert spot.read_bytes() == before  # carving the second left the first untouched
    assert set(read_holdout_lock(perp)["files"])  # and the second has a manifest of its own


def test_an_existing_lock_is_never_overwritten(tmp_path: Path) -> None:
    """Write-once is per lock path, which is what makes two holdouts safe rather than a hazard."""
    spot = _carve(tmp_path, "spot")
    before = spot.read_bytes()
    with pytest.raises(HoldoutLockError, match="write-once"):
        _carve(tmp_path, "spot")
    assert spot.read_bytes() == before


def test_the_second_carve_does_not_read_the_first(tmp_path: Path) -> None:
    """Carving the perpetual holdout must not depend on the spot one existing at all."""
    perp = _carve(tmp_path, "perp")
    assert perp.exists()
    assert not (tmp_path / "spot.lock").exists()


def test_each_holdout_keeps_its_own_directory(tmp_path: Path) -> None:
    _carve(tmp_path, "spot")
    _carve(tmp_path, "perp")
    spot_files = {p.name for p in (tmp_path / "spot").iterdir()}
    perp_files = {p.name for p in (tmp_path / "perp").iterdir()}
    assert spot_files and perp_files
    assert (tmp_path / "spot").resolve() != (tmp_path / "perp").resolve()


def test_the_lock_records_the_range_it_carved(tmp_path: Path) -> None:
    manifest = read_holdout_lock(_carve(tmp_path, "perp"))
    assert manifest["range"] == f"{START.isoformat()}/{END.isoformat()}"
