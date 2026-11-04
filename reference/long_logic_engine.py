"""Shared CUDA grid engine for the AVAX long Bollinger logics (fp32 on the GPU, float64 CPU replay).

Exits, fees and metrics are those of long_bollinger+RSI/train_full_grid_gpu.py: one open position,
take profit at tp_sl_ratio x risk, stop at risk, timeout after max_holding_bars, entry fee
0.001 / sl_price_ratio (trade skipped above 0.2 R), holding fee 0.0001 per fee_bars bars. The one
deliberate difference: the holding fee charges once per 8 hours on every timeframe, so 15min uses
fee_bars = 32 (the RSI script kept the legacy 8 on 15min, four times the short side's fee).

Two entry triggers on the Bollinger band of bar i:
  MODE_BREAKOUT  close[i-1] < upper[i] < close[i]; risk = sl_multiplier x (entry - lower[i])
  MODE_REENTRY   close[i-1] < lower[i] < close[i]; risk = sl_multiplier x (upper[i] - lower[i])
Exit options, each switched on by its grid column:
  tp_sl_ratio = 0           no take profit (exit by stop, trailing stop or timeout)
  trail_r (0 = off)         trailing stop: after each bar that does not exit, the stop rises to
                            max(stop, highest high since entry - trail_r x risk); a bar's own high
                            only moves the stop for later bars (no intrabar look-ahead)
  exit_rule / exit_param    indicator exit judged on bar j's close and filled at that close, after
                            the timeout, stop, take-profit and end-of-data checks:
                            1 RSI(rsi_window) < exit_param; 2 close < lowest low of the exit_param
                            bars before j (Donchian); 3 close < highest high since entry -
                            exit_param x ATR(50) (Chandelier); 4 %B of the entry band < exit_param
  max_holding_bars          per-combination timeout (else the run_gpu_grid argument)
Optional filters, each switched on by its grid column:
  rsi_window/rsi_threshold  breakout: RSI[i] > threshold; re-entry: RSI[i] < threshold
  trend_window (0 = off)    close[i] > SMA(close, trend_window)[i]
  vol_window/vol_mult       volume[i] > vol_mult x SMA(volume, vol_window)[i-1] (vol_mult 0 = off)
"""

from __future__ import annotations

import argparse
import ast
import itertools
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from numba import cuda
from pandas.tseries.frequencies import to_offset
from pandas.tseries.offsets import Tick


DEFAULT_CSV = Path(__file__).resolve().parent / "long_bollinger+RSI" / "avaxusdt_futures_5min.csv"
DEFAULT_SCORE = "r_martin_monthly"
SCORE_NAMES = frozenset({
    "performance", "max_dd", "avg_dd", "r2", "number_of_trade",
    "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly",
})
MONTH_DAYS = 365.2425 / 12
RAW_BAR_NANOS = pd.Timedelta(minutes=5).value
MODE_BREAKOUT = 0
MODE_REENTRY = 1
METRICS = ("performance", "max_dd", "avg_dd", "r2", "r_per_month", "ulcer_r",
           "r_martin_monthly", "r_sharpe_monthly")


def timeframe_nanos(timeframe: str) -> int:
    try:
        offset = to_offset(timeframe)
    except ValueError as exc:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use a fixed pandas offset like 5min, 15min, 1h, or 4h") from exc
    if not isinstance(offset, Tick):
        raise ValueError(f"Timeframe {timeframe!r} must be a fixed tick offset like 5min, 15min, 1h, or 4h")
    nanos = int(offset.nanos)
    if nanos <= 0:
        raise ValueError("Timeframe must be positive")
    if nanos < RAW_BAR_NANOS:
        raise ValueError("Timeframe must be at least 5min because the source data is 5min")
    if nanos % RAW_BAR_NANOS != 0:
        raise ValueError("Timeframe must be a multiple of the 5min source data")
    return nanos


def resolve_fee_bars(timeframe: str, fee_bars: float | None = None) -> float:
    """Bars per holding-fee charge: 8 hours on every timeframe (15min = 32, 1h = 8)."""
    if fee_bars is not None:
        if fee_bars <= 0:
            raise ValueError("fee_bars must be positive")
        return float(fee_bars)
    bar_hours = pd.Timedelta(timeframe_nanos(timeframe), unit="ns") / pd.Timedelta(hours=1)
    return float(8.0 / bar_hours)


def load_bars(csv_path: Path, timeframe: str, start: str, end: str) -> pd.DataFrame:
    timeframe_nanos(timeframe)
    bars = pd.read_csv(csv_path, parse_dates=["timestamp"])
    bars = bars.set_index("timestamp").sort_index()
    bars = bars.resample(timeframe).agg({"open": "first", "close": "last",
                                         "high": "max", "low": "min",
                                         "volume": "sum"}).dropna()
    bars = bars.loc[start:end].reset_index()
    if bars.empty:
        raise ValueError("No bars remain after resampling/date filtering")
    return bars


