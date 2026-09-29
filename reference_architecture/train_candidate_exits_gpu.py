"""Backtest Bollinger + RSI entries with batched GPU exit checks.

Each candidate entry is independent while its first exit is resolved. A final
CPU pass keeps only non-overlapping trades to match the original one-position
strategy. Use --allow-overlap to keep every candidate instead.

Examples:
    python train_candidate_exits_gpu.py --backend gpu --verify
    python train_candidate_exits_gpu.py --backend gpu --grid --limit 20
"""

from __future__ import annotations

import argparse
import itertools
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange, BollingerBands

try:
    from numba import cuda
except ImportError:
    cuda = None


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = ROOT / "adausdt_futures_5min.csv"
SEARCH_SPACE = {
    "boll_window": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 20, 25],
    "boll_window_dev": [2.0],
    "rsi_window": [40, 50, 60, 80, 100],
    "rsi_threshold": [50, 51, 52, 53, 54, 55, 56, 57],
    "atr_multiplier": [1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.4, 2.6, 2.8, 3.0],
    "tp_sl_ratio": [1.1, 1.5, 2.0],
}
DEFAULT_PARAMS = {
    "boll_window": 6,
    "boll_window_dev": 2.0,
    "rsi_window": 80,
    "rsi_threshold": 51,
    "atr_multiplier": 3.0,
    "tp_sl_ratio": 2.0,
}

# At each future bar, timeout wins at max_holding_bars; otherwise SL wins
# over TP if both prices are touched. This matches the existing notebook.
STOP, TARGET, TIMEOUT, DATA_END = 1, 2, 3, 4
REASONS = {STOP: "sl", TARGET: "tp", TIMEOUT: "timeout", DATA_END: "data_end"}


if cuda is not None:
    @cuda.jit
    def _exit_kernel(low, high, close, entries, entry_prices, stop_prices,
                     target_prices, max_holding_bars, tp_sl_ratio,
                     exit_indices, reasons, realized_ratios):
        k = cuda.grid(1)
        if k >= entries.size:
            return

        start = entries[k]
        last = close.size - 1
        end = min(start + max_holding_bars, last)
        risk = entry_prices[k] - stop_prices[k]

        for bar in range(start + 1, end + 1):
            if bar - start >= max_holding_bars:
                exit_indices[k] = bar
                reasons[k] = TIMEOUT
                realized_ratios[k] = (close[bar] - entry_prices[k]) / risk
                return
            if low[bar] <= stop_prices[k]:
                exit_indices[k] = bar
                reasons[k] = STOP
                realized_ratios[k] = tp_sl_ratio
                return
            if high[bar] >= target_prices[k]:
                exit_indices[k] = bar
                reasons[k] = TARGET
                realized_ratios[k] = tp_sl_ratio
                return

        exit_indices[k] = end
        reasons[k] = DATA_END
        realized_ratios[k] = (close[end] - entry_prices[k]) / risk


def load_bars(csv_path: Path, timeframe: str) -> pd.DataFrame:
    bars = pd.read_csv(csv_path, parse_dates=["timestamp"])
    bars = bars.set_index("timestamp").resample(timeframe).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()
    if bars.empty:
        raise ValueError("No bars after resampling")
    return bars


