"""AVAX long Bollinger/RSI grid search: indicators and replay on CUDA (fp32).

The training notebook allows one open position. Bollinger's opposite band sets
the stop distance; ATR is not used in this version. Timeframe can be any fixed
pandas offset at least 5min, such as 5min, 15min, 1h, or 4h. Run anywhere:

    python train_full_grid_gpu.py --timeframe 1h --verify 8
    python train_full_grid_gpu.py --timeframe 4h --score-formula "performance/max_dd*r2**5"
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


DEFAULT_CSV = Path(__file__).resolve().with_name("avaxusdt_futures_5min.csv")
DEFAULT_SCORE = "performance**2/number_of_trade/max_dd*r2**5"
SCORE_NAMES = frozenset({
    "performance", "max_dd", "avg_dd", "r2", "number_of_trade",
    "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly",
})
MONTH_DAYS = 365.2425 / 12
RAW_BAR_NANOS = pd.Timedelta(minutes=5).value
LEGACY_FEE_TIMEFRAMES = {"15min", "1h"}
SEARCH_SPACE = {
    "boll_window": [6, 7, 8, 10, 12, 14, 16, 18, 20, 25, 30],
    "boll_window_dev": [2.0],
    "rsi_window": [30, 35, 40, 45, 50, 60, 80],
    "rsi_threshold": list(range(50, 61)),
    "sl_multiplier": [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
    "tp_sl_ratio": [1.1],
}


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
    if fee_bars is not None:
        if fee_bars <= 0:
            raise ValueError("fee_bars must be positive")
        return float(fee_bars)
    timeframe_nanos(timeframe)
    if timeframe.lower() in LEGACY_FEE_TIMEFRAMES:
        return 8.0
    bar_hours = pd.Timedelta(timeframe_nanos(timeframe), unit="ns") / pd.Timedelta(hours=1)
    return float(8.0 / bar_hours)


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
    rsi[w_idx, 0] = zero
    for i in range(1, close.size):
        delta = close[i] - close[i - 1]
        up = delta if delta > zero else zero
        down = -delta if delta < zero else zero
        up_average = (one - alpha) * up_average + alpha * up
        down_average = (one - alpha) * down_average + alpha * down
        if i < window - 1:
            rsi[w_idx, i] = zero
        elif down_average == zero:
            rsi[w_idx, i] = hundred
        else:
            strength = up_average / down_average
            rsi[w_idx, i] = hundred - hundred / (one + strength)


@cuda.jit
def _grid_kernel(close, high, low, elapsed_days, upper, lower, rsi,
                 day_end_flags, elapsed_months,
                 boll_indices, rsi_indices, rsi_windows, thresholds,
                 multipliers, tp_ratios, max_holding_bars, fee_bars,
                 performances, max_drawdowns, avg_drawdowns, r2_values,
                 r_per_months, ulcer_rs, r_martin_monthlies,
                 r_sharpe_monthlies, trade_counts):
    combo = cuda.grid(1)
    if combo >= boll_indices.size:
        return
    bw = boll_indices[combo]
    rw = rsi_indices[combo]
    threshold = thresholds[combo]
    multiplier = multipliers[combo]
    tp_ratio = tp_ratios[combo]
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
    i = max(rsi_windows[rw], 1)
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
        band = upper[bw, i]
        if close[i - 1] < band and close[i] > band and rsi[rw, i] > threshold:
            entry = close[i]
            risk = (entry - lower[bw, i]) * multiplier
            if entry > zero and risk > zero:
                sl_price_ratio = risk / entry
                entry_fee = np.float32(0.001) / sl_price_ratio
                if entry_fee <= np.float32(0.2):
                    stop = entry - risk
                    target = entry + tp_ratio * risk
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
                            trade_r = -one
                            closed = True
                        elif high[j] >= target:
                            trade_r = tp_ratio
                            closed = True
                        elif j == n - 1:
                            trade_r = (close[j] - entry) / risk
                            closed = True

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


def parameter_grid(limit: int | None = None) -> pd.DataFrame:
    keys = list(SEARCH_SPACE)
    combos = itertools.product(*(SEARCH_SPACE[key] for key in keys))
    if limit is not None:
        combos = itertools.islice(combos, limit)
    return pd.DataFrame(combos, columns=keys)


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


def r_martin_monthly_from_closed_trades(
    timestamps: pd.Series,
    exit_indices: list[int] | np.ndarray,
    net_r_values: list[float] | np.ndarray,
) -> dict[str, float]:
    if len(timestamps) == 0:
        return {
            "r_per_month": math.nan,
            "ulcer_r": math.nan,
            "r_martin_monthly": math.nan,
            "r_sharpe_monthly": math.nan,
        }

    elapsed_days = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 86400
    duration_months = elapsed_days / MONTH_DAYS
    total_r = float(np.sum(np.asarray(net_r_values, dtype=np.float64)))
    r_per_month = total_r / duration_months if duration_months > 0 else math.nan
    if duration_months <= 0:
        return {
            "r_per_month": r_per_month,
            "ulcer_r": math.nan,
            "r_martin_monthly": math.nan,
            "r_sharpe_monthly": math.nan,
        }

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
    previous_day_cumulative = 0.0
    daily_r_mean = 0.0
    daily_r_m2 = 0.0
    trade_pos = 0
    flags = day_end_flags(pd.DataFrame({"timestamp": timestamps}))
    for bar_idx, is_day_end in enumerate(flags):
        if not is_day_end:
            continue
        while trade_pos < exits.size and exits[trade_pos] <= bar_idx:
            cumulative += float(values[trade_pos])
            trade_pos += 1
        day_count += 1
        daily_r = cumulative - previous_day_cumulative
        previous_day_cumulative = cumulative
        daily_delta = daily_r - daily_r_mean
        daily_r_mean += daily_delta / day_count
        daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        ulcer_sum += drawdown * drawdown

    ulcer_r = math.sqrt(ulcer_sum / day_count) if day_count else math.nan
    r_martin = r_per_month / ulcer_r if ulcer_r > 0 and math.isfinite(r_per_month) else math.nan
    if day_count >= 2 and daily_r_m2 > 0:
        daily_std = math.sqrt(daily_r_m2 / (day_count - 1))
        r_sharpe = daily_r_mean / daily_std * math.sqrt(MONTH_DAYS)
    else:
        r_sharpe = math.nan
    return {
        "r_per_month": r_per_month,
        "ulcer_r": ulcer_r,
        "r_martin_monthly": r_martin,
        "r_sharpe_monthly": r_sharpe,
    }


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


def run_gpu_grid(bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str,
                 max_holding_bars: int = 288, fee_bars: float | None = None,
                 score_formula: str = DEFAULT_SCORE) -> tuple[pd.DataFrame, dict]:
    if not cuda.is_available():
        raise RuntimeError("CUDA GPU is unavailable")
    timeframe_nanos(timeframe)
    resolved_fee_bars = resolve_fee_bars(timeframe, fee_bars)
    if grid.empty or max_holding_bars < 1:
        raise ValueError("Grid must be nonempty; max_holding_bars must be positive")
    if not np.isfinite(grid["boll_window_dev"].to_numpy(dtype=np.float64)).all():
        raise ValueError("boll_window_dev values must be finite")
    if len(bars) <= max(grid["boll_window"].max(), grid["rsi_window"].max()):
        raise ValueError("Too few bars for the chosen indicator windows")
    # Validate the expression before launching any CUDA work.
    score_result(pd.DataFrame({name: [1.0] for name in SCORE_NAMES}), score_formula)

    started = perf_counter()
    n = len(bars)
    close = np.ascontiguousarray(bars["close"].to_numpy(dtype=np.float32))
    high = np.ascontiguousarray(bars["high"].to_numpy(dtype=np.float32))
    low = np.ascontiguousarray(bars["low"].to_numpy(dtype=np.float32))
    elapsed_days = np.ascontiguousarray(
        ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() /
         86400).to_numpy(dtype=np.float32))
    elapsed_months = np.float32(float(elapsed_days[-1]) / MONTH_DAYS)
    end_flags = day_end_flags(bars)
    band_pairs = list(dict.fromkeys(
        (int(window), float(np.float32(deviation)))
        for window, deviation in zip(grid["boll_window"], grid["boll_window_dev"])
    ))
    band_windows = np.array([window for window, _ in band_pairs], dtype=np.int32)
    band_deviations = np.array([deviation for _, deviation in band_pairs], dtype=np.float32)
    rsi_windows = np.unique(grid["rsi_window"].to_numpy(dtype=np.int32))
    boll_map = {pair: i for i, pair in enumerate(band_pairs)}
    rsi_map = {int(window): i for i, window in enumerate(rsi_windows)}
    boll_indices = np.array([
        boll_map[(int(window), float(np.float32(deviation)))]
        for window, deviation in zip(grid["boll_window"], grid["boll_window_dev"])
    ], dtype=np.int32)
    rsi_indices = np.array([rsi_map[int(w)] for w in grid["rsi_window"]], dtype=np.int32)

    d_close = cuda.to_device(close)
    d_high = cuda.to_device(high)
    d_low = cuda.to_device(low)
    d_days = cuda.to_device(elapsed_days)
    d_day_end_flags = cuda.to_device(end_flags)
    d_boll_windows = cuda.to_device(band_windows)
    d_boll_deviations = cuda.to_device(band_deviations)
    d_rsi_windows = cuda.to_device(rsi_windows)
    shape = (len(band_pairs), n)
    d_upper = cuda.device_array(shape, dtype=np.float32)
    d_lower = cuda.device_array(shape, dtype=np.float32)
    d_rsi = cuda.device_array((len(rsi_windows), n), dtype=np.float32)
    threads = 128
    boll_tasks = len(band_pairs) * n
    _bollinger_bands[(boll_tasks + threads - 1) // threads, threads](
        d_close, d_boll_windows, d_boll_deviations,
        d_upper, d_lower)
    _rsi_windows[1, 32](d_close, d_rsi_windows, d_rsi)
    cuda.synchronize()
    indicator_seconds = perf_counter() - started

    count = len(grid)
    outputs = [cuda.device_array(count, dtype=np.float32) for _ in range(8)]
    d_trade_count = cuda.device_array(count, dtype=np.int32)
    _grid_kernel[(count + threads - 1) // threads, threads](
        d_close, d_high, d_low, d_days, d_upper, d_lower, d_rsi,
        d_day_end_flags, elapsed_months,
        cuda.to_device(boll_indices), cuda.to_device(rsi_indices), d_rsi_windows,
        cuda.to_device(grid["rsi_threshold"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["sl_multiplier"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["tp_sl_ratio"].to_numpy(dtype=np.float32)),
        max_holding_bars, np.float32(resolved_fee_bars),
        outputs[0], outputs[1], outputs[2], outputs[3],
        outputs[4], outputs[5], outputs[6], outputs[7], d_trade_count)
    cuda.synchronize()
    result = grid.copy()
    for column, output in zip(
        ("performance", "max_dd", "avg_dd", "r2",
         "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly"),
        outputs,
    ):
        result[column] = output.copy_to_host()
    result["number_of_trade"] = d_trade_count.copy_to_host()
    result = score_result(result, score_formula)
    return result, {"indicators_and_transfer_s": indicator_seconds,
                    "holding_fee_bars": resolved_fee_bars,
                    "total_gpu_s": perf_counter() - started}


def verify_samples(bars: pd.DataFrame, result: pd.DataFrame,
                   sample_count: int, max_holding_bars: int, fee_bars: float) -> None:
    """Compare selected fp32 outputs with the notebook's float64 CPU logic."""
    if sample_count < 1:
        return
    from ta.momentum import RSIIndicator
    from ta.volatility import BollingerBands

    indices = np.unique(np.linspace(0, len(result) - 1,
                                    min(sample_count, len(result)), dtype=int))
    differences = 0
    largest_performance_difference = 0.0
    largest_r_per_month_difference = 0.0
    largest_ulcer_difference = 0.0
    largest_martin_difference = 0.0
    largest_sharpe_difference = 0.0
    close = bars["close"].to_numpy()
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    flags = day_end_flags(bars)
    elapsed_days = ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() /
                    86400).to_numpy(dtype=np.float64)
    elapsed_months = elapsed_days[-1] / MONTH_DAYS
    for index in indices:
        row = result.iloc[index]
        band = BollingerBands(bars["close"], int(row.boll_window),
                              float(row.boll_window_dev))
        upper = band.bollinger_hband().to_numpy()
        lower = band.bollinger_lband().to_numpy()
        rsi = RSIIndicator(bars["close"], int(row.rsi_window)).rsi().to_numpy()
        cumulative = 0.0
        trades = 0
        i = int(row.rsi_window)
        daily_peak = 0.0
        ulcer_sum = 0.0
        daily_observations = 0
        last_daily_index = -1
        previous_day_cumulative = 0.0
        daily_r_mean = 0.0
        daily_r_m2 = 0.0
        for k in range(i):
            if flags[k]:
                daily_r = cumulative - previous_day_cumulative
                previous_day_cumulative = cumulative
                daily_observations += 1
                daily_delta = daily_r - daily_r_mean
                daily_r_mean += daily_delta / daily_observations
                daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                ulcer_sum += (daily_peak - cumulative) ** 2
                last_daily_index = k
        while i < len(bars) - 1:
            opened = False
            if (close[i - 1] < upper[i] and close[i] > upper[i] and
                    rsi[i] > row.rsi_threshold):
                entry = close[i]
                risk = (entry - lower[i]) * row.sl_multiplier
                if entry > 0 and risk > 0:
                    sl_ratio = risk / entry
                    entry_fee = 0.001 / sl_ratio
                    if entry_fee <= 0.2:
                        stop = entry - risk
                        target = entry + row.tp_sl_ratio * risk
                        if flags[i]:
                            daily_r = cumulative - previous_day_cumulative
                            previous_day_cumulative = cumulative
                            daily_observations += 1
                            daily_delta = daily_r - daily_r_mean
                            daily_r_mean += daily_delta / daily_observations
                            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                            daily_peak = max(daily_peak, cumulative)
                            ulcer_sum += (daily_peak - cumulative) ** 2
                            last_daily_index = i
                        j = i + 1
                        while j < len(bars):
                            holding = j - i
                            closed = False
                            if holding >= max_holding_bars:
                                trade_r = (close[j] - entry) / risk
                                closed = True
                            elif low[j] <= stop:
                                trade_r = -1.0
                                closed = True
                            elif high[j] >= target:
                                trade_r = row.tp_sl_ratio
                                closed = True
                            elif j == len(bars) - 1:
                                trade_r = (close[j] - entry) / risk
                                closed = True
                            if closed:
                                holding_fee = 0.0001 * (holding / fee_bars) / sl_ratio
                                cumulative += trade_r - entry_fee - holding_fee
                                trades += 1
                                if flags[j]:
                                    daily_r = cumulative - previous_day_cumulative
                                    previous_day_cumulative = cumulative
                                    daily_observations += 1
                                    daily_delta = daily_r - daily_r_mean
                                    daily_r_mean += daily_delta / daily_observations
                                    daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                                    daily_peak = max(daily_peak, cumulative)
                                    ulcer_sum += (daily_peak - cumulative) ** 2
                                    last_daily_index = j
                                i = j + 1
                                opened = True
                                break
                            if flags[j]:
                                daily_r = cumulative - previous_day_cumulative
                                previous_day_cumulative = cumulative
                                daily_observations += 1
                                daily_delta = daily_r - daily_r_mean
                                daily_r_mean += daily_delta / daily_observations
                                daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                                daily_peak = max(daily_peak, cumulative)
                                ulcer_sum += (daily_peak - cumulative) ** 2
                                last_daily_index = j
                            j += 1
                        if opened:
                            continue
            if flags[i]:
                daily_r = cumulative - previous_day_cumulative
                previous_day_cumulative = cumulative
                daily_observations += 1
                daily_delta = daily_r - daily_r_mean
                daily_r_mean += daily_delta / daily_observations
                daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
                daily_peak = max(daily_peak, cumulative)
                ulcer_sum += (daily_peak - cumulative) ** 2
                last_daily_index = i
            if opened:
                        continue
            i += 1
        if last_daily_index != len(bars) - 1:
            daily_r = cumulative - previous_day_cumulative
            previous_day_cumulative = cumulative
            daily_observations += 1
            daily_delta = daily_r - daily_r_mean
            daily_r_mean += daily_delta / daily_observations
            daily_r_m2 += daily_delta * (daily_r - daily_r_mean)
            daily_peak = max(daily_peak, cumulative)
            ulcer_sum += (daily_peak - cumulative) ** 2
        r_per_month = cumulative / elapsed_months if elapsed_months > 0 else math.nan
        ulcer_r = math.sqrt(ulcer_sum / daily_observations) if daily_observations else math.nan
        r_martin_monthly = r_per_month / ulcer_r if elapsed_months > 0 and ulcer_r > 0 else math.nan
        if elapsed_months > 0 and daily_observations >= 2 and daily_r_m2 > 0:
            daily_std = math.sqrt(daily_r_m2 / (daily_observations - 1))
            r_sharpe_monthly = daily_r_mean / daily_std * math.sqrt(MONTH_DAYS)
        else:
            r_sharpe_monthly = math.nan
        differences += int(trades != row.number_of_trade)
        largest_performance_difference = max(largest_performance_difference,
                                              abs(cumulative - row.performance))
        largest_r_per_month_difference = max(
            largest_r_per_month_difference, abs(r_per_month - row.r_per_month))
        largest_ulcer_difference = max(largest_ulcer_difference,
                                       abs(ulcer_r - row.ulcer_r))
        if np.isfinite(r_martin_monthly) or np.isfinite(row.r_martin_monthly):
            largest_martin_difference = max(
                largest_martin_difference,
                abs(r_martin_monthly - row.r_martin_monthly))
        if np.isfinite(r_sharpe_monthly) or np.isfinite(row.r_sharpe_monthly):
            largest_sharpe_difference = max(
                largest_sharpe_difference,
                abs(r_sharpe_monthly - row.r_sharpe_monthly))
    print(f"Compared {len(indices)} rows with float64 CPU: "
          f"{differences} trade-count differences; "
          f"max performance difference={largest_performance_difference:.6g}; "
          f"max r_per_month difference={largest_r_per_month_difference:.6g}; "
          f"max ulcer_r difference={largest_ulcer_difference:.6g}; "
          f"max r_martin_monthly difference={largest_martin_difference:.6g}; "
          f"max r_sharpe_monthly difference={largest_sharpe_difference:.6g}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--timeframe", default="1h",
                        help="Fixed pandas timeframe at least 5min, for example 5min, 15min, 1h, or 4h")
    parser.add_argument("--start", default="2023-07-01")
    parser.add_argument("--end", default="2025-01-01")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--fee-bars", type=float,
                        help="Holding-fee divisor in bars; default keeps 8 for 1h/15min and uses 8 hours per fee for other fixed timeframes")
    parser.add_argument("--limit", type=int, help="Run only the first N combinations")
    parser.add_argument("--verify", type=int, default=0, metavar="N",
                        help="Report differences for N rows against float64 CPU")
    parser.add_argument("--score-formula", default=DEFAULT_SCORE,
                        help="Arithmetic over: " + ",".join(sorted(SCORE_NAMES)))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    resolved_fee_bars = resolve_fee_bars(args.timeframe, args.fee_bars)
    bars = load_bars(args.csv, args.timeframe, args.start, args.end)
    grid = parameter_grid(args.limit)
    result, timing = run_gpu_grid(bars, grid, args.timeframe,
                                  args.max_holding_bars, resolved_fee_bars,
                                  args.score_formula)
    verify_samples(bars, result, args.verify,
                   args.max_holding_bars, resolved_fee_bars)
    output = args.output or Path(__file__).with_name(
        f"full_gpu_grid_results_{args.timeframe}_fp32.csv")
    result.to_csv(output, index=False)
    valid = result.dropna(subset=["score"])
    print(f"Bars: {len(bars)}; parameter sets: {len(grid)}")
    print("Timing:", timing)
    print("Holding fee bars:", resolved_fee_bars)
    print("Score formula:", args.score_formula)
    print("Saved:", output)
    if not valid.empty:
        print("Best:", valid.loc[valid["score"].idxmax()].to_dict())


if __name__ == "__main__":
    main()