def day_end_flags(bars: pd.DataFrame) -> np.ndarray:
    dates = bars["timestamp"].dt.normalize()
    flags = dates.ne(dates.shift(-1)).to_numpy(dtype=np.int8)
    if len(flags):
        flags[-1] = 1
    return np.ascontiguousarray(flags)


def score_result(result: pd.DataFrame, formula: str) -> pd.DataFrame:
    """Evaluate arithmetic over known result columns; never execute Python code."""
    parsed = ast.parse(formula, mode="eval")
    if sum(1 for _ in ast.walk(parsed)) > 64:
        raise ValueError("Score formula is too long")
    values = {name: result[name].to_numpy(dtype=np.float64) for name in SCORE_NAMES}

    def calculate(node: ast.AST):
        if isinstance(node, ast.Expression):
            return calculate(node.body)
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = calculate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = calculate(node.left), calculate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
        raise ValueError("Score formula may contain only numeric literals, + - * / **, "
                         "parentheses, and: " + ", ".join(sorted(SCORE_NAMES)))

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        scores = np.broadcast_to(calculate(parsed), len(result)).astype(np.float64).copy()
    scores[~np.isfinite(scores)] = np.nan
    result = result.copy()
    result["score"] = scores
    result["score_formula"] = formula
    return result


def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    """float64 SMA ending at each bar; NaN before the window fills."""
    return pd.Series(np.asarray(values, dtype=np.float64)).rolling(window).mean().to_numpy()


CHANDELIER_ATR = 50
EXIT_RULES = {"none": 0, "rsi": 1, "donchian": 2, "chandelier": 3, "pctb": 4}


def donchian_low(low: np.ndarray, window: int) -> np.ndarray:
    """Lowest low of the `window` bars before each bar (excludes the bar itself); NaN until filled."""
    lowest = pd.Series(np.asarray(low, dtype=np.float64)).rolling(window).min().to_numpy()
    return np.r_[np.nan, lowest[:-1]]


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int) -> np.ndarray:
    """Wilder ATR seeded with the SMA of the first `window` true ranges (as registry.atr); float64."""
    high, low, close = (np.asarray(a, dtype=np.float64) for a in (high, low, close))
    previous = np.r_[np.nan, close[:-1]]
    true_range = np.fmax(high - low, np.fmax(np.abs(high - previous), np.abs(low - previous)))
    out = np.full(len(close), np.nan)
    if len(close) < window:
        return out
    average = true_range[:window].mean()
    out[window - 1] = average
    alpha = 1.0 / window
    for k in range(window, len(close)):
        average = (1 - alpha) * average + alpha * true_range[k]
        out[k] = average
    return out


# --------------------------------------------------------------------------------------------
# GPU


@cuda.jit
def _bollinger_bands(close, windows, deviations, upper, lower):
    task = cuda.grid(1)
    if task >= windows.size * close.size:
        return
    w_idx = task // close.size
    i = task % close.size
    window = windows[w_idx]
    deviation = deviations[w_idx]
    if i < window - 1:
        upper[w_idx, i] = math.nan
        lower[w_idx, i] = math.nan
        return
    total = np.float32(0)
    for j in range(i - window + 1, i + 1):
        value = close[j]
        if value != value:
            upper[w_idx, i] = math.nan
            lower[w_idx, i] = math.nan
            return
        total += value
    mean = total / np.float32(window)
    squared = np.float32(0)
    for j in range(i - window + 1, i + 1):
        difference = close[j] - mean
        squared += difference * difference
    spread = deviation * np.float32(math.sqrt(squared / np.float32(window)))
    upper[w_idx, i] = mean + spread
    lower[w_idx, i] = mean - spread


@cuda.jit
def _rsi_windows(close, windows, rsi):
    """RSI as in long_bollinger+RSI (zero-seeded smoothing, alpha = 1/n); NaN before bar n-1."""
    w_idx = cuda.grid(1)
    if w_idx >= windows.size:
        return
    window = windows[w_idx]
    zero = np.float32(0)
    one = np.float32(1)
    hundred = np.float32(100)
    alpha = one / np.float32(window)
    up_average = zero
    down_average = zero
    rsi[w_idx, 0] = math.nan
    for i in range(1, close.size):
        delta = close[i] - close[i - 1]
        up = delta if delta > zero else zero
        down = -delta if delta < zero else zero
        up_average = (one - alpha) * up_average + alpha * up
        down_average = (one - alpha) * down_average + alpha * down
        if i < window - 1:
            rsi[w_idx, i] = math.nan
        elif down_average == zero:
            rsi[w_idx, i] = hundred
        else:
            strength = up_average / down_average
            rsi[w_idx, i] = hundred - hundred / (one + strength)


