"""CUDA full-grid search for the AVAX short Bollinger breakout in an ATR squeeze.

Entry at bar i: close crosses below the lower band (close[i-1] > lower[i] >
close[i]) while the bar before the breakout is in a volatility squeeze,
atr_ratio[i-1] = ATR(vol_fast)[i-1] / ATR(vol_slow)[i-1] < vol_threshold.
vol_threshold = 99 switches the filter off (baseline). The GPU computes
Bollinger bands and ATRs in float32, then replays one sequential position per
parameter set with the exits, fees and metrics of short_bollinger+RSI. Score
calculation stays on CPU so the formula can be changed safely with
--score-formula. Run from the repository root (the CPU check reads
quantcrucible's indicators):

    uv run python "reference/short_bollinger+atr_ratio/train_full_grid_gpu.py" --timeframe 1h --verify 8
"""

from __future__ import annotations

import argparse
import ast
import itertools
import math
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import pandas as pd
from numba import cuda
from pandas.tseries.frequencies import to_offset
from pandas.tseries.offsets import Tick


DEFAULT_CSV = Path(__file__).resolve().parents[1] / "short_bollinger+RSI" / "avaxusdt_futures_5min.csv"
VOL_FAST = [5, 10, 14, 20, 30, 50]
VOL_SLOW = [60, 100, 200, 300]
VOL_THRESHOLD = [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 99.0]
DEFAULT_SCORE_FORMULA = "performance**2/number_of_trade/avg_dd*r2**5"
DEFAULT_RANGES = {
    "1h": ("2023-01-01", "2024-07-01"),
    "15min": ("2024-01-01", "2025-07-01"),
}
FEE_HOLDING_DIVISOR = {"1h": 8.0, "15min": 32.0}
DEFAULT_SEARCH_SPACE_TIMEFRAME = "15min"
MIN_TIMEFRAME_NANOS = pd.Timedelta(minutes=5).value
MONTH_DAYS = 365.2425 / 12
SEARCH_SPACES = {
    "1h": {
        "boll_window": [6, 7, 8, 10, 12, 14, 16, 18, 20, 25, 30],
        "boll_window_dev": [2.0],
        "vol_fast": VOL_FAST,
        "vol_slow": VOL_SLOW,
        "vol_threshold": VOL_THRESHOLD,
        "sl_multiplier": [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0],
        "tp_sl_ratio": [1.1],
    },
    "15min": {
        "boll_window": [6, 8, 10, 12, 14, 16, 18, 20, 25, 30],
        "boll_window_dev": [2.0],
        "vol_fast": VOL_FAST,
        "vol_slow": VOL_SLOW,
        "vol_threshold": VOL_THRESHOLD,
        "sl_multiplier": [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
        "tp_sl_ratio": [1.1],
    },
}
SCORE_VARIABLES = {
    "performance", "max_dd", "avg_dd", "r2", "number_of_trade",
    "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly",
}


@cuda.jit
def _bollinger_windows(close, windows, deviations, upper, lower):
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
        delta = close[j] - mean
        squared += delta * delta
    width = deviation * np.float32(math.sqrt(squared / np.float32(window)))
    upper[w_idx, i] = mean + width
    lower[w_idx, i] = mean - width


@cuda.jit
def _atr_windows(close, high, low, windows, atr):
    """Wilder ATR seeded with the SMA of the first n true ranges, as in
    quantcrucible.core.strategy.registry.atr; NaN before bar n-1."""
    w_idx = cuda.grid(1)
    if w_idx >= windows.size:
        return
    window = windows[w_idx]
    one = np.float32(1)
    alpha = one / np.float32(window)
    total = np.float32(0)
    average = np.float32(0)
    for i in range(close.size):
        true_range = high[i] - low[i]
        if i > 0:
            up = abs(high[i] - close[i - 1])
            down = abs(low[i] - close[i - 1])
            if up > true_range:
                true_range = up
            if down > true_range:
                true_range = down
        if i < window - 1:
            total += true_range
            atr[w_idx, i] = math.nan
        elif i == window - 1:
            total += true_range
            average = total / np.float32(window)
            atr[w_idx, i] = average
        else:
            average = (one - alpha) * average + alpha * true_range
            atr[w_idx, i] = average


@cuda.jit(device=True)
def _squeezed(vol, fast, slow, k, threshold):
    """vol[fast, k] / vol[slow, k] < threshold; False on NaN or a non-positive slow side."""
    denominator = vol[slow, k]
    if not denominator > np.float32(0):
        return False
    return vol[fast, k] / denominator < threshold


@cuda.jit
def _grid_kernel(close, high, low, elapsed_days, day_ends, duration_months,
                 upper, lower, vol,
                 boll_indices, fast_indices, slow_indices, thresholds,
                 multipliers, tp_ratios, max_holding_bars, fee_divisor,
                 performances, max_drawdowns, avg_drawdowns, r2_values,
                 trade_counts, r_per_month_values, ulcer_r_values,
                 r_martin_monthly_values, r_sharpe_monthly_values):
    combo = cuda.grid(1)
    if combo >= boll_indices.size:
        return

    bw = boll_indices[combo]
    fw = fast_indices[combo]
    sw = slow_indices[combo]
    threshold = thresholds[combo]
    multiplier = multipliers[combo]
    ratio = tp_ratios[combo]
    n = close.size
    # Every combination starts at bar 1: NaN warm-up blocks entries, so all rows share one span.
    i = 1
    zero = np.float32(0)
    one = np.float32(1)
    cumulative = zero
    peak = zero
    max_dd = zero
    dd_sum = zero
    count = 0
    daily_peak = zero
    ulcer_sum = zero
    day_count = 0
    previous_day_cumulative = zero
    daily_r_mean = zero
    daily_r_m2 = zero

    observations = one
    mean_x = zero
    mean_y = zero
    m2x = zero
    m2y = zero
    covariance = zero

    for pre_i in range(i):
        if day_ends[pre_i] != 0:
            daily_r = cumulative - previous_day_cumulative
            previous_day_cumulative = cumulative
            day_count += 1
            daily_delta = daily_r - daily_r_mean
            daily_r_mean += daily_delta / np.float32(day_count)
            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
            if cumulative > daily_peak:
                daily_peak = cumulative
            daily_dd = daily_peak - cumulative
            ulcer_sum += daily_dd * daily_dd

    while i < n:
        if day_ends[i] != 0:
            daily_r = cumulative - previous_day_cumulative
            previous_day_cumulative = cumulative
            day_count += 1
            daily_delta = daily_r - daily_r_mean
            daily_r_mean += daily_delta / np.float32(day_count)
            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
            if cumulative > daily_peak:
                daily_peak = cumulative
            daily_dd = daily_peak - cumulative
            ulcer_sum += daily_dd * daily_dd
            if i >= n - 1:
                break

        lower_band = lower[bw, i]
        upper_band = upper[bw, i]
        if (i < n - 1 and close[i - 1] > lower_band and close[i] < lower_band and
                _squeezed(vol, fw, sw, i - 1, threshold)):
            entry = close[i]
            risk = (upper_band - entry) * multiplier
            if entry > zero and risk > zero:
                sl_price_ratio = risk / entry
                entry_fee = np.float32(0.001) / sl_price_ratio
                if entry_fee <= np.float32(0.2):
                    stop = entry + risk
                    target = entry - ratio * risk
                    j = i + 1
                    while j < n:
                        holding = j - i
                        if holding >= max_holding_bars:
                            trade_r = (entry - close[j]) / risk
                            break
                        if high[j] > stop:
                            trade_r = -one
                            break
                        if low[j] < target:
                            trade_r = ratio
                            break
                        if j == n - 1:
                            trade_r = (entry - close[j]) / risk
                            break
                        j += 1

                    for sample_i in range(i + 1, j):
                        if day_ends[sample_i] != 0:
                            daily_r = cumulative - previous_day_cumulative
                            previous_day_cumulative = cumulative
                            day_count += 1
                            daily_delta = daily_r - daily_r_mean
                            daily_r_mean += daily_delta / np.float32(day_count)
                            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                            if cumulative > daily_peak:
                                daily_peak = cumulative
                            daily_dd = daily_peak - cumulative
                            ulcer_sum += daily_dd * daily_dd

                    holding_fee = np.float32(0.0001) * (np.float32(holding) / fee_divisor) / sl_price_ratio
                    cumulative += trade_r - entry_fee - holding_fee
                    count += 1
                    if cumulative > peak:
                        peak = cumulative
                    current_dd = peak - cumulative
                    if current_dd > max_dd:
                        max_dd = current_dd
                    dd_sum += current_dd

                    x = elapsed_days[j]
                    observations += one
                    dx = x - mean_x
                    mean_x += dx / observations
                    dy = cumulative - mean_y
                    mean_y += dy / observations
                    m2x += dx * (x - mean_x)
                    m2y += dy * (cumulative - mean_y)
                    covariance += dx * (cumulative - mean_y)
                    if day_ends[j] != 0:
                        daily_r = cumulative - previous_day_cumulative
                        previous_day_cumulative = cumulative
                        day_count += 1
                        daily_delta = daily_r - daily_r_mean
                        daily_r_mean += daily_delta / np.float32(day_count)
                        daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                        if cumulative > daily_peak:
                            daily_peak = cumulative
                        daily_dd = daily_peak - cumulative
                        ulcer_sum += daily_dd * daily_dd
                    i = j + 1
                    continue
        i += 1

    current_dd = peak - cumulative
    dd_sum += current_dd
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

    performances[combo] = cumulative
    max_drawdowns[combo] = np.float32(math.floor(max_dd * np.float32(100) + np.float32(0.5))) / np.float32(100)
    avg_drawdowns[combo] = np.float32(math.floor((dd_sum / observations) * np.float32(100) + np.float32(0.5))) / np.float32(100)
    r2_values[combo] = r_squared
    trade_counts[combo] = count
    if duration_months > zero:
        r_per_month = cumulative / duration_months
    else:
        r_per_month = math.nan
    if day_count > 0:
        ulcer_r = np.float32(math.sqrt(ulcer_sum / np.float32(day_count)))
    else:
        ulcer_r = math.nan
    if ulcer_r == ulcer_r and ulcer_r > zero and duration_months > zero:
        r_martin = r_per_month / ulcer_r
    else:
        r_martin = math.nan
    if duration_months > zero and day_count >= 2 and daily_r_m2 > zero:
        daily_std = np.float32(math.sqrt(daily_r_m2 / np.float32(day_count - 1)))
        r_sharpe = (daily_r_mean / daily_std) * np.float32(math.sqrt(MONTH_DAYS))
    else:
        r_sharpe = math.nan
    r_per_month_values[combo] = r_per_month
    ulcer_r_values[combo] = ulcer_r
    r_martin_monthly_values[combo] = r_martin
    r_sharpe_monthly_values[combo] = r_sharpe


def timeframe_offset(timeframe: str) -> Tick:
    try:
        offset = to_offset(timeframe)
    except ValueError as exc:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use a fixed pandas offset such as 5min, 15min, 1h, or 4h") from exc
    if not isinstance(offset, Tick):
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use a fixed-duration offset such as 5min, 15min, 1h, or 4h")
    if offset.nanos <= 0:
        raise ValueError("timeframe must be positive")
    if offset.nanos < MIN_TIMEFRAME_NANOS:
        raise ValueError("timeframe must be at least 5min for this 5-minute source data")
    if offset.nanos % MIN_TIMEFRAME_NANOS != 0:
        raise ValueError("timeframe must be a multiple of the 5min source data")
    return offset


def _known_timeframe_key(timeframe: str) -> str:
    lowered = timeframe.lower()
    if timeframe in SEARCH_SPACES or timeframe in DEFAULT_RANGES or timeframe in FEE_HOLDING_DIVISOR:
        return timeframe
    if lowered in SEARCH_SPACES or lowered in DEFAULT_RANGES or lowered in FEE_HOLDING_DIVISOR:
        return lowered
    return timeframe


def default_range(timeframe: str) -> tuple[str, str]:
    timeframe_offset(timeframe)
    return DEFAULT_RANGES.get(_known_timeframe_key(timeframe), DEFAULT_RANGES[DEFAULT_SEARCH_SPACE_TIMEFRAME])


def fee_holding_divisor(timeframe: str) -> float:
    key = _known_timeframe_key(timeframe)
    if key in FEE_HOLDING_DIVISOR:
        return FEE_HOLDING_DIVISOR[key]
    return float(pd.Timedelta(hours=8).value / timeframe_offset(timeframe).nanos)


def search_space(timeframe: str) -> dict[str, list[float | int]]:
    timeframe_offset(timeframe)
    key = _known_timeframe_key(timeframe)
    return SEARCH_SPACES.get(key, SEARCH_SPACES[DEFAULT_SEARCH_SPACE_TIMEFRAME])


def parameter_grid(timeframe: str, limit: int | None = None) -> pd.DataFrame:
    space = search_space(timeframe)
    keys = list(space)
    values = itertools.product(*(space[key] for key in keys))
    if limit is not None:
        values = itertools.islice(values, limit)
    return pd.DataFrame(values, columns=keys)


def _inclusive_end(end: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(end)
    if len(end) == 10 and timestamp.time() == pd.Timestamp("00:00:00").time():
        return timestamp + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return timestamp


def load_bars(csv_path: Path, timeframe: str, start: str | None, end: str | None) -> pd.DataFrame:
    timeframe_offset(timeframe)
    bars = pd.read_csv(csv_path, parse_dates=["timestamp"])
    bars = bars.set_index("timestamp").resample(timeframe).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna().reset_index()
    if start:
        bars = bars[bars["timestamp"] >= pd.Timestamp(start)]
    if end:
        bars = bars[bars["timestamp"] <= _inclusive_end(end)]
    bars = bars.reset_index(drop=True)
    if bars.empty:
        raise ValueError("No bars after resampling/date filtering")
    return bars


def day_end_flags(timestamps: pd.Series) -> np.ndarray:
    dates = timestamps.dt.normalize().to_numpy()
    flags = np.zeros(len(timestamps), dtype=np.int8)
    if len(flags) == 0:
        return flags
    flags[:-1] = dates[:-1] != dates[1:]
    flags[-1] = 1
    return np.ascontiguousarray(flags)


def r_martin_monthly_from_closed_trades(
    timestamps: pd.Series,
    exit_indices: list[int] | np.ndarray,
    net_r_values: list[float] | np.ndarray,
) -> dict[str, float]:
    if len(timestamps) == 0:
        return {"r_per_month": math.nan, "ulcer_r": math.nan, "r_martin_monthly": math.nan}

    elapsed_days = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 86400
    duration_months = elapsed_days / MONTH_DAYS
    total_r = float(np.sum(np.asarray(net_r_values, dtype=np.float64)))
    r_per_month = total_r / duration_months if duration_months > 0 else math.nan

    if duration_months <= 0:
        return {"r_per_month": r_per_month, "ulcer_r": math.nan, "r_martin_monthly": math.nan}

    exits = np.asarray(exit_indices, dtype=np.int64)
    values = np.asarray(net_r_values, dtype=np.float64)
    if exits.size:
        order = np.argsort(exits, kind="stable")
        exits = exits[order]
        values = values[order]

    cumulative = 0.0
    peak = 0.0
    ulcer_sum = 0.0
    day_count = 0
    trade_pos = 0
    flags = day_end_flags(timestamps)
    for bar_idx, is_day_end in enumerate(flags):
        if not is_day_end:
            continue
        while trade_pos < exits.size and exits[trade_pos] <= bar_idx:
            cumulative += float(values[trade_pos])
            trade_pos += 1
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        ulcer_sum += drawdown * drawdown
        day_count += 1

    ulcer_r = math.sqrt(ulcer_sum / day_count) if day_count else math.nan
    r_martin = r_per_month / ulcer_r if ulcer_r > 0 and math.isfinite(r_per_month) else math.nan
    return {"r_per_month": r_per_month, "ulcer_r": ulcer_r, "r_martin_monthly": r_martin}


def r_sharpe_monthly_from_closed_trades(
    timestamps: pd.Series,
    exit_indices: list[int] | np.ndarray,
    net_r_values: list[float] | np.ndarray,
) -> float:
    if len(timestamps) == 0:
        return math.nan

    elapsed_days = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 86400
    if elapsed_days <= 0:
        return math.nan

    exits = np.asarray(exit_indices, dtype=np.int64)
    values = np.asarray(net_r_values, dtype=np.float64)
    if exits.size:
        order = np.argsort(exits, kind="stable")
        exits = exits[order]
        values = values[order]

    cumulative = 0.0
    previous_day_cumulative = 0.0
    daily_r_values: list[float] = []
    trade_pos = 0
    flags = day_end_flags(timestamps)
    for bar_idx, is_day_end in enumerate(flags):
        if not is_day_end:
            continue
        while trade_pos < exits.size and exits[trade_pos] <= bar_idx:
            cumulative += float(values[trade_pos])
            trade_pos += 1
        daily_r_values.append(cumulative - previous_day_cumulative)
        previous_day_cumulative = cumulative

    if len(daily_r_values) < 2:
        return math.nan
    daily_r = np.asarray(daily_r_values, dtype=np.float64)
    std = float(np.std(daily_r, ddof=1))
    if std <= 0 or not math.isfinite(std):
        return math.nan
    return float(np.mean(daily_r) / std * math.sqrt(MONTH_DAYS))


class _ScoreValidator(ast.NodeVisitor):
    allowed_nodes = (
        ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd,
        ast.FloorDiv,
    )

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, self.allowed_nodes):
            raise ValueError(f"Unsupported score expression node: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in SCORE_VARIABLES:
            raise ValueError(f"Unknown score variable {node.id!r}; allowed: {sorted(SCORE_VARIABLES)}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if type(node.value) not in (int, float):
            raise ValueError("Score constants must be numeric")


def compile_score_formula(formula: str):
    tree = ast.parse(formula, mode="eval")
    _ScoreValidator().visit(tree)
    return compile(tree, "<score-formula>", "eval")


def apply_score_formula(result: pd.DataFrame, formula: str) -> pd.Series:
    code = compile_score_formula(formula)
    local_vars = {name: result[name].to_numpy(dtype=np.float64) for name in SCORE_VARIABLES}
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        score = eval(code, {"__builtins__": {}}, local_vars)
    score = np.broadcast_to(score, (len(result),)).astype(np.float64).copy()
    score[~np.isfinite(score)] = np.nan
    return pd.Series(score, index=result.index)


def run_gpu_grid(bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str,
                 max_holding_bars: int = 288,
                 score_formula: str = DEFAULT_SCORE_FORMULA,
                 fee_bars: float | None = None) -> tuple[pd.DataFrame, dict]:
    if not cuda.is_available():
        raise RuntimeError("CUDA GPU is unavailable")
    timeframe_offset(timeframe)
    if grid.empty:
        raise ValueError("Parameter grid is empty")
    if max_holding_bars < 1:
        raise ValueError("max_holding_bars must be positive")
    if fee_bars is not None and fee_bars <= 0:
        raise ValueError("fee_bars must be positive")
    compile_score_formula(score_formula)
    if not np.isfinite(grid["boll_window_dev"].to_numpy(dtype=np.float64)).all():
        raise ValueError("boll_window_dev values must be finite")
    if not (grid["vol_fast"] < grid["vol_slow"]).all():
        raise ValueError("vol_fast must be shorter than vol_slow")
    if len(bars) <= max(grid["boll_window"].max(), grid["vol_slow"].max()):
        raise ValueError("Too few bars for the indicator windows")

    started = perf_counter()
    n = len(bars)
    close = np.ascontiguousarray(bars["close"].to_numpy(dtype=np.float32))
    high = np.ascontiguousarray(bars["high"].to_numpy(dtype=np.float32))
    low = np.ascontiguousarray(bars["low"].to_numpy(dtype=np.float32))
    elapsed_days = np.ascontiguousarray(
        ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() / 86400).to_numpy(dtype=np.float32)
    )
    day_ends = day_end_flags(bars["timestamp"])
    duration_months = np.float32(float(elapsed_days[-1]) / MONTH_DAYS)
    # Each distinct (window, deviation) has its own upper/lower band series.
    band_pairs = list(dict.fromkeys(
        (int(window), float(np.float32(deviation)))
        for window, deviation in zip(grid["boll_window"], grid["boll_window_dev"])
    ))
    band_windows = np.array([window for window, _ in band_pairs], dtype=np.int32)
    band_deviations = np.array([deviation for _, deviation in band_pairs], dtype=np.float32)
    # One ATR series per distinct period; the kernel divides fast by slow per combination.
    vol_windows = np.unique(np.concatenate([
        grid["vol_fast"].to_numpy(dtype=np.int32), grid["vol_slow"].to_numpy(dtype=np.int32)]))
    boll_map = {pair: idx for idx, pair in enumerate(band_pairs)}
    vol_map = {int(value): idx for idx, value in enumerate(vol_windows)}
    bw_idx = np.array([
        boll_map[(int(window), float(np.float32(deviation)))]
        for window, deviation in zip(grid["boll_window"], grid["boll_window_dev"])
    ], dtype=np.int32)
    fw_idx = np.array([vol_map[int(value)] for value in grid["vol_fast"]], dtype=np.int32)
    sw_idx = np.array([vol_map[int(value)] for value in grid["vol_slow"]], dtype=np.int32)

    d_close = cuda.to_device(close)
    d_high = cuda.to_device(high)
    d_low = cuda.to_device(low)
    d_elapsed_days = cuda.to_device(elapsed_days)
    d_day_ends = cuda.to_device(day_ends)
    d_boll_windows = cuda.to_device(band_windows)
    d_boll_deviations = cuda.to_device(band_deviations)
    d_vol_windows = cuda.to_device(vol_windows)
    d_upper = cuda.device_array((len(band_pairs), n), dtype=np.float32)
    d_lower = cuda.device_array((len(band_pairs), n), dtype=np.float32)
    d_vol = cuda.device_array((len(vol_windows), n), dtype=np.float32)

    threads = 128
    boll_tasks = len(band_pairs) * n
    _bollinger_windows[(boll_tasks + threads - 1) // threads, threads](
        d_close, d_boll_windows, d_boll_deviations, d_upper, d_lower,
    )
    _atr_windows[(len(vol_windows) + 31) // 32, 32](
        d_close, d_high, d_low, d_vol_windows, d_vol)
    cuda.synchronize()
    indicators_seconds = perf_counter() - started

    count = len(grid)
    output_perf = cuda.device_array(count, dtype=np.float32)
    output_max_dd = cuda.device_array(count, dtype=np.float32)
    output_avg_dd = cuda.device_array(count, dtype=np.float32)
    output_r2 = cuda.device_array(count, dtype=np.float32)
    output_count = cuda.device_array(count, dtype=np.int32)
    output_r_per_month = cuda.device_array(count, dtype=np.float32)
    output_ulcer_r = cuda.device_array(count, dtype=np.float32)
    output_r_martin = cuda.device_array(count, dtype=np.float32)
    output_r_sharpe = cuda.device_array(count, dtype=np.float32)
    _grid_kernel[(count + threads - 1) // threads, threads](
        d_close, d_high, d_low, d_elapsed_days, d_day_ends, duration_months,
        d_upper, d_lower, d_vol,
        cuda.to_device(bw_idx), cuda.to_device(fw_idx), cuda.to_device(sw_idx),
        cuda.to_device(grid["vol_threshold"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["sl_multiplier"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["tp_sl_ratio"].to_numpy(dtype=np.float32)),
        max_holding_bars, np.float32(fee_holding_divisor(timeframe) if fee_bars is None else fee_bars),
        output_perf, output_max_dd, output_avg_dd, output_r2, output_count,
        output_r_per_month, output_ulcer_r, output_r_martin, output_r_sharpe,
    )
    cuda.synchronize()

    result = grid.copy()
    result["performance"] = output_perf.copy_to_host()
    result["max_dd"] = output_max_dd.copy_to_host()
    result["avg_dd"] = output_avg_dd.copy_to_host()
    result["r2"] = output_r2.copy_to_host()
    result["number_of_trade"] = output_count.copy_to_host()
    result["r_per_month"] = output_r_per_month.copy_to_host()
    result["ulcer_r"] = output_ulcer_r.copy_to_host()
    result["r_martin_monthly"] = output_r_martin.copy_to_host()
    result["r_sharpe_monthly"] = output_r_sharpe.copy_to_host()
    result["score"] = apply_score_formula(result, score_formula)
    result["score_formula"] = score_formula
    timing = {
        "indicators_and_transfer_s": indicators_seconds,
        "total_gpu_s": perf_counter() - started,
    }
    return result, timing


def _cpu_bollinger(close: np.ndarray, windows: np.ndarray, deviation: float) -> tuple[np.ndarray, np.ndarray]:
    upper = np.full((len(windows), len(close)), np.nan, dtype=np.float32)
    lower = np.full_like(upper, np.nan)
    for w_idx, window in enumerate(windows):
        for i in range(int(window) - 1, len(close)):
            values = close[i - int(window) + 1:i + 1]
            mean = np.float32(values.sum(dtype=np.float32) / np.float32(window))
            squared = np.float32(0)
            for value in values:
                delta = np.float32(value - mean)
                squared = np.float32(squared + delta * delta)
            width = np.float32(deviation) * np.float32(math.sqrt(float(squared / np.float32(window))))
            upper[w_idx, i] = mean + width
            lower[w_idx, i] = mean - width
    return upper, lower


def _cpu_vol_ratio(bars: pd.DataFrame, fast: int, slow: int) -> np.ndarray:
    """atr_ratio in float64 from quantcrucible's indicator whitelist (reference values)."""
    from quantcrucible.core.strategy.registry import atr_ratio

    ohlc = SimpleNamespace(high=bars["high"].to_numpy(dtype=np.float64),
                           low=bars["low"].to_numpy(dtype=np.float64),
                           close=bars["close"].to_numpy(dtype=np.float64))
    return atr_ratio(ohlc, fast, slow)


def cpu_backtest_fp32(bars: pd.DataFrame, params: dict, timeframe: str,
                      max_holding_bars: int = 288,
                      fee_bars: float | None = None) -> dict:
    close = np.ascontiguousarray(bars["close"].to_numpy(dtype=np.float32))
    high = np.ascontiguousarray(bars["high"].to_numpy(dtype=np.float32))
    low = np.ascontiguousarray(bars["low"].to_numpy(dtype=np.float32))
    elapsed_days = np.ascontiguousarray(
        ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() / 86400).to_numpy(dtype=np.float32)
    )
    upper, lower = _cpu_bollinger(close, np.array([params["boll_window"]], dtype=np.int32), params["boll_window_dev"])
    vol_ratio = _cpu_vol_ratio(bars, int(params["vol_fast"]), int(params["vol_slow"]))

    cumulative = np.float32(0)
    peak = np.float32(0)
    max_dd = np.float32(0)
    dd_sum = np.float32(0)
    count = 0
    exit_indices: list[int] = []
    net_r_values: list[float] = []
    observations = np.float32(1)
    mean_x = mean_y = m2x = m2y = covariance = np.float32(0)
    i = 1
    n = len(close)
    while i < n - 1:
        if (close[i - 1] > lower[0, i] and close[i] < lower[0, i] and
                vol_ratio[i - 1] < params["vol_threshold"]):
            entry = close[i]
            risk = np.float32((upper[0, i] - entry) * np.float32(params["sl_multiplier"]))
            if entry > 0 and risk > 0:
                sl_price_ratio = np.float32(risk / entry)
                entry_fee = np.float32(0.001) / sl_price_ratio
                if entry_fee <= np.float32(0.2):
                    stop = np.float32(entry + risk)
                    target = np.float32(entry - np.float32(params["tp_sl_ratio"]) * risk)
                    j = i + 1
                    while j < n:
                        holding = j - i
                        if holding >= max_holding_bars:
                            trade_r = np.float32((entry - close[j]) / risk)
                            break
                        if high[j] > stop:
                            trade_r = np.float32(-1)
                            break
                        if low[j] < target:
                            trade_r = np.float32(params["tp_sl_ratio"])
                            break
                        if j == n - 1:
                            trade_r = np.float32((entry - close[j]) / risk)
                            break
                        j += 1
                    divisor = fee_holding_divisor(timeframe) if fee_bars is None else fee_bars
                    holding_fee = np.float32(0.0001) * (np.float32(holding) / np.float32(divisor)) / sl_price_ratio
                    net_r = np.float32(trade_r - entry_fee - holding_fee)
                    cumulative = np.float32(cumulative + net_r)
                    count += 1
                    exit_indices.append(j)
                    net_r_values.append(float(net_r))
                    if cumulative > peak:
                        peak = cumulative
                    current_dd = np.float32(peak - cumulative)
                    if current_dd > max_dd:
                        max_dd = current_dd
                    dd_sum = np.float32(dd_sum + current_dd)
                    x = elapsed_days[j]
                    observations = np.float32(observations + 1)
                    dx = np.float32(x - mean_x)
                    mean_x = np.float32(mean_x + dx / observations)
                    dy = np.float32(cumulative - mean_y)
                    mean_y = np.float32(mean_y + dy / observations)
                    m2x = np.float32(m2x + dx * (x - mean_x))
                    m2y = np.float32(m2y + dy * (cumulative - mean_y))
                    covariance = np.float32(covariance + dx * (cumulative - mean_y))
                    i = j + 1
                    continue
        i += 1

    dd_sum = np.float32(dd_sum + (peak - cumulative))
    x = elapsed_days[-1]
    observations = np.float32(observations + 1)
    dx = np.float32(x - mean_x)
    mean_x = np.float32(mean_x + dx / observations)
    dy = np.float32(cumulative - mean_y)
    mean_y = np.float32(mean_y + dy / observations)
    m2x = np.float32(m2x + dx * (x - mean_x))
    m2y = np.float32(m2y + dy * (cumulative - mean_y))
    covariance = np.float32(covariance + dx * (cumulative - mean_y))
    if m2y <= 0:
        r2 = np.float32(1)
    elif m2x <= 0:
        r2 = np.float32(0)
    else:
        r2 = np.float32(covariance * covariance / (m2x * m2y))
    r_martin_metrics = r_martin_monthly_from_closed_trades(
        bars["timestamp"], exit_indices, net_r_values,
    )
    r_sharpe_monthly = r_sharpe_monthly_from_closed_trades(
        bars["timestamp"], exit_indices, net_r_values,
    )
    return {
        "performance": float(cumulative),
        "max_dd": float(np.float32(math.floor(float(max_dd * 100 + np.float32(0.5))) / np.float32(100))),
        "avg_dd": float(np.float32(math.floor(float((dd_sum / observations) * 100 + np.float32(0.5))) / np.float32(100))),
        "r2": float(r2),
        "number_of_trade": count,
        "r_per_month": r_martin_metrics["r_per_month"],
        "ulcer_r": r_martin_metrics["ulcer_r"],
        "r_martin_monthly": r_martin_metrics["r_martin_monthly"],
        "r_sharpe_monthly": r_sharpe_monthly,
    }


def verify_samples(bars: pd.DataFrame, result: pd.DataFrame, timeframe: str,
                   sample_count: int, max_holding_bars: int,
                   score_formula: str, fee_bars: float | None) -> None:
    if sample_count < 1:
        return
    positions = np.unique(np.linspace(0, len(result) - 1, min(sample_count, len(result)), dtype=int))
    differences = {
        key: 0.0
        for key in [
            "performance", "max_dd", "avg_dd", "r2",
            "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly",
        ]
    }
    trade_count_differences = 0
    score_differences = 0.0
    rows = []
    for position in positions:
        row = result.iloc[position]
        params = {
            "boll_window": int(row["boll_window"]),
            "boll_window_dev": float(row["boll_window_dev"]),
            "vol_fast": int(row["vol_fast"]),
            "vol_slow": int(row["vol_slow"]),
            "vol_threshold": float(row["vol_threshold"]),
            "sl_multiplier": float(row["sl_multiplier"]),
            "tp_sl_ratio": float(row["tp_sl_ratio"]),
        }
        expected = cpu_backtest_fp32(bars, params, timeframe, max_holding_bars, fee_bars)
        trade_count_differences += int(int(row["number_of_trade"]) != expected["number_of_trade"])
        for key in differences:
            if np.isfinite(row[key]) or np.isfinite(expected[key]):
                differences[key] = max(differences[key], abs(float(row[key]) - expected[key]))
        expected_row = pd.DataFrame([{**params, **expected}])
        expected_score = float(apply_score_formula(expected_row, score_formula).iloc[0])
        if np.isfinite(row["score"]) or np.isfinite(expected_score):
            score_differences = max(score_differences, abs(float(row["score"]) - expected_score))
        rows.append(position)
    print(
        f"Compared {len(rows)} rows with CPU fp32: "
        f"{trade_count_differences} trade-count differences; "
        f"max performance diff={differences['performance']:.6g}; "
        f"max max_dd diff={differences['max_dd']:.6g}; "
        f"max avg_dd diff={differences['avg_dd']:.6g}; "
        f"max r2 diff={differences['r2']:.6g}; "
        f"max r_per_month diff={differences['r_per_month']:.6g}; "
        f"max ulcer_r diff={differences['ulcer_r']:.6g}; "
        f"max r_martin_monthly diff={differences['r_martin_monthly']:.6g}; "
        f"max r_sharpe_monthly diff={differences['r_sharpe_monthly']:.6g}; "
        f"max score diff={score_differences:.6g}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--timeframe", default="1h",
                        help="Pandas fixed timeframe, for example 5min, 15min, 1h, or 4h")
    parser.add_argument("--start", help="Inclusive start date; defaults to the source notebook's range")
    parser.add_argument("--end", help="Inclusive end date; defaults to the source notebook's range")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--limit", type=int, help="Only run the first N grid rows")
    parser.add_argument("--verify", type=int, default=0, metavar="N",
                        help="Compare N evenly spaced GPU rows against CPU fp32")
    parser.add_argument("--score-formula", default=DEFAULT_SCORE_FORMULA,
                        help=f"Arithmetic expression using {', '.join(sorted(SCORE_VARIABLES))}")
    parser.add_argument("--fee-bars", type=float,
                        help="Override holding-fee divisor; default is 8h divided by bar duration")
    parser.add_argument("--output", type=Path,
                        help="Output CSV path; defaults to a timeframe-specific file")
    args = parser.parse_args()

    default_start, default_end = default_range(args.timeframe)
    start = args.start or default_start
    end = args.end or default_end

    compile_score_formula(args.score_formula)
    bars = load_bars(args.csv, args.timeframe, start, end)
    grid = parameter_grid(args.timeframe, args.limit)
    result, timing = run_gpu_grid(
        bars, grid, args.timeframe, args.max_holding_bars, args.score_formula,
        args.fee_bars,
    )
    verify_samples(bars, result, args.timeframe, args.verify,
                   args.max_holding_bars, args.score_formula, args.fee_bars)
    output = args.output or Path(__file__).with_name(f"full_gpu_grid_results_fp32_{args.timeframe}.csv")
    result.to_csv(output, index=False)
    valid = result.dropna(subset=["score"])
    print(f"CSV: {args.csv}")
    print(f"Timeframe: {args.timeframe}; range: [{start}, {end}]; bars: {len(bars)}; parameter sets: {len(grid)}")
    print("Timing:", timing)
    print("Score formula:", args.score_formula)
    print(f"Saved: {output}")
    if not valid.empty:
        print("Best:", valid.loc[valid["score"].idxmax()].to_dict())


if __name__ == "__main__":
    main()
