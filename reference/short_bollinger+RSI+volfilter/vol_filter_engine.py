"""Bollinger + RSI breakout with a low realized-volatility filter: numba CPU grid with per-period R.

Entry at the close of bar t (multi_position_engine's entry_mode 0, one position at a time):

    short: close[t-1] > lower[t] > close[t] and RSI[t] < rsi_threshold, risk = (upper[t] - close[t]) x sl_multiplier
    long : close[t-1] < upper[t] < close[t] and RSI[t] > rsi_threshold, risk = (close[t] - lower[t]) x sl_multiplier

Volatility filter (vol_ratio_max = 0 turns it off): the signal also needs

    vol_ratio[t] = std(log returns over the last vol_window bars) / median of that std over the last vol_days days
    vol_ratio[t] < vol_ratio_max

i.e. the break comes out of a quiet market. rsi_threshold = 100 (short) / 0 (long) drops the RSI condition: the
break bar closes lower (higher), so its RSI is below 100 (above 0).

Trailing stop (trail_r = 0 or absent turns it off): after every bar the stop moves to the best price since the entry
(lowest low for a short, highest high for a long) + / - trail_r x risk when that tightens it; it never loosens and
takes effect from the next bar. A trailed stop books (entry - stop) / risk for a short ((stop - entry) / risk for a
long), outcome "trailing_stop"; the untouched initial stop books -1 R.

Exits from the bar after the entry, in this order: timeout (max_holding_bars) -> stop -> take profit
(tp_sl_ratio x risk; 0 = none) -> end of data. Fees, the 20%-of-R entry-fee cap, the first signal bar (rsi_window)
and the skip-while-open rule are multi_position_engine's (max_open = 1), so with vol_ratio_max = 0 and trail_r = 0
the trades are exactly engine.cpu_trace's.

run_grid returns, per grid row and period (period_index per bar, -1 = not counted), the net R and the count of the
trades closed in it, plus trade-level performance / max drawdown / trade count over the bars in [stats_from,
stats_to). trace replays one row in plain Python (float64) and returns its trade list.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numba as nb
import numpy as np
import pandas as pd

REFERENCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REFERENCE / "multi_position"))
import multi_position_engine as engine  # noqa: E402

SIDE_LONG = engine.SIDE_LONG
SIDE_SHORT = engine.SIDE_SHORT
load_bars = engine.load_bars
GRID_COLUMNS = ("boll_window", "boll_window_dev", "rsi_window", "rsi_threshold", "sl_multiplier", "tp_sl_ratio",
                "max_holding_bars", "vol_window", "vol_days", "vol_ratio_max", "trail_r")
OPTIONAL_COLUMNS = {"trail_r": [0.0]}
OUTCOMES = ("timeout", "stop", "trailing_stop", "take_profit", "end_of_data")


def bars_per_day(timeframe: str) -> float:
    return pd.Timedelta(days=1) / pd.Timedelta(engine.timeframe_nanos(timeframe), unit="ns")


def parameter_grid(space: dict) -> pd.DataFrame:
    space = {**OPTIONAL_COLUMNS, **space}
    missing = set(GRID_COLUMNS) - set(space)
    if missing:
        raise ValueError(f"search space is missing {sorted(missing)}")
    return pd.DataFrame(itertools.product(*(space[k] for k in GRID_COLUMNS)), columns=list(GRID_COLUMNS))


def vol_ratio(close: np.ndarray, window: int, days: int, timeframe: str) -> np.ndarray:
    """Realized vol over `window` bars, over its trailing `days`-day median (NaN until a third of it is warm)."""
    lookback = max(int(round(days * bars_per_day(timeframe))), 2)
    vol = np.log(pd.Series(close)).diff().rolling(int(window)).std()
    return (vol / vol.rolling(lookback, min_periods=max(lookback // 3, 1)).median()).to_numpy()


def _indicators(bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str):
    close = bars["close"].to_numpy(np.float64)
    band_keys = list(dict.fromkeys(zip(grid.boll_window.astype(int), grid.boll_window_dev.astype(float))))
    rsi_keys = sorted({int(w) for w in grid.rsi_window})
    vol_keys = list(dict.fromkeys((int(w), int(d)) for w, d, m in zip(grid.vol_window, grid.vol_days,
                                                                       grid.vol_ratio_max) if m > 0)) or [(0, 0)]
    upper = np.empty((len(band_keys), len(close)))
    lower = np.empty_like(upper)
    for i, (w, d) in enumerate(band_keys):
        upper[i], lower[i] = engine._cpu_bands(close, w, d)
    rsi = np.stack([engine._cpu_rsi(close, w) for w in rsi_keys])
    vr = np.stack([vol_ratio(close, w, d, timeframe) if w else np.full(len(close), np.nan) for w, d in vol_keys])
    band_map = {k: i for i, k in enumerate(band_keys)}
    rsi_map = {k: i for i, k in enumerate(rsi_keys)}
    vol_map = {k: i for i, k in enumerate(vol_keys)}
    b_idx = np.array([band_map[(int(w), float(d))] for w, d in zip(grid.boll_window, grid.boll_window_dev)])
    r_idx = np.array([rsi_map[int(w)] for w in grid.rsi_window])
    v_idx = np.array([vol_map.get((int(w), int(d)), 0) for w, d in zip(grid.vol_window, grid.vol_days)])
    return upper, lower, rsi, vr, b_idx, r_idx, v_idx


@nb.njit(parallel=True, cache=True)
def _grid_kernel(side, close, high, low, upper, lower, rsi, vr, period, n_periods, stats_from, stats_to,
                 fee_bars, entry_fee_rate, holding_fee_rate, max_fee_r,
                 b_idx, r_idx, v_idx, starts, thresholds, mults, tps, holds, vmax, trails):
    C = b_idx.size
    n = close.size
    R = np.zeros((C, n_periods))
    N = np.zeros((C, n_periods), dtype=np.int32)
    perf = np.zeros(C)
    max_dd = np.zeros(C)
    trades = np.zeros(C, dtype=np.int32)
    wins = np.zeros(C, dtype=np.int32)
    for c in nb.prange(C):
        b, r, v = b_idx[c], r_idx[c], v_idx[c]
        thr, m, tp, hd, cap, tr = thresholds[c], mults[c], tps[c], holds[c], vmax[c], trails[c]
        in_pos = False
        ei = 0
        entry = 0.0
        risk = 0.0
        stop = 0.0
        stop0 = 0.0
        best = 0.0
        target = 0.0
        cum = 0.0
        peak = 0.0
        for t in range(starts[c], n):
            if not in_pos:
                if t < n - 1:
                    if side == -1:
                        sig = close[t - 1] > lower[b, t] and close[t] < lower[b, t] and rsi[r, t] < thr
                        rk = (upper[b, t] - close[t]) * m
                    else:
                        sig = close[t - 1] < upper[b, t] and close[t] > upper[b, t] and rsi[r, t] > thr
                        rk = (close[t] - lower[b, t]) * m
                    if sig and cap > 0:
                        sig = vr[v, t] < cap  # NaN (not warm) -> no signal
                    if sig and close[t] > 0 and rk > 0 and entry_fee_rate / (rk / close[t]) <= max_fee_r:
                        in_pos = True
                        ei, entry, risk = t, close[t], rk
                        stop = entry - side * rk
                        stop0 = stop
                        best = entry
                        target = entry + side * tp * rk
                continue
            holding = t - ei
            if holding >= hd:
                gross = side * (close[t] - entry) / risk
            elif (side == 1 and low[t] <= stop) or (side == -1 and high[t] > stop):
                gross = -1.0 if stop == stop0 else side * (stop - entry) / risk
            elif tp > 0 and ((side == 1 and high[t] >= target) or (side == -1 and low[t] < target)):
                gross = tp
            elif t == n - 1:
                gross = side * (close[t] - entry) / risk
            else:
                if tr > 0:
                    if side == -1:
                        best = min(best, low[t])
                        stop = min(stop, best + tr * risk)
                    else:
                        best = max(best, high[t])
                        stop = max(stop, best - tr * risk)
                continue
            slr = risk / entry
            net = gross - entry_fee_rate / slr - holding_fee_rate * (holding / fee_bars) / slr
            p = period[t]
            if p >= 0:
                R[c, p] += net
                N[c, p] += 1
            if stats_from <= t < stats_to:
                cum += net
                peak = max(peak, cum)
                max_dd[c] = max(max_dd[c], peak - cum)
                trades[c] += 1
                if net > 0:
                    wins[c] += 1
            in_pos = False
        perf[c] = cum
    return R, N, perf, max_dd, trades, wins


def run_grid(side: int, bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str, period_index: np.ndarray,
             stats_from: int = 0, stats_to: int | None = None, fee_bars: float | None = None):
    """(R, N, stats): R/N are rows x periods; stats has performance, max_dd, number_of_trade, win_rate over the
    trades closed in bars [stats_from, stats_to)."""
    if side not in (SIDE_LONG, SIDE_SHORT):
        raise ValueError("side must be +1 (long) or -1 (short)")
    period_index = np.asarray(period_index, dtype=np.int64)
    if len(period_index) != len(bars):
        raise ValueError("period_index needs one entry per bar")
    upper, lower, rsi, vr, b_idx, r_idx, v_idx = _indicators(bars, grid, timeframe)
    R, N, perf, dd, trades, wins = _grid_kernel(
        side, bars["close"].to_numpy(np.float64), bars["high"].to_numpy(np.float64),
        bars["low"].to_numpy(np.float64), upper, lower, rsi, vr, period_index, max(int(period_index.max()) + 1, 1),
        int(stats_from), len(bars) if stats_to is None else int(stats_to), engine.resolve_fee_bars(timeframe, fee_bars),
        engine.ENTRY_FEE_RATE, engine.HOLDING_FEE_RATE, engine.MAX_FEE_R,
        b_idx.astype(np.int64), r_idx.astype(np.int64), v_idx.astype(np.int64),
        np.maximum(grid.rsi_window.to_numpy(np.int64), 1),
        grid.rsi_threshold.to_numpy(np.float64), grid.sl_multiplier.to_numpy(np.float64),
        grid.tp_sl_ratio.to_numpy(np.float64), grid.max_holding_bars.to_numpy(np.int64),
        grid.vol_ratio_max.to_numpy(np.float64),
        grid.trail_r.to_numpy(np.float64) if "trail_r" in grid else np.zeros(len(grid)))
    stats = pd.DataFrame({"performance": perf, "max_dd": dd, "number_of_trade": trades,
                          "win_rate": np.where(trades > 0, wins / np.maximum(trades, 1), np.nan)}, index=grid.index)
    return R, N, stats


def trace(side: int, bars: pd.DataFrame, params: dict, timeframe: str, fee_bars: float | None = None) -> pd.DataFrame:
    """Every trade of one parameter set, float64, plain Python (an independent replay of the kernel's rules)."""
    fee_bars = engine.resolve_fee_bars(timeframe, fee_bars)
    close = bars["close"].to_numpy(np.float64)
    high = bars["high"].to_numpy(np.float64)
    low = bars["low"].to_numpy(np.float64)
    upper, lower = engine._cpu_bands(close, int(params["boll_window"]), float(params["boll_window_dev"]))
    rsi = engine._cpu_rsi(close, int(params["rsi_window"]))
    cap = float(params["vol_ratio_max"])
    vr = vol_ratio(close, int(params["vol_window"]), int(params["vol_days"]), timeframe) if cap > 0 else None
    thr, mult = float(params["rsi_threshold"]), float(params["sl_multiplier"])
    tp, hold = float(params["tp_sl_ratio"]), int(params["max_holding_bars"])
    trail = float(params.get("trail_r", 0.0))
    rows, pos = [], None
    n = len(close)
    for t in range(max(int(params["rsi_window"]), 1), n):
        if pos is None:
            if t >= n - 1:
                continue
            if side == SIDE_SHORT:
                sig = close[t - 1] > lower[t] > close[t] and rsi[t] < thr
                risk = (upper[t] - close[t]) * mult
            else:
                sig = close[t - 1] < upper[t] < close[t] and rsi[t] > thr
                risk = (close[t] - lower[t]) * mult
            if sig and vr is not None:
                sig = bool(vr[t] < cap)
            if sig and close[t] > 0 and risk > 0 and engine.ENTRY_FEE_RATE / (risk / close[t]) <= engine.MAX_FEE_R:
                pos = {"entry_index": t, "entry_price": close[t], "risk": risk,
                       "stop_loss": close[t] - side * risk, "take_profit": close[t] + side * tp * risk if tp > 0 else np.nan}
                initial_stop, best = pos["stop_loss"], close[t]
            continue
        holding = t - pos["entry_index"]
        stop_hit = low[t] <= pos["stop_loss"] if side == SIDE_LONG else high[t] > pos["stop_loss"]
        tp_hit = tp > 0 and (high[t] >= pos["take_profit"] if side == SIDE_LONG else low[t] < pos["take_profit"])
        if holding >= hold:
            outcome, gross = "timeout", side * (close[t] - pos["entry_price"]) / pos["risk"]
        elif stop_hit and pos["stop_loss"] == initial_stop:
            outcome, gross = "stop", -1.0
        elif stop_hit:
            outcome, gross = "trailing_stop", side * (pos["stop_loss"] - pos["entry_price"]) / pos["risk"]
        elif tp_hit:
            outcome, gross = "take_profit", tp
        elif t == n - 1:
            outcome, gross = "end_of_data", side * (close[t] - pos["entry_price"]) / pos["risk"]
        else:
            if trail > 0:
                if side == SIDE_SHORT:
                    best = min(best, low[t])
                    pos["stop_loss"] = min(pos["stop_loss"], best + trail * pos["risk"])
                else:
                    best = max(best, high[t])
                    pos["stop_loss"] = max(pos["stop_loss"], best - trail * pos["risk"])
            continue
        slr = pos["risk"] / pos["entry_price"]
        entry_fee = engine.ENTRY_FEE_RATE / slr
        holding_fee = engine.HOLDING_FEE_RATE * (holding / fee_bars) / slr
        rows.append({**pos, "exit_index": t, "holding_bars": holding, "outcome": outcome, "gross_r": gross,
                     "entry_fee": entry_fee, "holding_fee": holding_fee, "net_r": gross - entry_fee - holding_fee})
        pos = None
    trades = pd.DataFrame(rows, columns=["entry_index", "entry_price", "risk", "stop_loss", "take_profit",
                                         "exit_index", "holding_bars", "outcome", "gross_r", "entry_fee",
                                         "holding_fee", "net_r"])
    ts = bars["timestamp"].to_numpy()
    trades.insert(1, "entry_timestamp", ts[trades.entry_index.to_numpy(dtype=int)])
    trades.insert(7, "exit_timestamp", ts[trades.exit_index.to_numpy(dtype=int)])
    return trades


def month_index(bars: pd.DataFrame, first_month: str) -> np.ndarray:
    """Calendar month of every bar, counted from first_month ('YYYY-MM'); earlier bars get -1."""
    start = pd.Timestamp(first_month)
    m = ((bars.timestamp.dt.year - start.year) * 12 + bars.timestamp.dt.month - start.month).to_numpy()
    return np.where(m >= 0, m, -1)


def monthly_max_dd(R: np.ndarray) -> np.ndarray:
    eq = np.concatenate([np.zeros((len(R), 1)), np.cumsum(R, axis=1)], axis=1)
    return (np.maximum.accumulate(eq, axis=1) - eq).max(axis=1)