@cuda.jit(device=True)
def _entry_signal(mode, close, volume, upper, lower, rsi, trend, vol_avg, bw, rw, tw, vw,
                  threshold, vol_mult, i):
    upper_band = upper[bw, i]
    lower_band = lower[bw, i]
    if mode == 0:
        if not (close[i - 1] < upper_band and close[i] > upper_band):
            return False
    else:
        if not (close[i - 1] < lower_band and close[i] > lower_band):
            return False
    if rw >= 0:
        value = rsi[rw, i]
        if mode == 0:
            if not value > threshold:
                return False
        else:
            if not value < threshold:
                return False
    if tw >= 0:
        if not close[i] > trend[tw, i]:
            return False
    if vw >= 0:
        if not volume[i] > vol_mult * vol_avg[vw, i - 1]:
            return False
    return True


@cuda.jit(device=True)
def _indicator_exit(rule, param, close, upper, lower, rsi, donchian, atr, bw, rw, dw, highest, j):
    """Exit at bar j's close: 1 RSI < param, 2 close < Donchian low, 3 close < highest - param x ATR,
    4 %B < param. NaN never exits."""
    if rule == 1:
        return rsi[rw, j] < param
    if rule == 2:
        return close[j] < donchian[dw, j]
    if rule == 3:
        return close[j] < highest - param * atr[0, j]
    if rule == 4:
        width = upper[bw, j] - lower[bw, j]
        if not width > np.float32(0):
            return False
        return (close[j] - lower[bw, j]) / width < param
    return False


