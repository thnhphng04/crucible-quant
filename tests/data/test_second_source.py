"""Second-source in-sample download for gate ⑥′ (ADR-0015): never stores holdout-period bars."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.data.ccxt_source import CcxtSource
from quantcrucible.data.second_source import download_in_sample
from quantcrucible.data.store import HoldoutAccessError, ResearchStore, read_bars
from tests.data.conftest import FakeExchange

HOLDOUT = (date(2024, 1, 1), date(2025, 1, 1))


def test_only_the_research_period_is_written(tmp_path: Path) -> None:
    source = CcxtSource("gate", FakeExchange(days=2000))
    written = download_in_sample(
        source, ["BTC/USDT", "ETH/USDT"], "1d", date(2020, 1, 1), [HOLDOUT], tmp_path
    )
    assert set(written) == {"BTC/USDT", "ETH/USDT"}
    bars = read_bars(tmp_path / "BTC-USDT_1d.parquet", "BTC/USDT", "1d")
    assert len(bars) == written["BTC/USDT"] > 1000
    assert bars.ts[-1] < np.datetime64("2024-01-01")  # nothing from the holdout on disk
    store = ResearchStore(tmp_path, [HOLDOUT])
    assert len(store.bars("ETH/USDT", "1d")) == written["ETH/USDT"]
    with pytest.raises(HoldoutAccessError):
        store.bars("ETH/USDT", "1d", start=date(2023, 6, 1), end=date(2024, 6, 1))


def test_a_holdout_range_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="holdout range"):
        download_in_sample(CcxtSource("gate", FakeExchange()), ["X/USDT"], "1d",
                           date(2020, 1, 1), [], tmp_path)  # fmt: skip
