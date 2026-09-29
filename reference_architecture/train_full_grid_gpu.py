"""Batched CUDA grid search for the one-position Bollinger/RSI strategy.

RSI, ATR, and Bollinger upper bands are calculated on CUDA in float32 for their
unique windows. Each Bollinger window/bar pair gets a CUDA lane to avoid
float32 drift from long rolling updates. One
CUDA thread then handles one parameter combination, including entry detection,
SL/TP/timeout checks, sequential position selection, fees, drawdown and R².
The entire grid is one kernel launch; no joblib workers compete for the GPU.

Examples (from the crypto directory):
    python "new2/ADA/long_bollinger+RSI max_dd+r2/train_full_grid_gpu.py" --limit 256 --verify 12
    python "new2/ADA/long_bollinger+RSI max_dd+r2/train_full_grid_gpu.py" --output gpu_full_grid.csv
"""

from __future__ import annotations

import argparse
import itertools
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from numba import cuda

from train_candidate_exits_gpu import (DEFAULT_CSV, SEARCH_SPACE, backtest,
                                       load_bars)


@cuda.jit
def _bollinger_upper_windows(close, windows, deviation, upper):
    """Compute the upper band from a fresh float32 window on each CUDA lane."""
    task = cuda.grid(1)
    if task >= windows.size * close.size:
        return
    w_idx = task // close.size
    i = task % close.size
    window = windows[w_idx]
    if i < window - 1:
        upper[w_idx, i] = math.nan
        return
    total = np.float32(0)
    for j in range(i - window + 1, i + 1):
        value = close[j]
        if value != value:
            upper[w_idx, i] = math.nan
            return
        total += value
    mean = total / np.float32(window)
    squared_deviations = np.float32(0)
    for j in range(i - window + 1, i + 1):
        delta = close[j] - mean
        squared_deviations += delta * delta
    variance = squared_deviations / np.float32(window)
    upper[w_idx, i] = mean + deviation * np.float32(math.sqrt(variance))


@cuda.jit
def _rsi_windows(close32, windows, rsi):
    w_idx = cuda.grid(1)
    if w_idx >= windows.size:
        return
    window = windows[w_idx]
    one = np.float32(1)
    zero = np.float32(0)
    hundred = np.float32(100)
    alpha = one / np.float32(window)
    up_average = zero
    down_average = zero
    rsi[w_idx, 0] = zero
    for i in range(1, close32.size):
        delta = close32[i] - close32[i - 1]
        up = delta if delta > zero else zero
        down = -delta if delta < zero else zero
        up_average = (one - alpha) * up_average + alpha * up
        down_average = (one - alpha) * down_average + alpha * down
        if i < window - 1:
            rsi[w_idx, i] = zero
        elif down_average == zero:
            rsi[w_idx, i] = hundred
        else:
            relative_strength = up_average / down_average
            rsi[w_idx, i] = hundred - hundred / (one + relative_strength)


@cuda.jit
def _atr_windows(high32, low32, close32, windows, atr):
    w_idx = cuda.grid(1)
    if w_idx >= windows.size:
        return
    window = windows[w_idx]
    initial_sum = np.float32(0)
    for i in range(window):
        tr = high32[i] - low32[i]
        if i > 0:
            high_gap = abs(high32[i] - close32[i - 1])
            low_gap = abs(low32[i] - close32[i - 1])
            tr = max(tr, high_gap, low_gap)
        initial_sum += tr
        atr[w_idx, i] = np.float32(0)
    previous = initial_sum / np.float32(window)
    atr[w_idx, window - 1] = previous
    for i in range(window, close32.size):
        tr = max(high32[i] - low32[i],
                 abs(high32[i] - close32[i - 1]),
                 abs(low32[i] - close32[i - 1]))
        previous = (previous * np.float32(window - 1) + tr) / np.float32(window)
        atr[w_idx, i] = previous


