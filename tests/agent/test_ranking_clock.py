"""The GP ranking reads gate ③'s moments, so it annualises on the campaign's clock (ADR-0049)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.run import _engine
from quantcrucible.agent.scheduler import Key
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.run import FEATURE_MAP_V2
from tests.factories import make_bars


@pytest.mark.parametrize(("clock", "ppy"), [("daily_v1", 365.0), (None, 8760.0)])
def test_an_hourly_gp_search_ranks_on_the_locked_clock(
    tmp_path: Path, clock: str | None, ppy: float
) -> None:
    derived: dict[str, Any] = {"feature_map": FEATURE_MAP_V2}
    if clock is not None:
        derived["evaluation_clock"] = clock
    daily = make_bars(100, symbol="BTC/USDT")
    hourly = dataclasses.replace(
        daily, timeframe="1h", ts=daily.ts[0] + np.arange(100) * np.timedelta64(1, "h")
    )
    ledger = Ledger.open(tmp_path / "ledger.db")
    session = ResearchSession(
        ledger, {"research": {}, "derived": derived}, "c1", {"BTC/USDT": hourly},
        sandbox=None, results_dir=tmp_path,  # type: ignore[arg-type]
    )  # fmt: skip
    engine = _engine(session, Key("BTC/USDT", "long", "gp", 0), "run")
    assert isinstance(engine, GpSearch) and engine.ppy == ppy
