"""Holdout carve-out (Architecture §4.2 layer 2, P6) — INV-08 (data side)."""

import json
import os
import stat
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.data.ccxt_source import CcxtSource
from quantcrucible.data.holdout_split import (
    CarveSummary,
    HoldoutLockError,
    carve,
    make_read_only,
    remove_deny_acl,
    sha256_file,
    verify_holdout,
)
from quantcrucible.data.store import file_name, read_bars
from tests.data.conftest import FakeExchange, utc


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    return {
        "in_sample_dir": tmp_path / "data" / "is",
        "holdout_dir": tmp_path / "ho",
        "lock_path": tmp_path / "ho.lock",
    }


def _carve(layout: dict[str, Path], exchange: FakeExchange) -> CarveSummary:
    bars = CcxtSource(exchange=exchange).bars(
        "BTC/USDT", "1d", utc(2020, 1, 2), utc(2030, 1, 1), now=utc(2023, 1, 1)
    )
    return carve({"BTC/USDT": bars}, date(2022, 1, 1), date(2023, 1, 2), harden=False, **layout)


def test_split_at_holdout_start(layout: dict[str, Path], fake_exchange: FakeExchange) -> None:
    summary = _carve(layout, fake_exchange)
    name = file_name("BTC/USDT", "1d")
    is_bars = read_bars(layout["in_sample_dir"] / name, "BTC/USDT", "1d")
    ho_bars = read_bars(layout["holdout_dir"] / name, "BTC/USDT", "1d")
    assert is_bars.ts[-1] < np.datetime64("2022-01-01", "ns") <= ho_bars.ts[0]
    assert len(is_bars) + len(ho_bars) == 1096  # 2020-01-02 … 2023-01-01, nothing lost
    assert summary.in_sample_rows == {"BTC/USDT": len(is_bars)}
    assert summary.holdout_range == "2022-01-01/2023-01-02"


def test_lock_hash_matches(layout: dict[str, Path], fake_exchange: FakeExchange) -> None:
    summary = _carve(layout, fake_exchange)
    manifest = json.loads(layout["lock_path"].read_text(encoding="utf-8"))
    name = file_name("BTC/USDT", "1d")
    assert manifest["files"][name] == sha256_file(layout["holdout_dir"] / name)
    assert summary.lock_sha256 == sha256_file(layout["lock_path"])
    verify_holdout(layout["lock_path"], layout["holdout_dir"])


def test_holdout_files_read_only(layout: dict[str, Path], fake_exchange: FakeExchange) -> None:
    _carve(layout, fake_exchange)
    for path in [layout["lock_path"], *layout["holdout_dir"].iterdir()]:
        assert not os.stat(path).st_mode & stat.S_IWRITE


def test_second_carve_refused(layout: dict[str, Path], fake_exchange: FakeExchange) -> None:
    _carve(layout, fake_exchange)
    with pytest.raises(HoldoutLockError, match="write-once"):
        _carve(layout, fake_exchange)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL hardening")
def test_hardened_file_stays_readable(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"data")
    make_read_only(target, harden=True)
    try:
        assert target.read_bytes() == b"data"  # the deny entry must not block reading
        with pytest.raises(PermissionError):
            target.write_bytes(b"x")
    finally:
        remove_deny_acl(target)
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)


def test_tampering_detected(layout: dict[str, Path], fake_exchange: FakeExchange) -> None:
    _carve(layout, fake_exchange)
    target = layout["holdout_dir"] / file_name("BTC/USDT", "1d")
    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(HoldoutLockError, match="modified"):
        verify_holdout(layout["lock_path"], layout["holdout_dir"])