@cuda.jit
def _grid_kernel(close, high, low, elapsed_days, upper, rsi, atr,
                 boll_indices, rsi_indices, rsi_windows, thresholds,
                 multipliers, tp_ratios, max_holding_bars,
                 performances, drawdowns, r2_values, trade_counts, scores):
    combo = cuda.grid(1)
    if combo >= boll_indices.size:
        return

    bw = boll_indices[combo]
    rw = rsi_indices[combo]
    ratio = tp_ratios[combo]
    multiplier = multipliers[combo]
    threshold = thresholds[combo]
    n = close.size
    i = max(rsi_windows[rw], 1)
    zero = np.float32(0)
    one = np.float32(1)
    cumulative = zero
    peak = zero
    max_dd = zero
    count = 0

    # Centered online covariance avoids subtracting large float32 time sums.
    observations = one
    mean_x = zero
    mean_y = zero
    m2x = zero
    m2y = zero
    covariance = zero

    while i < n - 1:
        band = upper[bw, i]
        if close[i - 1] < band and close[i] > band and rsi[rw, i] > threshold:
            entry = close[i]
            stop = entry - atr[bw, i] * multiplier
            risk = entry - stop
            if entry > zero and risk > zero:
                sl_price_ratio = risk / entry
                entry_fee = np.float32(0.001) / sl_price_ratio
                if entry_fee <= np.float32(0.2):
                    target = entry + ratio * risk
                    j = i + 1
                    while j < n:
                        holding = j - i
                        if holding >= max_holding_bars:
                            trade_r = (close[j] - entry) / risk
                            break
                        if low[j] <= stop:
                            trade_r = -one
                            break
                        if high[j] >= target:
                            trade_r = ratio
                            break
                        if j == n - 1:
                            trade_r = (close[j] - entry) / risk
                            break
                        j += 1

                    holding_fee = np.float32(0.0001) * (np.float32(holding) / np.float32(32)) / sl_price_ratio
                    cumulative += trade_r - entry_fee - holding_fee
                    count += 1
                    peak = max(peak, cumulative)
                    max_dd = max(max_dd, peak - cumulative)
                    x = elapsed_days[j]
                    observations += one
                    dx = x - mean_x
                    mean_x += dx / observations
                    dy = cumulative - mean_y
                    mean_y += dy / observations
                    m2x += dx * (x - mean_x)
                    m2y += dy * (cumulative - mean_y)
                    covariance += dx * (cumulative - mean_y)
                    i = j + 1  # No new entry on the exit bar.
                    continue
        i += 1

    # Last dummy trade at the final bar, preserving the notebook's R² method.
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

    rounded_dd = np.float32(math.floor(max_dd * np.float32(100) + np.float32(0.5))) / np.float32(100)
    performances[combo] = cumulative
    drawdowns[combo] = rounded_dd
    r2_values[combo] = r_squared
    trade_counts[combo] = count
    if count > 0 and rounded_dd > zero:
        scores[combo] = cumulative / rounded_dd * r_squared**5
    else:
        scores[combo] = math.nan


def parameter_grid(limit: int | None = None) -> pd.DataFrame:
    keys = list(SEARCH_SPACE)
    values = itertools.product(*(SEARCH_SPACE[key] for key in keys))
    if limit is not None:
        values = itertools.islice(values, limit)
    return pd.DataFrame(values, columns=keys)