def find_candidates(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    close = bars["close"]
    upper = BollingerBands(
        close=close, window=params["boll_window"],
        window_dev=params["boll_window_dev"],
    ).bollinger_hband()
    # The source notebook casts OHLC to float32 before calculating RSI/ATR.
    # Keep that detail so entries and stop prices agree with its CPU path.
    close32 = pd.Series(close.to_numpy(dtype=np.float32))
    high32 = pd.Series(bars["high"].to_numpy(dtype=np.float32))
    low32 = pd.Series(bars["low"].to_numpy(dtype=np.float32))
    rsi = RSIIndicator(close=close32, window=params["rsi_window"]).rsi()
    atr = AverageTrueRange(
        high=high32, low=low32, close=close32,
        window=params["boll_window"],
    ).average_true_range()

    entry = close.to_numpy(dtype=np.float64)
    stop = entry - atr.to_numpy(dtype=np.float64) * params["atr_multiplier"]
    risk = entry - stop
    sl_ratio = np.divide(risk, entry, out=np.zeros_like(risk), where=entry != 0)
    fee_ratio = np.divide(
        0.001, sl_ratio, out=np.full_like(sl_ratio, np.inf), where=sl_ratio > 0,
    )
    indices = np.arange(len(bars))
    valid = (
        (close.shift(1) < upper)
        & (close > upper)
        & (rsi > params["rsi_threshold"])
        & (indices >= params["rsi_window"])
        & (indices < len(bars) - 1)
        & (fee_ratio <= 0.2)
    )
    selected = np.flatnonzero(valid.to_numpy())
    return pd.DataFrame({
        "entry_index": selected.astype(np.int32),
        "entry_price": entry[selected],
        "stop_loss": stop[selected],
        "take_profit": entry[selected] + params["tp_sl_ratio"] * risk[selected],
        "sl_price_ratio": sl_ratio[selected],
        "entry_fee_risk_ratio": fee_ratio[selected],
    })


def _cpu_exits(bars: pd.DataFrame, candidates: pd.DataFrame,
               max_holding_bars: int, tp_sl_ratio: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    low = bars["low"].to_numpy()
    high = bars["high"].to_numpy()
    close = bars["close"].to_numpy()
    count = len(candidates)
    exits = np.empty(count, dtype=np.int32)
    reasons = np.empty(count, dtype=np.int8)
    ratios = np.empty(count, dtype=np.float64)

    for k, row in enumerate(candidates.itertuples(index=False)):
        start = int(row.entry_index)
        end = min(start + max_holding_bars, len(bars) - 1)
        risk = row.entry_price - row.stop_loss
        for bar in range(start + 1, end + 1):
            if bar - start >= max_holding_bars:
                reason, ratio = TIMEOUT, (close[bar] - row.entry_price) / risk
            elif low[bar] <= row.stop_loss:
                reason, ratio = STOP, tp_sl_ratio
            elif high[bar] >= row.take_profit:
                reason, ratio = TARGET, tp_sl_ratio
            else:
                continue
            break
        else:
            bar = end
            reason, ratio = DATA_END, (close[end] - row.entry_price) / risk
        exits[k], reasons[k], ratios[k] = bar, reason, ratio
    return exits, reasons, ratios


def _gpu_exits(bars: pd.DataFrame, candidates: pd.DataFrame,
               max_holding_bars: int, tp_sl_ratio: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if cuda is None or not cuda.is_available():
        raise RuntimeError("Numba CUDA is unavailable; use --backend cpu")

    count = len(candidates)
    low = cuda.to_device(np.ascontiguousarray(bars["low"].to_numpy(dtype=np.float64)))
    high = cuda.to_device(np.ascontiguousarray(bars["high"].to_numpy(dtype=np.float64)))
    close = cuda.to_device(np.ascontiguousarray(bars["close"].to_numpy(dtype=np.float64)))
    entries = cuda.to_device(candidates["entry_index"].to_numpy(dtype=np.int32))
    entry_prices = cuda.to_device(candidates["entry_price"].to_numpy(dtype=np.float64))
    stops = cuda.to_device(candidates["stop_loss"].to_numpy(dtype=np.float64))
    targets = cuda.to_device(candidates["take_profit"].to_numpy(dtype=np.float64))
    exits = cuda.device_array(count, dtype=np.int32)
    reasons = cuda.device_array(count, dtype=np.int8)
    ratios = cuda.device_array(count, dtype=np.float64)

    threads = 128
    _exit_kernel[(count + threads - 1) // threads, threads](
        low, high, close, entries, entry_prices, stops, targets,
        max_holding_bars, tp_sl_ratio, exits, reasons, ratios,
    )
    cuda.synchronize()
    return exits.copy_to_host(), reasons.copy_to_host(), ratios.copy_to_host()


def resolve_exits(bars: pd.DataFrame, candidates: pd.DataFrame,
                  max_holding_bars: int, tp_sl_ratio: float,
                  backend: str = "auto", verify: bool = False) -> tuple[pd.DataFrame, str]:
    if backend not in {"auto", "cpu", "gpu"}:
        raise ValueError("backend must be auto, cpu or gpu")
    if max_holding_bars < 1:
        raise ValueError("max_holding_bars must be positive")
    if candidates.empty:
        return candidates.assign(exit_index=pd.Series(dtype=np.int32)), "none"

    use_gpu = backend == "gpu" or (
        backend == "auto" and cuda is not None and cuda.is_available()
    )
    if use_gpu:
        try:
            exits, reasons, ratios = _gpu_exits(
                bars, candidates, max_holding_bars, tp_sl_ratio,
            )
        except Exception as exc:
            if backend == "gpu":
                raise
            warnings.warn(f"GPU exit check failed ({exc}); using CPU", stacklevel=2)
            use_gpu = False
    if not use_gpu:
        exits, reasons, ratios = _cpu_exits(
            bars, candidates, max_holding_bars, tp_sl_ratio,
        )
    if verify and use_gpu:
        cpu_exits, cpu_reasons, cpu_ratios = _cpu_exits(
            bars, candidates, max_holding_bars, tp_sl_ratio,
        )
        np.testing.assert_array_equal(exits, cpu_exits)
        np.testing.assert_array_equal(reasons, cpu_reasons)
        np.testing.assert_allclose(ratios, cpu_ratios, rtol=1e-12, atol=1e-12)

    resolved = candidates.copy()
    resolved["exit_index"] = exits
    resolved["exit_reason"] = reasons
    resolved["tp_sl_ratio"] = ratios
    return resolved, "gpu" if use_gpu else "cpu"


def select_trades(bars: pd.DataFrame, resolved: pd.DataFrame,
                  allow_overlap: bool = False) -> pd.DataFrame:
    timestamps = bars["timestamp"].to_numpy()
    rows = []
    last_exit = -1
    for row in resolved.itertuples(index=False):
        if not allow_overlap and row.entry_index <= last_exit:
            continue
        holding_bars = int(row.exit_index - row.entry_index)
        holding_fee = 0.0001 * (holding_bars / 32) / row.sl_price_ratio
        rows.append({
            "entry_index": int(row.entry_index),
            "exit_index": int(row.exit_index),
            "open_timestamp": timestamps[row.entry_index],
            "close_timestamp": timestamps[row.exit_index],
            "entry_price": row.entry_price,
            "stop_loss": row.stop_loss,
            "take_profit": row.take_profit,
            "tp_sl_ratio": row.tp_sl_ratio,
            "fee_risk_ratio": row.entry_fee_risk_ratio + holding_fee,
            "win_lose": row.exit_reason != STOP,
            "exit_reason": REASONS[row.exit_reason],
            "holding_bars": holding_bars,
        })
        last_exit = int(row.exit_index)
    return pd.DataFrame(rows)


def evaluate(trades: pd.DataFrame, start_time: pd.Timestamp,
             end_time: pd.Timestamp) -> dict:
    if trades.empty:
        return {"performance": 0.0, "max_dd": 0.0, "r2": 0.0,
                "number_of_trade": 0, "score": np.nan}
    ordered = trades.sort_values("close_timestamp", kind="stable")
    r = np.where(ordered["win_lose"], ordered["tp_sl_ratio"], -1.0)
    real_cum_r = np.cumsum(r - ordered["fee_risk_ratio"].to_numpy())
    performance = float(real_cum_r[-1])
    # Match the source notebook's zero-R dummy rows at the first and last bar.
    cum_r = np.r_[0.0, real_cum_r, performance]
    timeline = np.r_[np.datetime64(start_time),
                     ordered["close_timestamp"].to_numpy(),
                     np.datetime64(end_time)]
    max_dd = round(float(np.max(np.maximum.accumulate(cum_r) - cum_r)), 2)
    seconds = (timeline - timeline[0]) / np.timedelta64(1, "s")
    x = seconds.reshape(-1, 1)
    r2 = float(LinearRegression().fit(x, cum_r).score(x, cum_r))
    score = performance / max_dd * r2**5 if max_dd > 0 else np.nan
    return {"performance": performance, "max_dd": max_dd, "r2": r2,
            "number_of_trade": len(trades), "score": score}


def backtest(bars: pd.DataFrame, params: dict, backend: str,
             max_holding_bars: int, allow_overlap: bool,
             verify: bool = False) -> tuple[pd.DataFrame, dict, str, int]:
    candidates = find_candidates(bars, params)
    resolved, actual_backend = resolve_exits(
        bars, candidates, max_holding_bars, params["tp_sl_ratio"], backend, verify,
    )
    trades = select_trades(bars, resolved, allow_overlap)
    return trades, evaluate(trades, bars["timestamp"].iloc[0],
                            bars["timestamp"].iloc[-1]), actual_backend, len(candidates)


def run_grid(bars: pd.DataFrame, backend: str, max_holding_bars: int,
             allow_overlap: bool, limit: int | None, output: Path) -> None:
    keys = list(SEARCH_SPACE)
    combos = itertools.product(*(SEARCH_SPACE[key] for key in keys))
    rows = []
    for number, values in enumerate(combos, start=1):
        if limit is not None and number > limit:
            break
        params = dict(zip(keys, values))
        _, metrics, used, candidates = backtest(
            bars, params, backend, max_holding_bars, allow_overlap,
        )
        rows.append({**params, **metrics, "candidate_count": candidates, "backend": used})
        if number % 100 == 0:
            print(f"Completed {number} parameter sets")
    result = pd.DataFrame(rows)
    result.to_csv(output, index=False)
    valid = result.dropna(subset=["score"])
    print(f"Saved {len(result)} results to {output}")
    if not valid.empty:
        print("Best:", valid.loc[valid["score"].idxmax()].to_dict())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--backend", choices=["auto", "cpu", "gpu"], default="auto")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Compare GPU and CPU exit results")
    parser.add_argument("--grid", action="store_true", help="Run the notebook's full parameter grid")
    parser.add_argument("--limit", type=int, help="Number of grid combinations to run")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("candidate_gpu_grid_results.csv"))
    args = parser.parse_args()

    bars = load_bars(args.csv, args.timeframe)
    print(f"Loaded {len(bars)} bars from {args.csv}; timeframe={args.timeframe}")
    if args.grid:
        run_grid(bars, args.backend, args.max_holding_bars,
                 args.allow_overlap, args.limit, args.output)
    else:
        trades, metrics, used, candidates = backtest(
            bars, DEFAULT_PARAMS, args.backend, args.max_holding_bars,
            args.allow_overlap, args.verify,
        )
        print(f"Exit backend: {used}; candidates: {candidates}; trades: {len(trades)}")
        print("Metrics:", metrics)
        print(trades.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
