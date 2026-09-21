"""Indicator whitelist (Architecture §3.1.6) — INV-33: every indicator is causal."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantcrucible.core.strategy import registry
from quantcrucible.core.strategy.base import Bars, FeatureView
from quantcrucible.core.strategy.registry import INDICATORS, ind
from tests.factories import bars_from_close, make_bars

NAN = np.nan


def _compute(name: str, bars: Bars, n: int) -> np.ndarray:
    fn = getattr(registry, name)
    result: np.ndarray = fn(bars, n) if INDICATORS[name] == "bars" else fn(bars.close, n)
    return result


def _perturb_after(bars: Bars, t: int, seed: int) -> Bars:
    other = make_bars(len(bars), seed=seed + 1)
    cut = t + 1

    def mix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.concatenate((a[:cut], b[cut:]))

    return Bars(
        bars.symbol, bars.timeframe, bars.ts,
        mix(bars.open, other.open), mix(bars.high, other.high), mix(bars.low, other.low),
        mix(bars.close, other.close), mix(bars.volume, other.volume),
    )  # fmt: skip


SERIES = sorted(k for k, kind in INDICATORS.items() if kind != "predicate")


@pytest.mark.parametrize("name", SERIES)
@settings(max_examples=60, deadline=None)
@given(
    length=st.integers(5, 150),
    n=st.integers(1, 30),
    t_frac=st.floats(0, 1),
    seed=st.integers(0, 10_000),
)
def test_all_indicators_causal(name: str, length: int, n: int, t_frac: float, seed: int) -> None:
    bars = make_bars(length, seed=seed)
    t = min(int(t_frac * length), length - 1)
    full = _compute(name, bars, n)
    changed = _compute(name, _perturb_after(bars, t, seed), n)
    truncated = _compute(name, bars.slice(0, t + 1), n)
    assert full.shape == (length,)
    np.testing.assert_array_equal(full[: t + 1], changed[: t + 1])
    np.testing.assert_allclose(full[: t + 1], truncated, rtol=1e-12, equal_nan=True)


def test_every_whitelisted_name_exists_on_ind() -> None:
    for name in INDICATORS:
        assert callable(getattr(ind, name))


def test_sma_ema_reference_values() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    np.testing.assert_allclose(ind.sma(x, 3), [NAN, NAN, 2.0, 3.0, 4.0], equal_nan=True)
    # seed = SMA of the first 3 = 2; alpha = 0.5 → 3.0, 4.0
    np.testing.assert_allclose(ind.ema(x, 3), [NAN, NAN, 2.0, 3.0, 4.0], equal_nan=True)
    np.testing.assert_allclose(ind.ema(np.array([2.0, 4.0, 8.0]), 1), [2.0, 4.0, 8.0])


def test_atr_reference_value() -> None:
    ts = np.datetime64("2020-01-02", "ns") + np.arange(3) * np.timedelta64(1, "D")
    bars = Bars(
        "X", "1d", ts, np.array([1.5, 2.5, 3.0]), np.array([2.0, 3.0, 4.0]),
        np.array([1.0, 1.0, 2.0]), np.array([1.5, 2.5, 3.0]), np.ones(3),
    )  # fmt: skip
    # TR = [1, 2, 2]; seed = mean(1, 2) = 1.5; Wilder alpha 1/2 → 0.5*2 + 0.5*1.5 = 1.75
    np.testing.assert_allclose(ind.atr(bars, 2), [NAN, 1.5, 1.75], equal_nan=True)


def test_rsi_extremes() -> None:
    up = np.arange(1.0, 30.0)
    assert ind.rsi(up, 14)[-1] == 100.0
    down = up[::-1].copy()
    assert ind.rsi(down, 14)[-1] == 0.0
    assert np.isnan(ind.rsi(up, 14)[:14]).all()


def test_rolling_and_zscore() -> None:
    x = np.array([3.0, 1.0, 2.0, 5.0])
    np.testing.assert_allclose(ind.rolling_max(x, 2), [NAN, 3.0, 2.0, 5.0], equal_nan=True)
    np.testing.assert_allclose(ind.rolling_min(x, 2), [NAN, 1.0, 1.0, 2.0], equal_nan=True)
    z = ind.zscore(np.array([1.0, 2.0, 3.0]), 3)
    assert z[-1] == pytest.approx(1.0 / np.std([1.0, 2.0, 3.0]))
    assert np.isnan(ind.zscore(np.ones(5), 3)).all()


def test_cross_predicates() -> None:
    a = np.array([1.0, 3.0])
    b = np.array([2.0, 2.0])
    assert ind.cross_up(FeatureView({"a": a}, 1)["a"], FeatureView({"b": b}, 1)["b"])
    assert not ind.cross_down(FeatureView({"a": a}, 1)["a"], 2.0)
    assert ind.cross_down(FeatureView({"a": a[::-1].copy()}, 1)["a"], 2.0)
    assert not ind.cross_up(FeatureView({"a": np.array([NAN, 3.0])}, 1)["a"], 2.0)


@pytest.mark.parametrize("bad", [0, -3, 2.5, True])
def test_bad_period_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        ind.sma(np.ones(5), bad)  # type: ignore[arg-type]


def test_short_input_is_all_nan() -> None:
    bars = bars_from_close([1.0, 2.0])
    for name in SERIES:
        assert np.isnan(_compute(name, bars, 5)).all()
