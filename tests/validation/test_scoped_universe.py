"""A candidate sees one instrument (P3-13, ADR-0033).

`make_candidate` set `universe=tuple(is_data)` — one line that made every candidate a
whole-basket strategy, because the universe was a campaign constant rather than a property of
the search. Once each `(instrument, direction)` is searched independently that is wrong twice
over: a BTC-long candidate would be backtested on five contracts, and gate ④'s PBO grid would
mix candidates that never competed for the same slot.

The legacy behaviour is kept for a candidate with no scope, because that is exactly what the
pre-P3 campaigns did and their trials must keep meaning what they meant.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
from types import SimpleNamespace
from typing import Any

import pytest

from quantcrucible.core.strategy.base import Bars
from quantcrucible.validation.is_gates import universe_bars
from quantcrucible.validation.run import Provenance, make_candidate
from tests.factories import make_bars

IS_DATA = {
    "BTCUSDT": make_bars(60, seed=1, symbol="BTCUSDT"),
    "ETHUSDT": make_bars(60, seed=2, symbol="ETHUSDT"),
    "SOLUSDT": make_bars(60, seed=3, symbol="SOLUSDT"),
}
SOURCE = files("quantcrucible.core.zoo").joinpath("ema_crossover.py").read_text("utf-8")


def _provenance(instrument: str | None, direction: str = "long") -> Provenance:
    return Provenance(engine="gp", seed=0, run_id="r", instrument=instrument, direction=direction)


def test_a_scoped_candidate_universe_is_its_own_instrument() -> None:
    c = make_candidate(SOURCE, "c1", IS_DATA, provenance=_provenance("ETHUSDT"))
    assert c.universe == ("ETHUSDT",)
    assert c.instrument == "ETHUSDT"


def test_the_scope_reaches_the_bars_the_backtest_sees() -> None:
    """The point of the whole change: gate ③ measures one contract, not the basket."""
    c = make_candidate(SOURCE, "c1", IS_DATA, provenance=_provenance("BTCUSDT"))
    assert set(universe_bars(c, _ctx(IS_DATA))) == {"BTCUSDT"}


def test_an_unscoped_candidate_keeps_the_whole_basket() -> None:
    """Pre-P3 behaviour, unchanged: those campaigns searched the basket and their trials must
    keep meaning that."""
    c = make_candidate(SOURCE, "c1", IS_DATA)
    assert set(c.universe) == set(IS_DATA)
    assert c.instrument is None


def test_an_instrument_absent_from_the_session_is_refused() -> None:
    """Silently falling back to the basket would produce a trial labelled BTCUSDT that was
    measured on five contracts."""
    with pytest.raises(ValueError, match="not in this session"):
        make_candidate(SOURCE, "c1", IS_DATA, provenance=_provenance("XRPUSDT"))


def test_the_timerange_comes_from_the_scoped_instrument_only() -> None:
    short = dict(IS_DATA)
    short["ETHUSDT"] = make_bars(20, seed=9, symbol="ETHUSDT")
    scoped = make_candidate(SOURCE, "c1", short, provenance=_provenance("ETHUSDT"))
    basket = make_candidate(SOURCE, "c1", short)
    assert scoped.timerange != basket.timerange


def test_the_direction_reaches_the_candidate() -> None:
    c = make_candidate(SOURCE, "c1", IS_DATA, provenance=_provenance("BTCUSDT", "short"))
    assert c.direction == "short"


def _ctx(is_data: Mapping[str, Bars]) -> Any:
    """The slice of GateContext `universe_bars` reads."""

    ctx = SimpleNamespace(services={"is_data": is_data})
    return ctx
