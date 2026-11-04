"""AVAX short Bollinger/RSI breakdown gated by a slow downtrend: grid search on CUDA (fp32).

Entry at bar i: the short_bollinger+RSI breakdown (close[i-1] > lower[i] > close[i] and
RSI(rsi_window)[i] < rsi_threshold) while close[i] < SMA(close, trend_window)[i]; trend_window = 0 switches
the filter off (= the original short script, one position at a time, bit for bit). Stop distance =
sl_multiplier x (upper[i] - entry), TP = tp_sl_ratio x risk, timeout max_holding_bars, fees as the original.
trend_window values (15min bars): 672 = 1 week, 1344 = 2 weeks, 2880 = 30 days, 5760 = 60 days, 8640 = 90 days.
The SMA is computed on whatever bars are passed in unless they carry trend_sma_<window> columns
(engine.add_trend_columns on the full history; walk_forward_multi_asset.py does this). Logic lives in
reference/multi_position/multi_position_engine.py. Run from the repository root:

    uv run python "reference/short_bollinger+RSI+regime/train_full_grid_gpu.py" --verify 8 \\
        --start 2023-07-01 --end 2025-07-01 --max-open 1
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

SIDE = engine.SIDE_SHORT
MAX_OPEN = 1  # one position at a time, as the original short script
DEFAULT_CSV = engine.DEFAULT_CSV[SIDE]
TREND_WINDOWS = [0, 672, 1344, 2880, 5760, 8640]
SEARCH_SPACE = {
    "boll_window": [6, 8, 10, 12, 14, 16, 18, 20, 25, 30],
    "boll_window_dev": [1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3],
    "rsi_window": [30, 35, 40, 45, 50, 60, 80],
    "rsi_threshold": list(range(35, 50)),
    "sl_multiplier": [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0],
    "tp_sl_ratio": [1.1],
    "trend_window": TREND_WINDOWS,
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
