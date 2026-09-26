"""Research store (Architecture §4.2, P6) — INV-10: research never reads holdout-range bars."""

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.cli import add_months
from quantcrucible.data.store import (
    HoldoutAccessError,
    ResearchStore,
    file_name,
    parse_range,
    read_bars,
    write_bars,
)
from tests.factories import make_bars

HOLDOUT = parse_range("2021-01-01/2022-01-01")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    bars = make_bars(300, symbol="BTC/USDT", start="2020-01-01")  # closes 2020-01-02 … 2020-10-27
    write_bars(tmp_path / file_name("BTC/USDT", "1d"), bars)
    return tmp_path


def test_reads_in_sample_range(root: Path) -> None:
    store = ResearchStore(root, [HOLDOUT])
    bars = store.bars("BTC/USDT", "1d", date(2020, 3, 1), date(2020, 4, 1))
    assert len(bars) == 31
    assert bars.ts[0] == np.datetime64("2020-03-01", "ns")
    assert len(store.bars("BTC/USDT", "1d")) == 300


def test_holdout_range_refused(root: Path) -> None:
    store = ResearchStore(root, [HOLDOUT])
    with pytest.raises(HoldoutAccessError):
        store.bars("BTC/USDT", "1d", date(2020, 12, 1), date(2021, 2, 1))
    with pytest.raises(HoldoutAccessError):
        store.bars("BTC/USDT", "1d", date(2021, 6, 1), date(2021, 7, 1))  # even with no bars there
    assert len(store.bars("BTC/USDT", "1d", date(2020, 6, 1))) > 0  # open end = the clean file


def test_contaminated_file_refused(root: Path) -> None:
    store = ResearchStore(root, [parse_range("2020-06-01/2020-07-01")])
    with pytest.raises(HoldoutAccessError):
        store.bars("BTC/USDT", "1d", date(2020, 1, 1), date(2020, 2, 1))


@pytest.mark.parametrize(
    ("d", "months", "expected"),
    [
        (date(2026, 9, 21), -12, date(2025, 9, 21)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 1, 15), -1, date(2025, 12, 15)),
    ],
)
def test_add_months(d: date, months: int, expected: date) -> None:
    assert add_months(d, months) == expected


def test_a_perpetual_symbol_becomes_a_legal_filename() -> None:
    """ccxt spells a linear perpetual `BASE/QUOTE:SETTLE`. Replacing only the slash leaves the
    colon, and a colon does not fail on NTFS — it opens an **alternate data stream**. So
    `BTC/USDT:USDT` wrote its bars into a stream hanging off a file called `BTC-USDT`, which is
    the spot pair's own filename. Nothing raised, `exists()` was true, and the bytes were
    invisible to a directory listing, absent from any copy to a non-NTFS filesystem, and unseen
    by the container's bind mount.
    """
    name = file_name("BTC/USDT:USDT", "1d")
    assert not (set(name) & set(':*?"<>|')), name
    assert name.endswith("_1d.parquet")
    assert file_name("BTC/USDT", "1d") != name  # and it must not collide with the spot pair


def test_a_perpetual_file_is_a_real_file_a_listing_can_see(tmp_path: Path) -> None:
    """`exists()` is not enough to catch the stream: it returns true for one. The listing is."""
    bars = make_bars(10, symbol="BTC/USDT:USDT")
    name = file_name("BTC/USDT:USDT", "1d")
    write_bars(tmp_path / name, bars)
    assert [p.name for p in tmp_path.iterdir()] == [name]
    assert len(read_bars(tmp_path / name, "BTC/USDT:USDT", "1d")) == 10
