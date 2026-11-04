"""AVAX long Bollinger/RSI breakout gated by a slow uptrend: grid search on CUDA (fp32).

Entry at bar i: the long_bollinger+RSI breakout (close[i-1] < upper[i] < close[i] and
RSI(rsi_window)[i] > rsi_threshold) while close[i] > SMA(close, trend_window)[i]; trend_window = 0
switches the regime filter off (baseline: the original breakout + RSI at fee_bars 32). Stop distance
= sl_multiplier x (entry - lower[i]), as in the original. Exits, fees and metrics come from
reference/long_logic_engine.py. Run from the repository root:

    uv run python "reference/long_bollinger+RSI+regime/train_full_grid_gpu.py" --verify 8
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import long_logic_engine as engine  # noqa: E402
from long_logic_engine import (  # noqa: E402,F401
    DEFAULT_CSV, DEFAULT_SCORE, SCORE_NAMES, load_bars, resolve_fee_bars, score_result,
    timeframe_nanos, trade_metrics,
)

MODE = engine.MODE_BREAKOUT
SEARCH_SPACE = {
    "boll_window": [6, 7, 8, 10, 12, 14, 16, 18, 20, 25, 30],
    "boll_window_dev": [1.8, 2.0, 2.2, 2.4],
    "rsi_window": [30, 40, 50, 60, 80],
    "rsi_threshold": [50, 54, 58, 62, 66, 70],
    "trend_window": [0, 400, 800, 1600],
    "sl_multiplier": [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
    "tp_sl_ratio": [1.1],
}


def parameter_grid(limit=None):
    return engine.parameter_grid(SEARCH_SPACE, limit)


def run_gpu_grid(bars, grid, timeframe, max_holding_bars=288, fee_bars=None, score_formula=DEFAULT_SCORE):
    return engine.run_gpu_grid(bars, grid, timeframe, MODE, max_holding_bars, fee_bars, score_formula)


def cpu_trace(bars, params, timeframe, max_holding_bars=288, fee_bars=None):
    return engine.cpu_trace(bars, params, timeframe, MODE, max_holding_bars, fee_bars)


def verify_samples(bars, result, sample_count, max_holding_bars=288, fee_bars=None, timeframe="15min"):
    engine.verify_samples(bars, result, sample_count, MODE, max_holding_bars, fee_bars, timeframe)


if __name__ == "__main__":
    engine.main(__doc__, SEARCH_SPACE, MODE, Path(__file__))
