"""RSI momentum warm-up: v1 went long with a NaN stop when RSI(n < 14) was ready before ATR(14)
(three calibration attempts on the first real-data run, 2026-09-22). v2 trades only on a finite,
positive ATR and stop; v1 stays unchanged — its trials and archived source are history."""

from __future__ import annotations

from importlib.resources import files

import numpy as np
import pytest

from quantcrucible.core.strategy.base import Bars, FeatureView, Signal, step
from quantcrucible.core.strategy.registry import ind
from quantcrucible.core.strategy.template import load_strategy_class

ZOO = files("quantcrucible.core.zoo")
V1 = ZOO.joinpath("rsi_momentum.py").read_text("utf-8")
V2 = ZOO.joinpath("rsi_momentum_v2.py").read_text("utf-8")
PARAMS = {"n": 10, "level": 55, "k_atr": 2.5}  # n < 14: RSI ready before ATR


def rising(n: int) -> Bars:
    """A steady climb: RSI far above any level as soon as it exists."""
    close = 100.0 * np.cumprod(np.full(n, 1.01))
    close[::3] *= 0.995  # some down days so RSI is defined (not 0/0)
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "D")
    return Bars("TEST/USDT", "1d", ts, close, close * 1.01, close * 0.99, close, np.ones(n))


def signals(source: str, bars: Bars) -> list[Signal]:
    strategy = load_strategy_class(source)(PARAMS)
    return [step(strategy, bars.slice(0, i + 1)) for i in range(len(bars))]


def test_v1_goes_long_with_a_nan_stop_during_warm_up() -> None:
    with pytest.raises(ValueError, match="stop_distance must be finite"):
        signals(V1, rising(40))


def test_v2_stays_flat_until_atr_is_ready() -> None:
    bars = rising(40)
    out = signals(V2, bars)
    atr_ready = int(np.flatnonzero(np.isfinite(ind.atr(bars, 14)))[0])  # first bar with an ATR
    rsi_ready = int(np.flatnonzero(np.isfinite(ind.rsi(bars.close, int(PARAMS["n"]))))[0])
    assert rsi_ready < atr_ready  # the window in which v1 went long with a NaN stop
    assert all(s.direction == "flat" for s in out[:atr_ready])
    assert any(s.direction == "long" for s in out[atr_ready:])
    assert all(np.isfinite(s.stop_distance) and s.stop_distance > 0
               for s in out if s.direction == "long")  # fmt: skip


def test_after_warm_up_v2_trades_exactly_like_v1() -> None:
    bars = rising(60)
    v1 = load_strategy_class(V1)(PARAMS)
    v2 = load_strategy_class(V2)(PARAMS)
    for i in range(20, len(bars)):
        window = bars.slice(0, i + 1)
        assert step(v2, window) == step(v1, window)


@pytest.mark.parametrize("atr", [float("nan"), float("inf"), -float("inf"), 0.0, -1.0])
def test_v2_never_trades_on_a_bad_atr(atr: float) -> None:
    strategy = load_strategy_class(V2)(PARAMS)
    view = FeatureView({"r": np.array([90.0]), "atr": np.array([atr])}, 0)
    assert strategy.signal(view) == Signal("flat", 0.0, 0.0)
