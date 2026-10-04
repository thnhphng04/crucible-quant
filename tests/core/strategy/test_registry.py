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


def test_bollinger_bands_use_eight_closes_and_population_deviation() -> None:
    closes = np.arange(1.0, 9.0)
    middle, deviation = np.mean(closes), np.std(closes, ddof=0)
    upper = registry.boll_upper(closes, 8)
    lower = registry.boll_lower(closes, 8)
    assert np.all(np.isnan(upper[:7])) and np.all(np.isnan(lower[:7]))
    assert upper[7] == pytest.approx(middle + 2 * deviation)
    assert lower[7] == pytest.approx(middle - 2 * deviation)


def _compute(name: str, bars: Bars, n: int) -> np.ndarray:
    fn = getattr(registry, name)
    if INDICATORS[name] == "pair":  # two series, e.g. the spread close − open
        result: np.ndarray = fn(bars.close, bars.open, n)
    else:
        result = fn(bars, n) if INDICATORS[name] == "bars" else fn(bars.close, n)
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


def test_spread_stdev_reference_values() -> None:
    """P3-58: the population standard deviation of x − y over n bars; NaN where it is 0, and
    NaN while a window holds a NaN (an operand still warming up)."""
    x = np.array([5.0, 7.0, 6.0, 10.0, 10.0])
    y = np.array([4.0, 4.0, 4.0, 6.0, 6.0])  # x − y = 1, 3, 2, 4, 4
    out = registry.spread_stdev(x, y, 2)
    np.testing.assert_allclose(out, [NAN, 1.0, 0.5, 1.0, NAN], equal_nan=True)
    out3 = registry.spread_stdev(x, y, 3)  # windows (1, 3, 2), (3, 2, 4), (2, 4, 4)
    assert np.isnan(out3[:2]).all()
    assert out3[2] == pytest.approx(np.sqrt(2 / 3))
    assert out3[3] == pytest.approx(np.sqrt(2 / 3))
    assert out3[4] == pytest.approx(np.sqrt(8 / 9))
    warming = registry.spread_stdev(x, np.array([NAN, 4.0, 4.0, 6.0, 6.0]), 2)
    assert np.isnan(warming[:2]).all() and warming[2] == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [0, -3, 2.5, True])
def test_bad_period_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        ind.sma(np.ones(5), bad)  # type: ignore[arg-type]


def test_short_input_is_all_nan() -> None:
    bars = bars_from_close([1.0, 2.0])
    for name in SERIES:
        assert np.isnan(_compute(name, bars, 5)).all()


# ── OpSpec metadata (typed DSL, arch §3.1.11) ─────────────────────────────────────────────────
@pytest.mark.parametrize("name", SERIES)
def test_warmup_matches_first_valid_value(name: str) -> None:
    """The declared warm-up is exactly where the indicator becomes finite on clean data —
    the generator relies on it to emit its readiness guard."""
    spec = registry.OPS[name]
    bars = make_bars(300, seed=3)
    for n in (2, 5, 14, 30):
        values = _compute(name, bars, n)
        first = int(np.argmax(np.isfinite(values)))
        assert first == spec.warmup(n) - 1, (name, n, first)


def test_every_indicator_has_a_spec() -> None:
    assert set(registry.OPS) == set(INDICATORS)
    for name, spec in registry.OPS.items():
        assert spec.kind == INDICATORS[name]
        if spec.kind != "predicate":
            assert spec.period is not None and 1 < spec.period[0] <= spec.period[1]
        if spec.output == "dimensionless":
            assert spec.value_range is not None
