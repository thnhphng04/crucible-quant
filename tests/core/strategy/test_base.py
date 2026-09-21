"""Strategy contract (Architecture §3.3) — INV-03."""

import math

import numpy as np
import pytest

from quantcrucible.core.strategy.base import (
    FLAT,
    Bars,
    Features,
    FeatureView,
    LookAheadError,
    Signal,
    Strategy,
    generate_signals,
    step,
)
from quantcrucible.core.strategy.registry import ind
from tests.factories import bars_from_close, make_bars


class EmaCrossover(Strategy):
    """The §3.3.1 example, written against the contract."""

    def indicators(self, bars: Bars) -> Features:
        return {
            "f": ind.ema(bars.close, int(self.p.fast)),
            "s": ind.ema(bars.close, int(self.p.slow)),
            "atr": ind.atr(bars, 14),
        }

    def signal(self, x: FeatureView) -> Signal:
        if ind.cross_up(x["f"], x["s"]):
            return Signal("long", 1.0, self.p.k_atr * x["atr"])
        return Signal("flat", 0.0, self.p.k_atr * x["atr"])


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(direction="up", strength=1.0, stop_distance=1.0),
        dict(direction="long", strength=1.5, stop_distance=1.0),
        dict(direction="long", strength=-0.1, stop_distance=1.0),
        dict(direction="long", strength=1.0, stop_distance=0.0),
        dict(direction="short", strength=0.5, stop_distance=math.nan),
        dict(direction="long", strength=0.5, stop_distance=1.0, take_profit=-1.0),
        dict(direction="flat", strength=0.5, stop_distance=1.0),
    ],
)
def test_signal_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Signal(**kwargs)  # type: ignore[arg-type]


def test_flat_signal_ignores_stop() -> None:
    assert Signal("flat", 0.0, math.nan).direction == "flat"
    assert FLAT.strength == 0.0


def test_bars_validation() -> None:
    bars = make_bars(5)
    with pytest.raises(ValueError, match="increasing"):
        Bars("X", "1d", bars.ts[::-1], bars.open, bars.high, bars.low, bars.close, bars.volume)
    with pytest.raises(ValueError, match="1-D"):
        Bars("X", "1d", bars.ts, bars.open[:3], bars.high, bars.low, bars.close, bars.volume)


def test_window_is_the_past_only() -> None:
    bars = make_bars(10)
    w = bars.window(5, 3)
    assert list(w.close) == list(bars.close[3:6])
    assert len(bars.window(1, 10)) == 2


def test_series_view_cannot_read_the_future() -> None:
    view = FeatureView({"a": np.arange(5.0)}, t=2)
    assert view["a"].now == 2.0
    assert view["a"].ago(2) == 0.0
    assert math.isnan(view["a"].ago(3))
    with pytest.raises(LookAheadError):
        view["a"].ago(-1)


def test_series_view_arithmetic_uses_current_value() -> None:
    view = FeatureView({"a": np.array([1.0, 4.0]), "b": np.array([9.0, 2.0])}, t=1)
    a, b = view["a"], view["b"]
    assert a + b == 6.0 and 2 * a == 8.0 and a / 2 == 2.0 and 10 - a == 6.0
    assert a > b and not a < b and abs(-a) == 4.0
    with pytest.raises(TypeError):
        bool(a)


def test_params_are_read_only() -> None:
    s = EmaCrossover({"fast": 3})
    assert s.p.fast == 3
    with pytest.raises(AttributeError):
        s.p.fast = 4
    with pytest.raises(AttributeError):
        _ = s.p.missing


def test_ema_crossover_emits_long_on_cross() -> None:
    close = [10.0] * 20 + [9.0] * 5 + [12.0, 13.0, 14.0]
    bars = bars_from_close(close)
    signals = generate_signals(EmaCrossover({"fast": 2, "slow": 5, "k_atr": 2.0}), bars, 50)
    longs = [i for i, s in enumerate(signals) if s.direction == "long"]
    assert longs == [25]
    assert signals[25].stop_distance > 0


def test_step_rejects_misshaped_features() -> None:
    class Bad(Strategy):
        def indicators(self, bars: Bars) -> Features:
            return {"x": np.zeros(len(bars) + 1)}

        def signal(self, x: FeatureView) -> Signal:
            return FLAT

    with pytest.raises(ValueError, match="one value per bar"):
        step(Bad(), make_bars(10))


def test_step_rejects_non_signal() -> None:
    class Bad(Strategy):
        def indicators(self, bars: Bars) -> Features:
            return {}

        def signal(self, x: FeatureView) -> Signal:
            return "long"  # type: ignore[return-value]

    with pytest.raises(TypeError):
        step(Bad(), make_bars(10))
