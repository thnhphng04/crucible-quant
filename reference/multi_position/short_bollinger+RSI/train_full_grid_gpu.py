"""AVAX short Bollinger/RSI breakout, every signal its own trade: grid search on CUDA (fp32).

Entry at bar i: close[i-1] > lower[i] > close[i] and RSI(rsi_window)[i] < rsi_threshold; stop
distance = sl_multiplier x (upper[i] - entry); TP = tp_sl_ratio x risk; timeout max_holding_bars.
Same as reference/short_bollinger+RSI except that trades overlap: a signal opens a new trade while
earlier ones are still open (up to max_open). Logic, fees and metrics live in
reference/multi_position/multi_position_engine.py. Run from the repository root:

    uv run python "reference/multi_position/short_bollinger+RSI/train_full_grid_gpu.py" --verify 8 \\
        --start 2023-07-01 --end 2025-07-01
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import multi_position_engine as engine  # noqa: E402
from multi_position_engine import (  # noqa: E402,F401
    DEFAULT_SCORE, MAX_SLOTS, SCORE_NAMES, load_bars, mtm_equity, resolve_fee_bars, score_result,
    timeframe_nanos, trade_metrics,
)

SIDE = engine.SIDE_SHORT
DEFAULT_CSV = engine.DEFAULT_CSV[SIDE]
SEARCH_SPACE = {
    "boll_window": [6, 8, 10, 12, 14, 16, 18, 20, 25, 30],
    "boll_window_dev": [1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3],
    "rsi_window": [30, 35, 40, 45, 50, 60, 80],
    "rsi_threshold": list(range(35, 50)),
    "sl_multiplier": [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0],
    "tp_sl_ratio": [1.1],
}


def parameter_grid(limit=None):
    return engine.parameter_grid(SEARCH_SPACE, limit)


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
