"""AVAX long pullback in an uptrend, at the lower Bollinger band: grid search on CUDA (fp32).

Entry at bar i: close comes back inside the lower band (close[i-1] < lower[i] < close[i]) while
the slow trend is up, close[i] > SMA(close, trend_window)[i]; trend_window = 0 switches the trend
filter off (baseline: the bare re-entry trigger). Stop distance = sl_multiplier x band width
(upper[i] - lower[i]); take profit = tp_sl_ratio x risk. Exits, fees and metrics come from
reference/long_logic_engine.py. Run from the repository root:

    uv run python "reference/long_bollinger+pullback/train_full_grid_gpu.py" --verify 8
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

MODE = engine.MODE_REENTRY
SEARCH_SPACE = {
    "boll_window": [10, 14, 20, 25, 30, 40, 50],
    "boll_window_dev": [1.8, 2.0, 2.2, 2.4, 2.6, 2.8],
    "trend_window": [0, 100, 200, 400, 800, 1200, 1600],
    "sl_multiplier": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
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
