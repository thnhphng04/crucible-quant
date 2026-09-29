"""The gate-③/④ report is built by one set of pure functions, shared by the sandbox and the
kernel engine (P3-34, ADR-0038), so the two paths cannot drift in what they report.

The golden digests were taken from the sandbox job functions before the builder was extracted:
the refactor must not change a byte. ``indicator_corr`` goes through ``np.corrcoef`` (BLAS), so
it is checked on its own rather than hashed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pytest

from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import (
    Combine,
    Cross,
    Genome,
    Indicator,
    Param,
    Slope,
    render_genome,
)
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.validation import sandbox_runner
from quantcrucible.validation.sandbox import SandboxResult

BACKTEST_GOLDEN = "efb2b8db444afbb7ef1bd159bc64f1f203161ef4ebfe0f8403adeffe6c3c2994"
GRID_GOLDEN = "98b606bfff9c77f4c1cbd78f71e912d42269496aba4108d5235868430ccf598d"


def _period(v: int) -> Param:
    return Param("period", 2, 300, v)


def _setup() -> tuple[Any, dict[str, Bars], dict[str, Any]]:
    g = Genome(
        Combine(
            "and",
            (
                Cross(Indicator("ema", _period(10)), True, Indicator("ema", _period(30))),
                Slope(Indicator("ema", _period(50)), True),
            ),
        ),
        Param("mult", 0.5, 5.0, 2.0),
    )
    src, params = render_genome(g, "long", 1.1)
    cls = load_strategy_class(src, "report_golden")  # trusted: a hand-built genome
    rng = np.random.default_rng(4)
    n = 1500
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[100.0, close[:-1]]
    high, low = np.maximum(open_, close) * 1.004, np.minimum(open_, close) * 0.996
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    bars = {"BTC/USDT": Bars("BTC/USDT", "1h", ts, open_, high, low, close, np.full(n, 10.0))}
    job = {
        "params": params,
        "lookback": 200,
        "risk": {"max_risk_pct": 0.01},
        "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
        "exit_policy": {"mode": "bracket_timeout_v1", "tp_sl_ratio": 1.1, "max_holding_bars": 100},
        "seed": 0,
    }
    return cls, bars, job


def _digest(report: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()


def test_the_backtest_report_is_unchanged_by_the_extraction() -> None:
    cls, bars, job = _setup()
    report = sandbox_runner._backtest(cls, bars, job)
    corr = report.pop("indicator_corr")
    assert report["public"]["n_trades"] > 10
    assert 0.0 < corr <= 1.0
    assert _digest(report) == BACKTEST_GOLDEN


def test_the_grid_report_is_unchanged_by_the_extraction() -> None:
    cls, bars, job = _setup()
    params = job["params"]
    grid = [params, {**params, "n1": 12}, {**params, "k_stop": 2.5}]
    report = sandbox_runner._grid_backtest(cls, bars, {**job, "grid": grid})
    assert len(report["returns"]) == 3
    assert _digest(report) == GRID_GOLDEN


def test_a_sandbox_result_names_its_engine() -> None:
    res = SandboxResult(True, {"ok": True}, "", "", 0, False, None, 0.1)
    assert res.engine == "sandbox"
    assert SandboxResult(True, None, "", "", 0, False, None, 0.1, engine="cuda").engine == "cuda"


@pytest.mark.parametrize("stream", [{}, {"A/USDT": []}])
def test_an_empty_signal_stream_is_reported_as_is(stream: dict[str, list[Any]]) -> None:
    from quantcrucible.validation.backtest_report import signals_stream

    assert signals_stream({k: [] for k in stream}) == stream
