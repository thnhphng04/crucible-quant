"""AVAX short trend following on slow Bollinger bands with a fast RSI, every signal its own trade: CUDA grid (fp32).

Short entry at the close of bar i:

    close[i-1] > lower[i] > close[i] and RSI[i] < rsi_threshold

Risk = sl_multiplier x (upper[i] - entry), stop = entry + risk; take profit = entry - tp_sl_ratio x risk, none
when tp_sl_ratio = 0; timeout max_holding_bars; fees as the breakout scripts. Same as
reference/short_bollinger+RSI+trend except that trades overlap: a signal opens a new trade while earlier ones
are still open (up to max_open; max_open = 1 reproduces the one-position script). Logic lives in
reference/multi_position/multi_position_engine.py. Run from the repository root:

    uv run python "reference/multi_position/short_bollinger+RSI+trend/train_full_grid_gpu.py" --verify 8 \\
        --csv reference/data_5m/avaxusdt_futures_5min.csv --timeframe 5min --max-holding-bars 100 \\
        --start 2024-07-01 --end 2026-01-01
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import multi_position_engine as engine  # noqa: E402
from multi_position_engine import (  # noqa: E402,F401
    DEFAULT_SCORE, MAX_SLOTS, SCORE_NAMES, add_trend_columns, load_bars, mtm_equity, resolve_fee_bars,
    score_result, timeframe_nanos, trade_metrics,
)

SIDE = engine.SIDE_SHORT
DEFAULT_CSV = engine.REFERENCE / "data_5m" / "avaxusdt_futures_5min.csv"
SEARCH_SPACE = {
    "boll_window": [40, 45, 50, 60, 80, 100, 150, 200, 400],
    "boll_window_dev": [1.8, 2.0, 2.2, 2.5],
    "rsi_window": [5, 7, 9, 14, 20, 30],
    "rsi_threshold": [5, 10, 15, 20, 25, 30, 35, 40],
    "sl_multiplier": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
    "tp_sl_ratio": [1.1],
}


def parameter_grid(limit=None):
    return engine.parameter_grid(SEARCH_SPACE, limit)


def apply_score_formula(result, formula):
    return score_result(result, formula)["score"]


def run_gpu_grid(bars, grid, timeframe, max_holding_bars=288, fee_bars=None, score_formula=DEFAULT_SCORE,
                 max_open=MAX_SLOTS):
    return engine.run_gpu_grid(SIDE, bars, grid, timeframe, max_holding_bars, fee_bars, score_formula, max_open)


def cpu_trace(bars, params, timeframe, max_holding_bars=288, fee_bars=None, max_open=MAX_SLOTS):
    return engine.cpu_trace(SIDE, bars, params, timeframe, max_holding_bars, fee_bars, max_open)


def verify_samples(bars, result, sample_count, max_holding_bars=288, fee_bars=None, timeframe="15min",
                   max_open=MAX_SLOTS):
    return engine.verify_samples(SIDE, bars, result, sample_count, max_holding_bars, fee_bars, timeframe,
                                 max_open)


if __name__ == "__main__":
    engine.main(__doc__, SIDE, SEARCH_SPACE, Path(__file__))
