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
from quantcrucible.ledger.records import TrialStats
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


@pytest.mark.parametrize(("tag", "scope"), [("campaign_v1", "c1"), (None, None)])
def test_the_gp_ranking_reads_the_locked_trial_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str | None, scope: str | None
) -> None:
    """ADR-0050: the ranking's ``N_eff`` and ``V[SR]`` are this campaign's under the tag."""
    derived: dict[str, Any] = {"feature_map": FEATURE_MAP_V2}
    if tag is not None:
        derived["trial_scope"] = tag
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    session = ResearchSession(
        ledger, {"research": {}, "derived": derived}, "c1",
        {"BTC/USDT": make_bars(100, symbol="BTC/USDT")},
        sandbox=None, results_dir=tmp_path,  # type: ignore[arg-type]
    )  # fmt: skip
    engine = _engine(session, Key("BTC/USDT", "long", "gp", 0), "run")
    assert isinstance(engine, GpSearch) and engine.stats_scope == scope
    asked: list[str | None] = []
    real = ledger.trial_stats

    def spy(campaign_id: str | None = None) -> TrialStats:
        asked.append(campaign_id)
        return real(campaign_id)

    monkeypatch.setattr(ledger, "trial_stats", spy)
    engine.next()
    assert asked == [scope]
