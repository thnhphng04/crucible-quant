"""AVAX long mean reversion at the lower Bollinger band (slow bands, fast RSI): grid search on CUDA (fp32).

Bollinger is the slow indicator (boll_window 48-672 bars = 12 hours to 7 days at 15min), RSI the fast one
(rsi_window 2-14). Long entry at the close of bar i, against the move:

    entry_mode 1 (leaving the band):  close[i-1] > lower[i] > close[i] and RSI[i] < rsi_threshold
    entry_mode 2 (returning inside):  close[i-1] < lower[i] < close[i] and RSI[i] < rsi_threshold

Risk = sl_multiplier x (upper[i] - lower[i]), stop = entry - risk; take profit = entry + tp_sl_ratio x risk, or
the middle band of the entry bar when tp_sl_ratio = -1; timeout max_holding_bars; one position at a time; fees
as the breakout scripts. Logic lives in reference/multi_position/multi_position_engine.py. Run from the
repository root:

    uv run python "reference/long_bollinger+RSI+fade/train_full_grid_gpu.py" --verify 8 \\
        --start 2024-07-01 --end 2026-01-14 --max-open 1
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "multi_position"))

import multi_position_engine as engine  # noqa: E402
from multi_position_engine import (  # noqa: E402,F401
    DEFAULT_SCORE, SCORE_NAMES, add_trend_columns, load_bars, mtm_equity, resolve_fee_bars, score_result,
    timeframe_nanos, trade_metrics,
)

SIDE = engine.SIDE_LONG
MAX_OPEN = 1  # one position at a time
DEFAULT_CSV = engine.REFERENCE / "data" / "avaxusdt_futures_5min.csv"
SEARCH_SPACE = {
    "boll_window": [48, 96, 192, 288, 480, 672],
    "boll_window_dev": [1.8, 2.0, 2.2, 2.5, 3.0],
    "rsi_window": [2, 3, 4, 5, 7, 9, 14],
    "rsi_threshold": [5, 10, 15, 20, 25, 30, 35],
    "sl_multiplier": [0.25, 0.375, 0.5, 0.75, 1.0],
    "tp_sl_ratio": [0.5, 0.75, 1.0, 1.5, 2.0, -1],
    "entry_mode": [1, 2],
}


def parameter_grid(limit=None):
    return engine.parameter_grid(SEARCH_SPACE, limit)


def apply_score_formula(result, formula):
    return score_result(result, formula)["score"]


def run_gpu_grid(bars, grid, timeframe, max_holding_bars=288, fee_bars=None, score_formula=DEFAULT_SCORE,
                 max_open=MAX_OPEN):
    return engine.run_gpu_grid(SIDE, bars, grid, timeframe, max_holding_bars, fee_bars, score_formula, max_open)


def cpu_trace(bars, params, timeframe, max_holding_bars=288, fee_bars=None, max_open=MAX_OPEN):
    return engine.cpu_trace(SIDE, bars, params, timeframe, max_holding_bars, fee_bars, max_open)


def verify_samples(bars, result, sample_count, max_holding_bars=288, fee_bars=None, timeframe="15min",
                   max_open=MAX_OPEN):
    return engine.verify_samples(SIDE, bars, result, sample_count, max_holding_bars, fee_bars, timeframe,
                                 max_open)


if __name__ == "__main__":
    engine.main(__doc__, SIDE, SEARCH_SPACE, Path(__file__))