@cuda.jit
def _grid_kernel(close, high, low, volume, elapsed_days, upper, lower, rsi, trend, vol_avg,
                 donchian, atr, day_end_flags, elapsed_months, mode,
                 boll_indices, rsi_indices, starts, thresholds, trend_indices,
                 vol_indices, vol_mults, multipliers, tp_ratios, trail_rs,
                 exit_rules, exit_params, donchian_indices, max_holds, fee_bars,
                 performances, max_drawdowns, avg_drawdowns, r2_values,
                 r_per_months, ulcer_rs, r_martin_monthlies,
                 r_sharpe_monthlies, trade_counts):
    combo = cuda.grid(1)
    if combo >= boll_indices.size:
        return
    bw = boll_indices[combo]
    rw = rsi_indices[combo]
    tw = trend_indices[combo]
    vw = vol_indices[combo]
    threshold = thresholds[combo]
    vol_mult = vol_mults[combo]
    multiplier = multipliers[combo]
    tp_ratio = tp_ratios[combo]
    trail_r = trail_rs[combo]
    exit_rule = exit_rules[combo]
    exit_param = exit_params[combo]
    dw = donchian_indices[combo]
    max_holding_bars = max_holds[combo]
    zero = np.float32(0)
    one = np.float32(1)
    cumulative = zero
    peak = zero
    max_dd = zero
    drawdown_sum = zero
    count = 0
    daily_peak = zero
    ulcer_sum = zero
    daily_observations = 0
    last_daily_index = -1
    previous_day_cumulative = zero
    daily_r_mean = zero
    daily_r_m2 = zero

    # The notebook includes dummy trades at the first and final timestamps.
    observations = one
    mean_x = zero
    mean_y = zero
    m2x = zero
    m2y = zero
    covariance = zero

    n = close.size
    i = max(starts[combo], 1)
    for k in range(i):
        if day_end_flags[k] != 0:
            daily_r = cumulative - previous_day_cumulative
            previous_day_cumulative = cumulative
            daily_observations += 1
            daily_delta = daily_r - daily_r_mean
            daily_r_mean += daily_delta / np.float32(daily_observations)
            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
            ulcer_sum += (daily_peak - cumulative) * (daily_peak - cumulative)
            last_daily_index = k

    while i < n - 1:
        opened = False
        if _entry_signal(mode, close, volume, upper, lower, rsi, trend, vol_avg,
                         bw, rw, tw, vw, threshold, vol_mult, i):
            entry = close[i]
            if mode == 0:
                risk = (entry - lower[bw, i]) * multiplier
            else:
                risk = (upper[bw, i] - lower[bw, i]) * multiplier
            if entry > zero and risk > zero:
                sl_price_ratio = risk / entry
                entry_fee = np.float32(0.001) / sl_price_ratio
                if entry_fee <= np.float32(0.2):
                    stop = entry - risk
                    target = entry + tp_ratio * risk
                    highest = entry
                    stop_moved = False
                    if day_end_flags[i] != 0:
                        daily_r = cumulative - previous_day_cumulative
                        previous_day_cumulative = cumulative
                        daily_observations += 1
                        daily_delta = daily_r - daily_r_mean
                        daily_r_mean += daily_delta / np.float32(daily_observations)
                        daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                        if cumulative > daily_peak:
                            daily_peak = cumulative
                        ulcer_sum += (daily_peak - cumulative) * (daily_peak - cumulative)
                        last_daily_index = i
                    j = i + 1
                    while j < n:
                        holding = j - i
                        closed = False
                        if holding >= max_holding_bars:
                            trade_r = (close[j] - entry) / risk
                            closed = True
                        elif low[j] <= stop:
                            trade_r = (stop - entry) / risk if stop_moved else -one
                            closed = True
                        elif tp_ratio > zero and high[j] >= target:
                            trade_r = tp_ratio
                            closed = True
                        elif j == n - 1:
                            trade_r = (close[j] - entry) / risk
                            closed = True
                        else:
                            if high[j] > highest:
                                highest = high[j]
                            if exit_rule > 0 and _indicator_exit(
                                    exit_rule, exit_param, close, upper, lower, rsi, donchian,
                                    atr, bw, rw, dw, highest, j):
                                trade_r = (close[j] - entry) / risk
                                closed = True
                            elif trail_r > zero:
                                trailed = highest - trail_r * risk
                                if trailed > stop:
                                    stop = trailed
                                    stop_moved = True

                        if closed:
                            holding_fee = (np.float32(0.0001) *
                                           (np.float32(holding) / np.float32(fee_bars)) /
                                           sl_price_ratio)
                            cumulative += trade_r - entry_fee - holding_fee
                            count += 1
                            peak = max(peak, cumulative)
                            drawdown = peak - cumulative
                            max_dd = max(max_dd, drawdown)
                            drawdown_sum += drawdown

                            x = elapsed_days[j]
                            observations += one
                            dx = x - mean_x
                            mean_x += dx / observations
                            dy = cumulative - mean_y
                            mean_y += dy / observations
                            m2x += dx * (x - mean_x)
                            m2y += dy * (cumulative - mean_y)
                            covariance += dx * (cumulative - mean_y)
                            if day_end_flags[j] != 0:
                                daily_r = cumulative - previous_day_cumulative
                                previous_day_cumulative = cumulative
                                daily_observations += 1
                                daily_delta = daily_r - daily_r_mean
                                daily_r_mean += daily_delta / np.float32(daily_observations)
                                daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                                if cumulative > daily_peak:
                                    daily_peak = cumulative
                                daily_drawdown = daily_peak - cumulative
                                ulcer_sum += daily_drawdown * daily_drawdown
                                last_daily_index = j
                            i = j + 1  # The exit bar cannot also open a new trade.
                            opened = True
                            break
                        if day_end_flags[j] != 0:
                            daily_r = cumulative - previous_day_cumulative
                            previous_day_cumulative = cumulative
                            daily_observations += 1
                            daily_delta = daily_r - daily_r_mean
                            daily_r_mean += daily_delta / np.float32(daily_observations)
                            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                            if cumulative > daily_peak:
                                daily_peak = cumulative
                            daily_drawdown = daily_peak - cumulative
                            ulcer_sum += daily_drawdown * daily_drawdown
                            last_daily_index = j
                        j += 1
                    if opened:
                        continue
        if day_end_flags[i] != 0:
            daily_r = cumulative - previous_day_cumulative
            previous_day_cumulative = cumulative
            daily_observations += 1
            daily_delta = daily_r - daily_r_mean
            daily_r_mean += daily_delta / np.float32(daily_observations)
            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
            if cumulative > daily_peak:
                daily_peak = cumulative
            daily_drawdown = daily_peak - cumulative
            ulcer_sum += daily_drawdown * daily_drawdown
            last_daily_index = i
        i += 1

    # Final dummy repeats the last cumulative R and drawdown.
    drawdown_sum += peak - cumulative
    x = elapsed_days[n - 1]
    observations += one
    dx = x - mean_x
    mean_x += dx / observations
    dy = cumulative - mean_y
    mean_y += dy / observations
    m2x += dx * (x - mean_x)
    m2y += dy * (cumulative - mean_y)
    covariance += dx * (cumulative - mean_y)
    if m2y <= zero:
        r_squared = one
    elif m2x <= zero:
        r_squared = zero
    else:
        r_squared = covariance * covariance / (m2x * m2y)

    if last_daily_index != n - 1:
        daily_r = cumulative - previous_day_cumulative
        previous_day_cumulative = cumulative
        daily_observations += 1
        daily_delta = daily_r - daily_r_mean
        daily_r_mean += daily_delta / np.float32(daily_observations)
        daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
        if cumulative > daily_peak:
            daily_peak = cumulative
        daily_drawdown = daily_peak - cumulative
        ulcer_sum += daily_drawdown * daily_drawdown
    if elapsed_months > zero:
        r_per_month = cumulative / elapsed_months
    else:
        r_per_month = np.float32(math.nan)
    if daily_observations > 0:
        ulcer_r = np.float32(math.sqrt(ulcer_sum / np.float32(daily_observations)))
    else:
        ulcer_r = np.float32(math.nan)
    if elapsed_months > zero and ulcer_r > zero:
        r_martin_monthly = r_per_month / ulcer_r
    else:
        r_martin_monthly = np.float32(math.nan)
    if elapsed_months > zero and daily_observations >= 2 and daily_r_m2 > zero:
        daily_std = np.float32(math.sqrt(daily_r_m2 / np.float32(daily_observations - 1)))
        r_sharpe_monthly = (daily_r_mean / daily_std) * np.float32(math.sqrt(MONTH_DAYS))
    else:
        r_sharpe_monthly = np.float32(math.nan)

    max_drawdowns[combo] = np.float32(math.floor(max_dd * np.float32(100) +
                                                np.float32(0.5))) / np.float32(100)
    average = drawdown_sum / np.float32(count + 2)
    avg_drawdowns[combo] = np.float32(math.floor(average * np.float32(100) +
                                                np.float32(0.5))) / np.float32(100)
    performances[combo] = cumulative
    r2_values[combo] = r_squared
    r_per_months[combo] = r_per_month
    ulcer_rs[combo] = ulcer_r
    r_martin_monthlies[combo] = r_martin_monthly
    r_sharpe_monthlies[combo] = r_sharpe_monthly
    trade_counts[combo] = count


