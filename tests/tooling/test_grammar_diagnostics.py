"""scripts/grammar_diagnostics.py (P3-54): hand-computed values on tiny series."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from quantcrucible.agent.grammar import Close, Distance, Indicator, Param
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.store import write_bars

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "grammar_diagnostics", ROOT / "scripts" / "grammar_diagnostics.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


D = _load()


def bars_of(close: list[float] | np.ndarray, spread: float = 0.5, symbol: str = "X/Y") -> Bars:
    c = np.asarray(close, dtype=np.float64)
    ts = np.datetime64("2020-01-01", "ns") + np.arange(len(c)) * np.timedelta64(1, "D")
    return Bars(symbol, "1d", ts, c, c + spread, c - spread, c, np.full(len(c), 10.0))


def nan_eq(a: np.ndarray, b: list[float]) -> bool:
    return bool(np.allclose(a, np.asarray(b), equal_nan=True))


def test_candidate_indicators_match_hand_values() -> None:
    assert nan_eq(D.stdev(np.array([1.0, 2.0, 3.0, 5.0]), 2), [math.nan, 0.5, 0.5, 1.0])
    assert nan_eq(D.stdev(np.array([4.0, 4.0, 4.0]), 2), [math.nan, math.nan, math.nan])
    # sma 2, population std 1 ⇒ 4·1/2
    assert nan_eq(D.bandwidth(np.array([1.0, 3.0]), 2), [math.nan, 2.0])
    # |3 − 1| / (1 + 1) = 1, then |2 − 2| / (1 + 1) = 0
    assert nan_eq(D.efficiency_ratio(np.array([1.0, 2.0, 3.0, 2.0]), 2), [math.nan] * 2 + [1, 0])
    # log returns 1 and 2: sum 3, population std 0.5 ⇒ 3 / (0.5·√2)
    x = np.exp(np.array([0.0, 1.0, 3.0]))
    assert nan_eq(D.tsmom(x, 2), [math.nan, math.nan, 3 / (0.5 * math.sqrt(2))])
    assert nan_eq(D.relative_volume(np.array([1.0, 3.0]), 2), [math.nan, 1.5])
    b = Bars(
        "X/Y", "1d", np.array(["2020-01-01", "2020-01-02"], dtype="datetime64[ns]"),
        np.array([1.0, 1.0]), np.array([2.0, 1.0]), np.array([0.0, 1.0]),
        np.array([1.5, 1.0]), np.array([1.0, 1.0]),
    )  # fmt: skip
    assert nan_eq(D.close_location(b), [0.75, math.nan])


def test_cross_follows_the_registry_predicates() -> None:
    a, b = np.array([0.0, 2.0, 0.5]), np.ones(3)
    assert D._cross(a, b, up=True).tolist() == [False, True, False]
    assert D._cross(a, b, up=False).tolist() == [False, False, True]
    assert D._cross(np.array([math.nan, 2.0]), np.ones(2), up=True).tolist() == [False, False]


def test_breakout_compares_with_the_previous_bar_extreme() -> None:
    from quantcrucible.agent.grammar import Breakout

    ev = D.Evaluator(bars_of([1.0, 2.0, 3.0, 1.0, 4.0]))
    up = Breakout(True, Param("period", 2, 300, 2))
    # previous 2-bar max: -, -, 2, 3, 3 ⇒ close above it on bars 2 and 4
    assert ev.mask(up).tolist() == [False, False, True, False, True]


def _distance(n: int) -> Distance:
    return Distance(
        Close(), Indicator("sma", Param("period", 2, 300, n)), ">", Param("level", -3, 3, 0)
    )


def test_distance_saturates_over_atr_but_not_over_stdev_on_a_trend() -> None:
    # close = t, high/low ± 0.5 ⇒ true range 1.5; close − sma(101) = 50 ⇒ 33 ATRs,
    # while the population std of 101 consecutive integers is ≈ 29.2 ⇒ ≈ 1.7
    ev = D.Evaluator(bars_of(np.arange(1.0, 401.0)))
    df = D.distance_saturation(ev, [_distance(101)])
    by_scale = df.set_index("scale")["outside_k"]
    assert by_scale["atr"] == 1.0
    assert by_scale["stdev"] == 0.0
    assert df.set_index("scale").loc["stdev", "abs_median"] == pytest.approx(
        50 / math.sqrt((101**2 - 1) / 12)
    )


def test_bollinger_stop_is_non_positive_below_the_band() -> None:
    close = np.r_[np.full(30, 100.0) + np.tile([0.5, -0.5], 15), 80.0]  # a crash on the last bar
    df = D.bollinger_stop(D.Evaluator(bars_of(close)))
    long8 = df[(df.side == "long") & (df.n == 8) & (df.trigger == "any")].iloc[0]
    assert long8["bars"] == 24  # 31 bars, the first 7 warming up
    assert long8["stop_le_0"] == pytest.approx(1 / 24)
    assert df["stop_le_0"].dropna().between(0, 1).all()


def test_refuses_a_holdout_folder(tmp_path: Path) -> None:
    with pytest.raises(D.DiagnosticsError):
        D.check_in_sample(tmp_path / "holdout" / "is")
    with pytest.raises(D.DiagnosticsError):
        D.load_folder(tmp_path, None)  # empty


def test_end_to_end_on_a_synthetic_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rng = np.random.default_rng(1)
    for sym in ("AAA/USDT", "BBB/USDT"):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 600)))
        write_bars(
            tmp_path / "is" / f"{sym.replace('/', '-')}_1d.parquet", bars_of(close, 1.0, sym)
        )
    out = tmp_path / "out"
    assert D.main(["--data-dir", str(tmp_path / "is"), "--samples", "5", "--out", str(out)]) == 0
    assert {p.stem for p in out.glob("*.csv")} == {
        "fire",
        "grid",
        "scenarios",
        "distance",
        "bollinger",
        "volatility",
        "corr",
    }
    tables = D.run(D.load_folder(tmp_path / "is", "1d"), D.sample_clauses(5, 0))
    fire = tables["fire"]
    assert set(fire["symbol"]) == {"AAA/USDT", "BBB/USDT"}
    assert len(fire) == 2 * 7 * 5
    assert fire["rate"].between(0, 1).all()
    assert len(tables["scenarios"]) == 2 * len(D.SCENARIOS) * 7 * 5
    text = capsys.readouterr().out
    assert "== fire ==" in text and "== corr ==" in text and "== grid Cross" in text


def test_resampling_draws_periods_within_the_caps_and_keeps_other_numbers() -> None:
    from quantcrucible.agent.grammar import Threshold

    rsi = Threshold(Indicator("rsi", Param("period", 2, 100, 80)), ">", Param("level", 50, 90, 70))
    rng = np.random.default_rng(0)
    drawn = [D.resample_periods(rsi, rng, {"rsi": (2, 30)}) for _ in range(200)]
    assert all(isinstance(t, Threshold) for t in drawn)
    assert {t.osc.period.low for t in drawn} == {2} and {t.osc.period.high for t in drawn} == {30}
    assert all(2 <= t.osc.period.value <= 30 for t in drawn)
    assert {t.level.value for t in drawn} == {70}
    # log-uniform on [2, 300]: P(n > 100) = ln 3 / ln 150 ≈ 0.22, against 0.67 when uniform
    long = [D.resample_periods(_distance(50), rng, {}) for _ in range(4000)]
    share = np.mean([d.right.period.value > 100 for d in long])
    assert share == pytest.approx(math.log(3) / math.log(150), abs=0.03)


def test_grid_covers_each_range_and_describes_its_points() -> None:
    grid = D.grid_clauses()
    rows = [D.describe(c) for c in grid]
    rsi_up = {r["value"] for r in rows if r["clause"] == "CrossLevel" and r["op"] == "up"}
    assert rsi_up >= {50.0, 60.0, 70.0, 80.0, 90.0}
    assert max(r["n1"] for r in rows if r["osc"] == "rsi") == 100  # rsi's own period bound
    assert min(r["n1"] for r in rows if r["osc"] == "zscore") == 5
    pairs = {(r["n1"], r["n2"]) for r in rows if r["clause"] == "Cross"}
    assert (0, 2) in pairs and (200, 300) in pairs and (300, 300) not in pairs