def run_gpu_grid(bars: pd.DataFrame, grid: pd.DataFrame,
                 max_holding_bars: int = 288) -> tuple[pd.DataFrame, dict]:
    if not cuda.is_available():
        raise RuntimeError("CUDA GPU is unavailable")
    if grid.empty:
        raise ValueError("Parameter grid is empty")
    if max_holding_bars < 1:
        raise ValueError("max_holding_bars must be positive")
    if len(bars) < max(max(SEARCH_SPACE["boll_window"]), max(SEARCH_SPACE["rsi_window"])):
        raise ValueError("Too few bars for the indicator windows")
    if grid["boll_window_dev"].nunique() != 1:
        raise ValueError("This GPU kernel expects one Bollinger deviation per batch")

    started = perf_counter()
    n = len(bars)
    close = np.ascontiguousarray(bars["close"].to_numpy(dtype=np.float32))
    high = np.ascontiguousarray(bars["high"].to_numpy(dtype=np.float32))
    low = np.ascontiguousarray(bars["low"].to_numpy(dtype=np.float32))
    elapsed_days = np.ascontiguousarray(
        ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() / 86400).to_numpy(dtype=np.float32)
    )
    boll_windows = np.unique(grid["boll_window"].to_numpy(dtype=np.int32))
    rsi_windows = np.unique(grid["rsi_window"].to_numpy(dtype=np.int32))
    boll_map = {int(v): i for i, v in enumerate(boll_windows)}
    rsi_map = {int(v): i for i, v in enumerate(rsi_windows)}
    bw_idx = np.array([boll_map[int(v)] for v in grid["boll_window"]], dtype=np.int32)
    rw_idx = np.array([rsi_map[int(v)] for v in grid["rsi_window"]], dtype=np.int32)

    d_close, d_high, d_low = cuda.to_device(close), cuda.to_device(high), cuda.to_device(low)
    d_elapsed_days = cuda.to_device(elapsed_days)
    d_boll_windows = cuda.to_device(boll_windows)
    d_rsi_windows = cuda.to_device(rsi_windows)
    d_upper = cuda.device_array((len(boll_windows), n), dtype=np.float32)
    d_rsi = cuda.device_array((len(rsi_windows), n), dtype=np.float32)
    d_atr = cuda.device_array((len(boll_windows), n), dtype=np.float32)

    threads = 128
    boll_tasks = len(boll_windows) * n
    _bollinger_upper_windows[(boll_tasks + threads - 1) // threads, threads](
        d_close, d_boll_windows, np.float32(grid["boll_window_dev"].iloc[0]), d_upper)
    _rsi_windows[1, 32](d_close, d_rsi_windows, d_rsi)
    _atr_windows[1, 32](d_high, d_low, d_close, d_boll_windows, d_atr)
    cuda.synchronize()
    indicators_seconds = perf_counter() - started

    count = len(grid)
    output_perf = cuda.device_array(count, dtype=np.float32)
    output_dd = cuda.device_array(count, dtype=np.float32)
    output_r2 = cuda.device_array(count, dtype=np.float32)
    output_count = cuda.device_array(count, dtype=np.int32)
    output_score = cuda.device_array(count, dtype=np.float32)
    _grid_kernel[(count + threads - 1) // threads, threads](
        d_close, d_high, d_low, d_elapsed_days, d_upper, d_rsi, d_atr,
        cuda.to_device(bw_idx), cuda.to_device(rw_idx), d_rsi_windows,
        cuda.to_device(grid["rsi_threshold"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["atr_multiplier"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["tp_sl_ratio"].to_numpy(dtype=np.float32)),
        max_holding_bars, output_perf, output_dd, output_r2,
        output_count, output_score,
    )
    cuda.synchronize()
    result = grid.copy()
    result["performance"] = output_perf.copy_to_host()
    result["max_dd"] = output_dd.copy_to_host()
    result["r2"] = output_r2.copy_to_host()
    result["number_of_trade"] = output_count.copy_to_host()
    result["score"] = output_score.copy_to_host()
    timing = {"indicators_and_transfer_s": indicators_seconds,
              "total_gpu_s": perf_counter() - started}
    return result, timing


def verify_samples(bars: pd.DataFrame, result: pd.DataFrame,
                   sample_count: int, max_holding_bars: int) -> None:
    """Report expected fp32 differences against the float64 CPU strategy."""
    if sample_count < 1:
        return
    positions = np.unique(np.linspace(0, len(result) - 1,
                                      min(sample_count, len(result)), dtype=int))
    trade_count_differences = 0
    largest_performance_difference = 0.0
    largest_score_difference = 0.0
    for position in positions:
        row = result.iloc[position]
        params = {key: (int(row[key]) if key in {"boll_window", "rsi_window", "rsi_threshold"}
                        else float(row[key])) for key in SEARCH_SPACE}
        _, expected, _, _ = backtest(bars, params, "cpu", max_holding_bars, False)
        trade_count_differences += int(row["number_of_trade"] != expected["number_of_trade"])
        largest_performance_difference = max(
            largest_performance_difference,
            abs(row["performance"] - expected["performance"]),
        )
        if np.isfinite(row["score"]) and np.isfinite(expected["score"]):
            largest_score_difference = max(
                largest_score_difference, abs(row["score"] - expected["score"]),
            )
    print(f"Compared {len(positions)} rows with float64 CPU: "
          f"{trade_count_differences} trade-count differences; "
          f"max performance difference={largest_performance_difference:.6g}; "
          f"max score difference={largest_score_difference:.6g}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--limit", type=int, help="Only run the first N grid rows")
    parser.add_argument("--verify", type=int, default=0, metavar="N",
                        help="Report differences for N evenly spaced rows against float64 CPU")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).with_name("full_gpu_grid_results_fp32.csv"))
    args = parser.parse_args()

    bars = load_bars(args.csv, args.timeframe)
    grid = parameter_grid(args.limit)
    result, timing = run_gpu_grid(bars, grid, args.max_holding_bars)
    verify_samples(bars, result, args.verify, args.max_holding_bars)
    result.to_csv(args.output, index=False)
    valid = result.dropna(subset=["score"])
    print(f"Bars: {len(bars)}; parameter sets: {len(grid)}")
    print("Timing:", timing)
    print(f"Saved: {args.output}")
    if not valid.empty:
        print("Best:", valid.loc[valid["score"].idxmax()].to_dict())


if __name__ == "__main__":
    main()