def _filter_index(grid: pd.DataFrame, column: str, active: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Distinct active windows of `column` and each combination's index into them (-1 = off)."""
    if column not in grid:
        return np.zeros(0, dtype=np.int32), np.full(len(grid), -1, dtype=np.int32)
    windows = np.unique(grid.loc[active, column].to_numpy(dtype=np.int32))
    lookup = {int(w): k for k, w in enumerate(windows)}
    indices = np.array([lookup[int(w)] if on else -1
                        for w, on in zip(grid[column], active)], dtype=np.int32)
    return windows, indices


def _filter_rows(rows: list[np.ndarray], n: int) -> np.ndarray:
    """Stack filter series; one NaN row when the filter is unused (kernels need a 2-D array)."""
    if not rows:
        return np.full((1, n), np.nan, dtype=np.float32)
    return np.ascontiguousarray(np.vstack(rows).astype(np.float32))


def run_gpu_grid(bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str, mode: int,
                 max_holding_bars: int = 288, fee_bars: float | None = None,
                 score_formula: str = DEFAULT_SCORE) -> tuple[pd.DataFrame, dict]:
    if not cuda.is_available():
        raise RuntimeError("CUDA GPU is unavailable")
    resolved_fee_bars = resolve_fee_bars(timeframe, fee_bars)
    if grid.empty or max_holding_bars < 1:
        raise ValueError("Grid must be nonempty; max_holding_bars must be positive")
    longest = max(int(grid[c].max()) for c in ("boll_window", "rsi_window", "trend_window", "vol_window")
                  if c in grid)
    if len(bars) <= longest:
        raise ValueError("Too few bars for the chosen indicator windows")
    score_result(pd.DataFrame({name: [1.0] for name in SCORE_NAMES}), score_formula)

    started = perf_counter()
    n = len(bars)
    close = np.ascontiguousarray(bars["close"].to_numpy(dtype=np.float32))
    high = np.ascontiguousarray(bars["high"].to_numpy(dtype=np.float32))
    low = np.ascontiguousarray(bars["low"].to_numpy(dtype=np.float32))
    volume = np.ascontiguousarray(bars["volume"].to_numpy(dtype=np.float32))
    elapsed_days = np.ascontiguousarray(
        ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() /
         86400).to_numpy(dtype=np.float32))
    elapsed_months = np.float32(float(elapsed_days[-1]) / MONTH_DAYS)
    end_flags = day_end_flags(bars)

    band_pairs = list(dict.fromkeys(
        (int(window), float(np.float32(deviation)))
        for window, deviation in zip(grid["boll_window"], grid["boll_window_dev"])))
    boll_map = {pair: k for k, pair in enumerate(band_pairs)}
    boll_indices = np.array([boll_map[(int(w), float(np.float32(d)))]
                             for w, d in zip(grid["boll_window"], grid["boll_window_dev"])], dtype=np.int32)

    uses_rsi = "rsi_window" in grid
    rsi_windows, rsi_indices = _filter_index(grid, "rsi_window", pd.Series(uses_rsi, index=grid.index))
    starts = (grid["rsi_window"].to_numpy(dtype=np.int32) if uses_rsi
              else np.ones(len(grid), dtype=np.int32))
    thresholds = (grid["rsi_threshold"].to_numpy(dtype=np.float32) if uses_rsi
                  else np.zeros(len(grid), dtype=np.float32))
    trend_on = grid["trend_window"] > 0 if "trend_window" in grid else pd.Series(False, index=grid.index)
    trend_windows, trend_indices = _filter_index(grid, "trend_window", trend_on)
    vol_on = grid["vol_mult"] > 0 if "vol_mult" in grid else pd.Series(False, index=grid.index)
    vol_windows, vol_indices = _filter_index(grid, "vol_window", vol_on)
    vol_mults = (grid["vol_mult"].to_numpy(dtype=np.float32) if "vol_mult" in grid
                 else np.zeros(len(grid), dtype=np.float32))
    trend = _filter_rows([rolling_mean(bars["close"], int(w)) for w in trend_windows], n)
    vol_avg = _filter_rows([rolling_mean(bars["volume"], int(w)) for w in vol_windows], n)

    exit_rules = (grid["exit_rule"].to_numpy(dtype=np.int32) if "exit_rule" in grid
                  else np.zeros(len(grid), dtype=np.int32))
    exit_params = (grid["exit_param"].to_numpy(dtype=np.float32) if "exit_param" in grid
                   else np.zeros(len(grid), dtype=np.float32))
    if (exit_rules == EXIT_RULES["rsi"]).any() and not uses_rsi:
        raise ValueError("The RSI exit needs rsi_window in the grid")
    donchian_on = pd.Series(exit_rules == EXIT_RULES["donchian"], index=grid.index)
    donchian_windows, donchian_indices = _filter_index(
        grid.assign(exit_window=exit_params.astype(np.int32)), "exit_window", donchian_on)
    donchian = _filter_rows([donchian_low(bars["low"], int(w)) for w in donchian_windows], n)
    atr = _filter_rows([atr_wilder(bars["high"], bars["low"], bars["close"], CHANDELIER_ATR)]
                       if (exit_rules == EXIT_RULES["chandelier"]).any() else [], n)
    max_holds = (grid["max_holding_bars"].to_numpy(dtype=np.int32) if "max_holding_bars" in grid
                 else np.full(len(grid), max_holding_bars, dtype=np.int32))
    if (max_holds < 1).any():
        raise ValueError("max_holding_bars must be positive")

    d_close = cuda.to_device(close)
    d_upper = cuda.device_array((len(band_pairs), n), dtype=np.float32)
    d_lower = cuda.device_array((len(band_pairs), n), dtype=np.float32)
    threads = 128
    tasks = len(band_pairs) * n
    _bollinger_bands[(tasks + threads - 1) // threads, threads](
        d_close, cuda.to_device(np.array([w for w, _ in band_pairs], dtype=np.int32)),
        cuda.to_device(np.array([d for _, d in band_pairs], dtype=np.float32)), d_upper, d_lower)
    if len(rsi_windows):
        d_rsi = cuda.device_array((len(rsi_windows), n), dtype=np.float32)
        _rsi_windows[(len(rsi_windows) + 31) // 32, 32](d_close, cuda.to_device(rsi_windows), d_rsi)
    else:
        d_rsi = cuda.to_device(np.full((1, n), np.nan, dtype=np.float32))
    cuda.synchronize()
    indicator_seconds = perf_counter() - started

    count = len(grid)
    outputs = [cuda.device_array(count, dtype=np.float32) for _ in range(8)]
    d_trade_count = cuda.device_array(count, dtype=np.int32)
    _grid_kernel[(count + threads - 1) // threads, threads](
        d_close, cuda.to_device(high), cuda.to_device(low), cuda.to_device(volume),
        cuda.to_device(elapsed_days), d_upper, d_lower, d_rsi, cuda.to_device(trend),
        cuda.to_device(vol_avg), cuda.to_device(donchian), cuda.to_device(atr),
        cuda.to_device(end_flags), elapsed_months, np.int32(mode),
        cuda.to_device(boll_indices), cuda.to_device(rsi_indices), cuda.to_device(starts),
        cuda.to_device(thresholds), cuda.to_device(trend_indices), cuda.to_device(vol_indices),
        cuda.to_device(vol_mults),
        cuda.to_device(grid["sl_multiplier"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["tp_sl_ratio"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["trail_r"].to_numpy(dtype=np.float32) if "trail_r" in grid
                       else np.zeros(len(grid), dtype=np.float32)),
        cuda.to_device(exit_rules), cuda.to_device(exit_params), cuda.to_device(donchian_indices),
        cuda.to_device(max_holds), np.float32(resolved_fee_bars),
        outputs[0], outputs[1], outputs[2], outputs[3],
        outputs[4], outputs[5], outputs[6], outputs[7], d_trade_count)
    cuda.synchronize()
    result = grid.copy()
    for column, output in zip(METRICS, outputs):
        result[column] = output.copy_to_host()
    result["number_of_trade"] = d_trade_count.copy_to_host()
    result = score_result(result, score_formula)
    return result, {"indicators_and_transfer_s": indicator_seconds,
                    "holding_fee_bars": resolved_fee_bars,
                    "total_gpu_s": perf_counter() - started}


# --------------------------------------------------------------------------------------------
# CPU float64 replay (verification and the notebooks' single-trial view)


def _cpu_rsi(close: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    up_average = down_average = 0.0
    alpha = 1.0 / window
    for i in range(1, len(close)):
        delta = close[i] - close[i - 1]
        up_average = (1 - alpha) * up_average + alpha * max(delta, 0.0)
        down_average = (1 - alpha) * down_average + alpha * max(-delta, 0.0)
        if i >= window - 1:
            out[i] = 100.0 if down_average == 0 else 100.0 - 100.0 / (1.0 + up_average / down_average)
    return out


def cpu_trace(bars: pd.DataFrame, params: dict, timeframe: str, mode: int,
              max_holding_bars: int = 288, fee_bars: float | None = None) -> pd.DataFrame:
    """Replay one parameter set in float64; one row per closed trade."""
    fee_bars = resolve_fee_bars(timeframe, fee_bars)
    close = bars["close"].to_numpy(dtype=np.float64)
    high = bars["high"].to_numpy(dtype=np.float64)
    low = bars["low"].to_numpy(dtype=np.float64)
    volume = bars["volume"].to_numpy(dtype=np.float64)
    rolling = bars["close"].astype(np.float64).rolling(int(params["boll_window"]))
    mean = rolling.mean().to_numpy()
    spread = float(params["boll_window_dev"]) * rolling.std(ddof=0).to_numpy()
    upper, lower = mean + spread, mean - spread
    rsi = _cpu_rsi(close, int(params["rsi_window"])) if "rsi_window" in params else None
    trend_window = int(params.get("trend_window", 0) or 0)
    trend = rolling_mean(close, trend_window) if trend_window > 0 else None
    vol_mult = float(params.get("vol_mult", 0) or 0)
    vol_avg = rolling_mean(volume, int(params["vol_window"])) if vol_mult > 0 else None
    multiplier, ratio = float(params["sl_multiplier"]), float(params["tp_sl_ratio"])
    trail_r = float(params.get("trail_r", 0) or 0)
    max_holding_bars = int(params.get("max_holding_bars", max_holding_bars))
    exit_rule = int(params.get("exit_rule", 0) or 0)
    exit_param = float(params.get("exit_param", 0) or 0)
    exit_name = {v: k for k, v in EXIT_RULES.items()}[exit_rule]
    if exit_rule == EXIT_RULES["rsi"] and rsi is None:
        raise ValueError("The RSI exit needs rsi_window")
    donchian = donchian_low(low, int(exit_param)) if exit_rule == EXIT_RULES["donchian"] else None
    atr = atr_wilder(high, low, close, CHANDELIER_ATR) if exit_rule == EXIT_RULES["chandelier"] else None

    def indicator_exit(j: int, highest: float) -> bool:
        if exit_rule == EXIT_RULES["rsi"]:
            return rsi[j] < exit_param
        if exit_rule == EXIT_RULES["donchian"]:
            return close[j] < donchian[j]
        if exit_rule == EXIT_RULES["chandelier"]:
            return close[j] < highest - exit_param * atr[j]
        if exit_rule == EXIT_RULES["pctb"]:
            width = upper[j] - lower[j]
            return bool(width > 0) and (close[j] - lower[j]) / width < exit_param
        return False

    timestamps = bars["timestamp"].to_numpy()

    trades = []
    n = len(close)
    i = max(int(params["rsi_window"]) if rsi is not None else 1, 1)
    while i < n - 1:
        if mode == MODE_BREAKOUT:
            signal = close[i - 1] < upper[i] < close[i]
        else:
            signal = close[i - 1] < lower[i] < close[i]
        if signal and rsi is not None:
            signal = (rsi[i] > params["rsi_threshold"] if mode == MODE_BREAKOUT
                      else rsi[i] < params["rsi_threshold"])
        if signal and trend is not None:
            signal = close[i] > trend[i]
        if signal and vol_avg is not None:
            signal = volume[i] > vol_mult * vol_avg[i - 1]
        if signal:
            entry = close[i]
            risk = ((entry - lower[i]) if mode == MODE_BREAKOUT else (upper[i] - lower[i])) * multiplier
            if entry > 0 and risk > 0:
                sl_ratio = risk / entry
                entry_fee = 0.001 / sl_ratio
                if entry_fee <= 0.2:
                    stop, target = entry - risk, entry + ratio * risk
                    highest, stop_moved = entry, False
                    for j in range(i + 1, n):
                        holding = j - i
                        if holding >= max_holding_bars:
                            gross, outcome = (close[j] - entry) / risk, "timeout"
                        elif low[j] <= stop:
                            gross, outcome = ((stop - entry) / risk, "trail") if stop_moved else (-1.0, "stop")
                        elif ratio > 0 and high[j] >= target:
                            gross, outcome = ratio, "target"
                        elif j == n - 1:
                            gross, outcome = (close[j] - entry) / risk, "end"
                        else:
                            highest = max(highest, high[j])
                            if exit_rule and indicator_exit(j, highest):
                                gross, outcome = (close[j] - entry) / risk, f"exit_{exit_name}"
                                break
                            if trail_r > 0 and highest - trail_r * risk > stop:
                                stop, stop_moved = highest - trail_r * risk, True
                            continue
                        break
                    holding_fee = 0.0001 * (holding / fee_bars) / sl_ratio
                    trades.append({
                        "entry_index": i, "exit_index": j,
                        "open_timestamp": timestamps[i], "close_timestamp": timestamps[j],
                        "entry_price": entry, "risk": risk, "stop_loss": stop, "take_profit": target,
                        "outcome": outcome, "gross_r": gross, "entry_fee": entry_fee,
                        "holding_fee": holding_fee, "net_r": gross - entry_fee - holding_fee,
                    })
                    i = j + 1
                    continue
        i += 1
    return pd.DataFrame(trades, columns=[
        "entry_index", "exit_index", "open_timestamp", "close_timestamp", "entry_price", "risk",
        "stop_loss", "take_profit", "outcome", "gross_r", "entry_fee", "holding_fee", "net_r"])


def trade_metrics(bars: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float]:
    """performance, r_per_month, ulcer_r, r_martin_monthly, r_sharpe_monthly from closed trades."""
    timestamps = bars["timestamp"]
    months = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 86400 / MONTH_DAYS
    total = float(trades["net_r"].sum())
    r_per_month = total / months if months > 0 else math.nan
    flags = day_end_flags(bars)
    exits, values = trades["exit_index"].to_numpy(), trades["net_r"].to_numpy()
    cumulative = peak = ulcer_sum = previous = 0.0
    daily: list[float] = []
    position = 0
    for bar, is_end in enumerate(flags):
        if not is_end:
            continue
        while position < len(exits) and exits[position] <= bar:
            cumulative += float(values[position])
            position += 1
        daily.append(cumulative - previous)
        previous = cumulative
        peak = max(peak, cumulative)
        ulcer_sum += (peak - cumulative) ** 2
    ulcer_r = math.sqrt(ulcer_sum / len(daily)) if daily else math.nan
    std = float(np.std(daily, ddof=1)) if len(daily) >= 2 else 0.0
    return {
        "performance": total, "number_of_trade": len(trades), "r_per_month": r_per_month,
        "ulcer_r": ulcer_r,
        "r_martin_monthly": r_per_month / ulcer_r if ulcer_r > 0 else math.nan,
        "r_sharpe_monthly": float(np.mean(daily)) / std * math.sqrt(MONTH_DAYS) if std > 0 else math.nan,
    }


def verify_samples(bars: pd.DataFrame, result: pd.DataFrame, sample_count: int, mode: int,
                   max_holding_bars: int = 288, fee_bars: float | None = None,
                   timeframe: str = "15min") -> None:
    """Compare evenly spaced GPU rows with the float64 CPU replay."""
    if sample_count < 1:
        return
    params = [c for c in ("boll_window", "boll_window_dev", "rsi_window", "rsi_threshold",
                          "trend_window", "vol_window", "vol_mult", "sl_multiplier", "tp_sl_ratio", "trail_r",
                          "exit_rule", "exit_param", "max_holding_bars")
              if c in result]
    positions = np.unique(np.linspace(0, len(result) - 1, min(sample_count, len(result)), dtype=int))
    trade_differences = 0
    largest = {k: 0.0 for k in ("performance", "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly")}
    for position in positions:
        row = result.iloc[position]
        expected = trade_metrics(bars, cpu_trace(bars, {k: row[k] for k in params}, timeframe, mode,
                                                 max_holding_bars, fee_bars))
        trade_differences += int(expected["number_of_trade"] != int(row["number_of_trade"]))
        for key in largest:
            if np.isfinite(expected[key]) or np.isfinite(row[key]):
                largest[key] = max(largest[key], abs(expected[key] - float(row[key])))
    print(f"Compared {len(positions)} rows with float64 CPU: {trade_differences} trade-count differences; "
          + "; ".join(f"max {k} difference={v:.6g}" for k, v in largest.items()))


# --------------------------------------------------------------------------------------------
# CLI shared by the logic scripts


def parameter_grid(search_space: dict, limit: int | None = None) -> pd.DataFrame:
    combos = itertools.product(*search_space.values())
    if limit is not None:
        combos = itertools.islice(combos, limit)
    return pd.DataFrame(combos, columns=list(search_space))


def main(doc: str, search_space: dict, mode: int, script: Path) -> None:
    parser = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--timeframe", default="15min")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-07-01")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--fee-bars", type=float, help="Bars per holding-fee charge; default 8 hours")
    parser.add_argument("--limit", type=int, help="Run only the first N combinations")
    parser.add_argument("--verify", type=int, default=0, metavar="N",
                        help="Compare N rows against the float64 CPU replay")
    parser.add_argument("--score-formula", default=DEFAULT_SCORE,
                        help="Arithmetic over: " + ",".join(sorted(SCORE_NAMES)))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    bars = load_bars(args.csv, args.timeframe, args.start, args.end)
    grid = parameter_grid(search_space, args.limit)
    result, timing = run_gpu_grid(bars, grid, args.timeframe, mode, args.max_holding_bars,
                                  args.fee_bars, args.score_formula)
    verify_samples(bars, result, args.verify, mode, args.max_holding_bars, args.fee_bars, args.timeframe)
    output = args.output or script.with_name(f"full_gpu_grid_results_{args.timeframe}_fp32.csv")
    result.to_csv(output, index=False)
    valid = result.dropna(subset=["score"])
    print(f"Bars: {len(bars)}; parameter sets: {len(grid)}")
    print("Timing:", timing)
    print("Score formula:", args.score_formula)
    print("Saved:", output)
    if not valid.empty:
        print("Best:", valid.loc[valid["score"].idxmax()].to_dict())
