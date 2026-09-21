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
