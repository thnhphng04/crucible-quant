"""AVAX Bollinger + RSI breakout with overlapping positions: grid search on CUDA (fp32).

Entries, stops, take profits, timeouts and fees are those of reference/long_bollinger+RSI
(side = +1) and reference/short_bollinger+RSI (side = -1):

    long : close[i-1] < upper[i] < close[i] and RSI[i] > rsi_threshold, risk = (entry - lower) * sl_multiplier
    short: close[i-1] > lower[i] > close[i] and RSI[i] < rsi_threshold, risk = (upper - entry) * sl_multiplier

The difference: the originals hold one position and skip every signal while it is open; here every
signal opens its own trade, which then runs to its own stop / take profit / timeout. At each bar t:

1. signal at t, judged against the positions open at the start of t; opens unless max_open are open
   (max_open = 1 reproduces the original scripts exactly: a trade's exit bar cannot open a new trade);
2. exits of the open trades, in entry order: timeout -> stop -> take profit -> end of data;
3. the new trade (if any) joins the open list; its exit checks start at t + 1;
4. mark-to-market equity, open-position count and end-of-day bookkeeping.

R is per trade: with N trades open, N R are at risk. Closed-trade metrics (performance, max_dd,
avg_dd, r2, r_per_month, ulcer_r, r_martin_monthly, r_sharpe_monthly) follow the originals, ordered
by exit bar; mtm_max_dd also counts open trades at each close (entry fee and accrued holding fee
included); max_concurrent / avg_concurrent count open trades per bar; skipped_entries counts signals
dropped because max_open trades were open. tp_sl_ratio = 0 means no take profit.

Optional trend filter (grid column trend_window, 0 or absent = off): a long signal also needs
close[t] > SMA(close, trend_window)[t], a short signal close[t] < SMA(close, trend_window)[t]. The SMA comes
from a `trend_sma_<window>` column of `bars` when present (add_trend_columns on the full history, so long
windows are warm at the start of a walk-forward slice), otherwise from the bars passed in.

Optional entry_mode column (0 or absent = the breakout above). 1 and 2 FADE the band instead, trading
against the move, with risk = sl_multiplier x (upper - lower):

    1, leaving the band   long : close[i-1] > lower[i] > close[i] and RSI[i] < rsi_threshold
                          short: close[i-1] < upper[i] < close[i] and RSI[i] > rsi_threshold
    2, returning inside   long : close[i-1] < lower[i] < close[i] and RSI[i] < rsi_threshold
                          short: close[i-1] > upper[i] > close[i] and RSI[i] > rsi_threshold

Optional max_holding_bars column: the timeout per row (absent = the max_holding_bars argument).

Optional band_ratio_max column (0 or absent = off): a signal also needs width[t] <= band_ratio_max x the median
width over the trailing BAND_RATIO_LOOKBACK_DAYS, width = (upper - lower) / close of the signal's bands, so
trades start from a band that is narrow for its recent past (band_width_ratio, float64 on the CPU, shared by
the GPU and the replay).

min_trades_per_year (result column, not a parameter): the fewest trades closed in a calendar year, each year's
count scaled to a full year; years covering under half a year are skipped unless no year covers more.

tp_sl_ratio = -1 puts the take profit at the middle band of the entry bar (fixed, like a limit order);
hitting it books (middle - entry) / risk R for a long, (entry - middle) / risk for a short, and a signal whose
middle band is not on the profitable side of the entry is not traded.

Wilson lower bound (reference/Wilson_Lower_Bound_RR_Strategy_VI.docx): a win is a trade whose net R (after
fees) is > 0. number_of_win, avg_win_r (mean net R of the wins) and avg_loss_r (mean |net R| of the rest) come
from the kernel; add_wilson_columns turns them into win_rate, wilson_p_lower (one-sided Wilson lower bound of
the win rate at `confidence`) and

    wilson_fitness = p_L * avg_win_r - (1 - p_L) * avg_loss_r      (R per trade, net of fees)

With a fixed payoff +k / -1 and a fixed cost c per trade this is the document's (k + 1) p_L - 1 - c; with
timeouts, tp_sl_ratio = 0 or the middle-band take profit the payoff varies and only the win rate's uncertainty
is bounded, not that of the payoff sizes (the document's section 8).
"""

from __future__ import annotations

import argparse
import ast
import itertools
import math
from pathlib import Path
from statistics import NormalDist
from time import perf_counter

import numpy as np
import pandas as pd
from numba import cuda, float32, int32
from pandas.tseries.frequencies import to_offset
from pandas.tseries.offsets import Tick

REFERENCE = Path(__file__).resolve().parents[1]
SIDE_LONG = 1
SIDE_SHORT = -1
DEFAULT_CSV = {SIDE_LONG: REFERENCE / "long_bollinger+RSI" / "avaxusdt_futures_5min.csv",
               SIDE_SHORT: REFERENCE / "short_bollinger+RSI" / "avaxusdt_futures_5min.csv"}
DEFAULT_SCORE = "r_martin_monthly"
MAX_SLOTS = 64  # open trades one parameter set can hold; signals beyond it count as skipped
# Costs as fractions of price: round-trip entry fee, holding fee per fee_bars (8 hours). Crypto defaults; the CUDA
# kernels freeze them at their first compile, so a caller changes them before the first run (forex: §9p).
ENTRY_FEE_RATE = 0.001
HOLDING_FEE_RATE = 0.0001
MAX_FEE_R = 0.2  # a signal is skipped when its entry fee exceeds this fraction of R (original scripts: 20%)
BAND_RATIO_LOOKBACK_DAYS = 30  # trailing window of the band-width median (band_ratio_max)
MAX_YEARS = 16  # calendar years one run can span (min_trades_per_year)
WILSON_CONFIDENCE = 0.95  # one-sided lower bound: z = 1.645
SCORE_NAMES = frozenset({
    "performance", "max_dd", "avg_dd", "r2", "number_of_trade",
    "r_per_month", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly",
    "mtm_max_dd", "max_concurrent", "avg_concurrent",
    "number_of_win", "win_rate", "avg_win_r", "avg_loss_r", "wilson_p_lower", "wilson_fitness",
    "min_trades_per_year",
})
MONTH_DAYS = 365.2425 / 12
RAW_BAR_NANOS = pd.Timedelta(minutes=5).value
OUTPUT_COLUMNS = ("performance", "max_dd", "avg_dd", "r2", "r_per_month", "ulcer_r",
                  "r_martin_monthly", "r_sharpe_monthly", "mtm_max_dd", "avg_concurrent",
                  "avg_win_r", "avg_loss_r", "min_trades_per_year")
COUNT_COLUMNS = ("number_of_trade", "max_concurrent", "skipped_entries", "number_of_win")


def timeframe_nanos(timeframe: str) -> int:
    try:
        offset = to_offset(timeframe)
    except ValueError as exc:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use a fixed pandas offset like 5min, 15min, 1h, or 4h") from exc
    if not isinstance(offset, Tick):
        raise ValueError(f"Timeframe {timeframe!r} must be a fixed tick offset like 5min, 15min, 1h, or 4h")
    nanos = int(offset.nanos)
    if nanos < RAW_BAR_NANOS or nanos % RAW_BAR_NANOS != 0:
        raise ValueError("Timeframe must be a positive multiple of the 5min source data")
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


def bars_per_day(bars: pd.DataFrame) -> float:
    step = bars["timestamp"].diff().median()
    return pd.Timedelta(days=1) / step


def band_width_ratio(bars: pd.DataFrame, window: int, deviation: float) -> np.ndarray:
    """(upper - lower) / close over its trailing BAND_RATIO_LOOKBACK_DAYS median (NaN until a third is warm)."""
    close = bars["close"].to_numpy(dtype=np.float64)
    upper, lower = _cpu_bands(close, int(window), float(deviation))
    width = pd.Series((upper - lower) / close)
    lookback = max(int(round(BAND_RATIO_LOOKBACK_DAYS * bars_per_day(bars))), 2)
    median = width.rolling(lookback, min_periods=max(lookback // 3, 1)).median()
    return (width / median).to_numpy()


def year_weights(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Calendar-year index of every bar, and per year the factor that scales its trade count to a full year
    (0 = skipped: covers under half a year while another year covers more)."""
    timestamps = bars["timestamp"]
    years = timestamps.dt.year.to_numpy()
    first = int(years[0])
    index = (years - first).astype(np.int32)
    count = int(index[-1]) + 1
    if count > MAX_YEARS:
        raise ValueError(f"bars span {count} calendar years; at most {MAX_YEARS}")
    step = timestamps.diff().median()
    coverage = np.zeros(count)
    for year in range(count):
        inside = timestamps[index == year]
        if len(inside):
            length = pd.Timestamp(first + year + 1, 1, 1) - pd.Timestamp(first + year, 1, 1)
            coverage[year] = min((inside.iloc[-1] - inside.iloc[0] + step) / length, 1.0)
    weights = np.where(coverage > 0, 1.0 / np.maximum(coverage, 1e-12), 0.0)
    if (coverage >= 0.5).any():
        weights[coverage < 0.5] = 0.0
    return np.ascontiguousarray(index), np.ascontiguousarray(weights.astype(np.float32))


def min_trades_per_year(bars: pd.DataFrame, exit_indices: np.ndarray) -> float:
    index, weights = year_weights(bars)
    counts = np.bincount(index[np.asarray(exit_indices, dtype=int)], minlength=len(weights))
    return float((counts * weights)[weights > 0].min())


def parameter_grid(space: dict, limit: int | None = None) -> pd.DataFrame:
    combos = itertools.product(*space.values())
    if limit is not None:
        combos = itertools.islice(combos, limit)
    return pd.DataFrame(combos, columns=list(space))


def wilson_lower_bound(wins, n, confidence: float = WILSON_CONFIDENCE) -> np.ndarray:
    """One-sided Wilson lower bound of the win rate; NaN where n <= 0."""
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1)")
    wins = np.asarray(wins, dtype=np.float64)
    n = np.asarray(n, dtype=np.float64)
    z = NormalDist().inv_cdf(confidence)
    z2 = z * z
    with np.errstate(divide="ignore", invalid="ignore"):
        p_hat = wins / n
        numerator = p_hat + z2 / (2 * n) - z * np.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n))
        lower = numerator / (1 + z2 / n)
    return np.where(n > 0, np.clip(lower, 0.0, 1.0), np.nan)  # clip: rounding at wins = 0


def add_wilson_columns(result: pd.DataFrame, confidence: float = WILSON_CONFIDENCE) -> pd.DataFrame:
    """win_rate, wilson_p_lower and wilson_fitness (R per trade) from number_of_win, avg_win_r, avg_loss_r."""
    trades = result["number_of_trade"].to_numpy(dtype=np.float64)
    wins = result["number_of_win"].to_numpy(dtype=np.float64)
    p_lower = wilson_lower_bound(wins, trades, confidence)
    # no wins (or no losses): p_L = 0 (or that side's weight is 0), so its undefined mean drops out
    avg_win = np.where(wins > 0, result["avg_win_r"].to_numpy(dtype=np.float64), 0.0)
    avg_loss = np.where(trades > wins, result["avg_loss_r"].to_numpy(dtype=np.float64), 0.0)
    result = result.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        result["win_rate"] = np.where(trades > 0, wins / trades, np.nan)
    result["wilson_p_lower"] = p_lower
    result["wilson_fitness"] = p_lower * avg_win - (1 - p_lower) * avg_loss
    result.attrs["wilson_confidence"] = confidence
    return result


def neighbour_score(result: pd.DataFrame, space: dict, column: str = "score", min_trades: int = 0,
                    radius: int = 1) -> pd.Series:
    """Median of `column` over each row's grid neighbourhood: every parameter within +-radius steps of its
    value list in `space` (the row itself included), so a set scores by the plateau around it, not its own peak.

    Rows with fewer than min_trades trades or a NaN `column` do not count as neighbours; a row gets NaN when
    fewer than half of its in-grid neighbours count. Rows may be any subset of the product of `space`.
    """
    names = list(space)
    shape = tuple(len(space[name]) for name in names)
    positions = []
    for name in names:
        lookup = {float(value): i for i, value in enumerate(space[name])}
        values = result[name].to_numpy(dtype=np.float64)
        index = np.array([lookup.get(float(value), -1) for value in values])
        if (index < 0).any():
            raise ValueError(f"{name} has values outside space[{name!r}]")
        positions.append(index)
    flat = np.ravel_multi_index(positions, shape)
    if len(np.unique(flat)) != len(flat):
        raise ValueError("result has duplicate parameter sets")
    valid = result[column].notna().to_numpy()
    if min_trades:
        valid &= result["number_of_trade"].to_numpy() >= min_trades
    cube = np.full(int(np.prod(shape)), np.nan, dtype=np.float32)
    cube[flat[valid]] = result[column].to_numpy(dtype=np.float32)[valid]
    cube = cube.reshape(shape)
    padded = np.pad(cube, radius, constant_values=np.nan)
    # only shift the dimensions that have more than one value
    steps = [range(-radius, radius + 1) if size > 1 else range(0, 1) for size in shape]
    shifts = list(itertools.product(*steps))
    in_grid = np.ones(shape)
    for axis, size in enumerate(shape):
        i = np.arange(size)
        width = np.minimum(i + radius, size - 1) - np.maximum(i - radius, 0) + 1
        in_grid = in_grid * width.reshape([-1 if a == axis else 1 for a in range(len(shape))])
    # the neighbour stack is len(shifts) x grid; build it a block of leading positions at a time (<= ~64 MB)
    lead = 0
    while lead < len(shape) and len(shifts) * np.prod(shape[lead:]) > 16_000_000:
        lead += 1
    median = np.full(shape, np.nan, dtype=np.float32)
    rest = shape[lead:]
    for block in np.ndindex(*shape[:lead]):
        stack = np.stack([padded[tuple(radius + d + b for d, b in zip(shift, block))
                                 + tuple(slice(radius + d, radius + d + size) for d, size in zip(shift[lead:], rest))]
                          for shift in shifts])
        counted = (~np.isnan(stack)).sum(axis=0)
        with np.errstate(all="ignore"):
            part = np.nanmedian(stack, axis=0)
        part[counted < in_grid[block] / 2] = np.nan
        median[block] = part
    return pd.Series(median.reshape(-1)[flat].astype(np.float64), index=result.index, name=f"{column}_neighbour")


def score_result(result: pd.DataFrame, formula: str) -> pd.DataFrame:
    """Evaluate arithmetic over known result columns; never execute Python code."""
    parsed = ast.parse(formula, mode="eval")
    if sum(1 for _ in ast.walk(parsed)) > 64:
        raise ValueError("Score formula is too long")
    # columns missing from older result CSVs (e.g. the Wilson ones) are only an error when the formula uses them
    values = {name: result[name].to_numpy(dtype=np.float64) for name in SCORE_NAMES if name in result}

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


@cuda.jit
def _grid_kernel(side, close, high, low, elapsed_days, upper, lower, rsi,
                 day_end_flags, elapsed_months,
                 boll_indices, rsi_indices, rsi_windows, thresholds,
                 multipliers, tp_ratios, max_opens, trend_indices, trend, entry_modes,
                 max_holds, band_caps, band_ratio_indices, band_ratio, year_index, year_weights,
                 period_index, period_rs, period_counts, fee_bars,
                 performances, max_drawdowns, avg_drawdowns, r2_values,
                 r_per_months, ulcer_rs, r_martin_monthlies, r_sharpe_monthlies,
                 mtm_max_drawdowns, avg_concurrents, avg_win_rs, avg_loss_rs, min_year_trades,
                 trade_counts, max_concurrents, skipped_counts, win_counts):
    combo = cuda.grid(1)
    if combo >= boll_indices.size:
        return
    year_counts = cuda.local.array(MAX_YEARS, int32)
    for y in range(MAX_YEARS):
        year_counts[y] = 0
    slot_index = cuda.local.array(MAX_SLOTS, int32)
    slot_entry = cuda.local.array(MAX_SLOTS, float32)
    slot_risk = cuda.local.array(MAX_SLOTS, float32)
    slot_stop = cuda.local.array(MAX_SLOTS, float32)
    slot_target = cuda.local.array(MAX_SLOTS, float32)
    slot_fee = cuda.local.array(MAX_SLOTS, float32)
    slot_ratio = cuda.local.array(MAX_SLOTS, float32)
    slot_tp_r = cuda.local.array(MAX_SLOTS, float32)

    bw = boll_indices[combo]
    rw = rsi_indices[combo]
    threshold = thresholds[combo]
    multiplier = multipliers[combo]
    tp_ratio = tp_ratios[combo]
    max_open = max_opens[combo]
    ti = trend_indices[combo]
    mode = entry_modes[combo]
    max_holding_bars = max_holds[combo]
    band_cap = band_caps[combo]
    bri = band_ratio_indices[combo]
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
    previous_day_cumulative = zero
    daily_r_mean = zero
    daily_r_m2 = zero
    mtm_peak = zero
    mtm_max_dd = zero
    open_count = 0
    open_total = 0
    max_concurrent = 0
    skipped = 0
    wins = 0
    win_sum = zero
    loss_sum = zero

    # Dummy trades at the first and final timestamps, as in the original notebooks.
    observations = one
    mean_x = zero
    mean_y = zero
    m2x = zero
    m2y = zero
    covariance = zero

    n = close.size
    start = max(rsi_windows[rw], 1)
    for t in range(n):
        # 1. Signal, judged against the trades open at the start of the bar.
        new_trade = False
        new_entry = zero
        new_risk = zero
        new_stop = zero
        new_target = zero
        new_fee = zero
        new_ratio = zero
        new_tp_r = zero
        if t >= start and t < n - 1:
            if mode == 0:
                if side == 1:
                    band = upper[bw, t]
                    signal = close[t - 1] < band and close[t] > band and rsi[rw, t] > threshold
                else:
                    band = lower[bw, t]
                    signal = close[t - 1] > band and close[t] < band and rsi[rw, t] < threshold
            elif mode == 1:
                # fade the close that leaves the band
                if side == 1:
                    band = lower[bw, t]
                    signal = close[t - 1] > band and close[t] < band and rsi[rw, t] < threshold
                else:
                    band = upper[bw, t]
                    signal = close[t - 1] < band and close[t] > band and rsi[rw, t] > threshold
            else:
                # fade the close that comes back inside the band
                if side == 1:
                    band = lower[bw, t]
                    signal = close[t - 1] < band and close[t] > band and rsi[rw, t] < threshold
                else:
                    band = upper[bw, t]
                    signal = close[t - 1] > band and close[t] < band and rsi[rw, t] > threshold
            if signal and ti >= 0:
                if side == 1:
                    signal = close[t] > trend[ti, t]
                else:
                    signal = close[t] < trend[ti, t]
            if signal and band_cap > zero:
                signal = band_ratio[bri, t] <= band_cap  # NaN (not warm) -> no signal
            if signal:
                entry = close[t]
                if mode != 0:
                    risk = (upper[bw, t] - lower[bw, t]) * multiplier
                elif side == 1:
                    risk = (entry - lower[bw, t]) * multiplier
                else:
                    risk = (upper[bw, t] - entry) * multiplier
                if entry > zero and risk > zero:
                    sl_price_ratio = risk / entry
                    entry_fee = np.float32(ENTRY_FEE_RATE) / sl_price_ratio
                    if entry_fee <= np.float32(MAX_FEE_R):
                        # stop/target next to risk, as in the original scripts, so the compiler fuses the
                        # arithmetic the same way (fp32 stop prices then match bit for bit).
                        tp_r = tp_ratio
                        if tp_ratio < zero:
                            # take profit at the middle band of the entry bar
                            target = (upper[bw, t] + lower[bw, t]) * np.float32(0.5)
                            if side == 1:
                                stop = entry - risk
                                tp_r = (target - entry) / risk
                            else:
                                stop = entry + risk
                                tp_r = (entry - target) / risk
                        elif side == 1:
                            stop = entry - risk
                            target = entry + tp_ratio * risk
                        else:
                            stop = entry + risk
                            target = entry - tp_ratio * risk
                        if not tp_r > zero and tp_ratio < zero:
                            pass  # middle band on the losing side of the entry: no trade
                        elif open_count < max_open:
                            new_trade = True
                            new_entry = entry
                            new_risk = risk
                            new_stop = stop
                            new_target = target
                            new_tp_r = tp_r
                            new_fee = entry_fee
                            new_ratio = sl_price_ratio
                        else:
                            skipped += 1

        # 2. Exits of the open trades, in entry order.
        kept = 0
        for k in range(open_count):
            holding = t - slot_index[k]
            entry = slot_entry[k]
            risk = slot_risk[k]
            closed = False
            trade_r = zero
            if side == 1:
                if holding >= max_holding_bars:
                    trade_r = (close[t] - entry) / risk
                    closed = True
                elif low[t] <= slot_stop[k]:
                    trade_r = -one
                    closed = True
                elif tp_ratio != zero and high[t] >= slot_target[k]:
                    trade_r = slot_tp_r[k]
                    closed = True
                elif t == n - 1:
                    trade_r = (close[t] - entry) / risk
                    closed = True
            else:
                if holding >= max_holding_bars:
                    trade_r = (entry - close[t]) / risk
                    closed = True
                elif high[t] > slot_stop[k]:
                    trade_r = -one
                    closed = True
                elif tp_ratio != zero and low[t] < slot_target[k]:
                    trade_r = slot_tp_r[k]
                    closed = True
                elif t == n - 1:
                    trade_r = (entry - close[t]) / risk
                    closed = True
            if closed:
                holding_fee = (np.float32(HOLDING_FEE_RATE) *
                               (np.float32(holding) / np.float32(fee_bars)) /
                               slot_ratio[k])
                net_r = trade_r - slot_fee[k] - holding_fee
                cumulative += net_r
                count += 1
                year_counts[year_index[t]] += 1
                period = period_index[t]
                if period >= 0:
                    period_rs[combo, period] += net_r
                    period_counts[combo, period] += 1
                if net_r > zero:
                    wins += 1
                    win_sum += net_r
                else:
                    loss_sum -= net_r
                peak = max(peak, cumulative)
                drawdown = peak - cumulative
                max_dd = max(max_dd, drawdown)
                drawdown_sum += drawdown

                x = elapsed_days[t]
                observations += one
                dx = x - mean_x
                mean_x += dx / observations
                dy = cumulative - mean_y
                mean_y += dy / observations
                m2x += dx * (x - mean_x)
                m2y += dy * (cumulative - mean_y)
                covariance += dx * (cumulative - mean_y)
            else:
                slot_index[kept] = slot_index[k]
                slot_entry[kept] = entry
                slot_risk[kept] = risk
                slot_stop[kept] = slot_stop[k]
                slot_target[kept] = slot_target[k]
                slot_fee[kept] = slot_fee[k]
                slot_ratio[kept] = slot_ratio[k]
                slot_tp_r[kept] = slot_tp_r[k]
                kept += 1
        open_count = kept

        # 3. The new trade joins the open list.
        if new_trade:
            slot_index[open_count] = t
            slot_entry[open_count] = new_entry
            slot_risk[open_count] = new_risk
            slot_stop[open_count] = new_stop
            slot_target[open_count] = new_target
            slot_fee[open_count] = new_fee
            slot_ratio[open_count] = new_ratio
            slot_tp_r[open_count] = new_tp_r
            open_count += 1

        # 4. Mark-to-market equity, concurrency, end of day.
        open_total += open_count
        if open_count > max_concurrent:
            max_concurrent = open_count
        unrealized = zero
        for k in range(open_count):
            if side == 1:
                move = (close[t] - slot_entry[k]) / slot_risk[k]
            else:
                move = (slot_entry[k] - close[t]) / slot_risk[k]
            accrued = (np.float32(HOLDING_FEE_RATE) *
                       (np.float32(t - slot_index[k]) / np.float32(fee_bars)) / slot_ratio[k])
            unrealized += move - slot_fee[k] - accrued
        mtm = cumulative + unrealized
        mtm_peak = max(mtm_peak, mtm)
        mtm_max_dd = max(mtm_max_dd, mtm_peak - mtm)

        if day_end_flags[t] != 0:
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
    mtm_max_drawdowns[combo] = mtm_max_dd
    avg_concurrents[combo] = np.float32(open_total) / np.float32(n)
    avg_win_rs[combo] = win_sum / np.float32(wins) if wins > 0 else np.float32(math.nan)
    avg_loss_rs[combo] = loss_sum / np.float32(count - wins) if count > wins else np.float32(math.nan)
    fewest = np.float32(math.inf)
    for y in range(year_weights.size):
        if year_weights[y] > zero:
            fewest = min(fewest, np.float32(year_counts[y]) * year_weights[y])
    min_year_trades[combo] = fewest
    trade_counts[combo] = count
    max_concurrents[combo] = max_concurrent
    skipped_counts[combo] = skipped
    win_counts[combo] = wins


def trend_series(bars: pd.DataFrame, window: int) -> np.ndarray:
    """SMA(close, window): the precomputed `trend_sma_<window>` column if bars has one."""
    column = f"trend_sma_{int(window)}"
    if column in bars:
        return bars[column].to_numpy(dtype=np.float64)
    return bars["close"].rolling(int(window)).mean().to_numpy(dtype=np.float64)


def add_trend_columns(bars: pd.DataFrame, windows) -> pd.DataFrame:
    """Add `trend_sma_<window>` columns computed on these (full-history) bars; slices keep them warm."""
    bars = bars.copy()
    for window in sorted({int(w) for w in windows if int(w) > 0}):
        bars[f"trend_sma_{window}"] = bars["close"].rolling(window).mean()
    return bars


def _max_open_column(grid: pd.DataFrame, max_open: int) -> np.ndarray:
    values = grid["max_open"].to_numpy() if "max_open" in grid else np.full(len(grid), max_open)
    values = values.astype(np.int64)
    if (values < 1).any():
        raise ValueError("max_open must be at least 1")
    return np.minimum(values, MAX_SLOTS).astype(np.int32)


def run_gpu_grid(side: int, bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str,
                 max_holding_bars: int = 288, fee_bars: float | None = None,
                 score_formula: str = DEFAULT_SCORE,
                 max_open: int = MAX_SLOTS) -> tuple[pd.DataFrame, dict]:
    """Every row of `grid` on the GPU; an optional `max_open` column overrides the argument per row."""
    result, timing, _, _ = _run_gpu(side, bars, grid, timeframe, max_holding_bars, fee_bars, score_formula,
                                    max_open, None)
    return result, timing


def run_gpu_grid_periods(side: int, bars: pd.DataFrame, grid: pd.DataFrame, timeframe: str,
                         period_index: np.ndarray, max_holding_bars: int = 288, fee_bars: float | None = None,
                         score_formula: str = DEFAULT_SCORE, max_open: int = MAX_SLOTS
                         ) -> tuple[pd.DataFrame, dict, np.ndarray, np.ndarray]:
    """run_gpu_grid plus, per row and period, the net R and the count of the trades closed in it.

    period_index gives every bar's period (0..P-1, -1 = none), e.g. its calendar month; returns
    (result, timing, period_r [rows x P] float32, period_trades [rows x P] int32).
    """
    return _run_gpu(side, bars, grid, timeframe, max_holding_bars, fee_bars, score_formula, max_open,
                    np.asarray(period_index))


def _run_gpu(side, bars, grid, timeframe, max_holding_bars, fee_bars, score_formula, max_open, period_index):
    if side not in (SIDE_LONG, SIDE_SHORT):
        raise ValueError("side must be +1 (long) or -1 (short)")
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
    max_opens = _max_open_column(grid, max_open)
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
    trend_windows = (grid["trend_window"].to_numpy(dtype=np.int64) if "trend_window" in grid
                     else np.zeros(len(grid), dtype=np.int64))
    distinct_trends = sorted({int(w) for w in trend_windows if w > 0})
    trend = np.full((max(len(distinct_trends), 1), n), np.nan, dtype=np.float32)
    for row, window in enumerate(distinct_trends):
        trend[row] = trend_series(bars, window).astype(np.float32)
    trend_map = {window: row for row, window in enumerate(distinct_trends)}
    trend_indices = np.array([trend_map.get(int(w), -1) for w in trend_windows], dtype=np.int32)
    entry_modes = (grid["entry_mode"].to_numpy(dtype=np.int32) if "entry_mode" in grid
                   else np.zeros(len(grid), dtype=np.int32))
    if not np.isin(entry_modes, (0, 1, 2)).all():
        raise ValueError("entry_mode must be 0 (breakout), 1 (fade the break) or 2 (fade the return)")
    max_holds = (grid["max_holding_bars"].to_numpy(dtype=np.int64) if "max_holding_bars" in grid
                 else np.full(len(grid), max_holding_bars, dtype=np.int64))
    if (max_holds < 1).any():
        raise ValueError("max_holding_bars must be positive")
    band_caps = (grid["band_ratio_max"].to_numpy(dtype=np.float32) if "band_ratio_max" in grid
                 else np.zeros(len(grid), dtype=np.float32))
    ratio_pairs = list(dict.fromkeys(band_pairs[i] for i, cap in zip(boll_indices, band_caps) if cap > 0))
    band_ratio = np.full((max(len(ratio_pairs), 1), n), np.nan, dtype=np.float32)
    for row, (window, deviation) in enumerate(ratio_pairs):
        band_ratio[row] = band_width_ratio(bars, window, deviation).astype(np.float32)
    ratio_map = {pair: row for row, pair in enumerate(ratio_pairs)}
    band_ratio_indices = np.array([ratio_map.get(band_pairs[i], 0) for i in boll_indices], dtype=np.int32)
    year_index, weights = year_weights(bars)

    d_close = cuda.to_device(close)
    d_rsi_windows = cuda.to_device(rsi_windows)
    d_upper = cuda.device_array((len(band_pairs), n), dtype=np.float32)
    d_lower = cuda.device_array((len(band_pairs), n), dtype=np.float32)
    d_rsi = cuda.device_array((len(rsi_windows), n), dtype=np.float32)
    threads = 128
    boll_tasks = len(band_pairs) * n
    _bollinger_bands[(boll_tasks + threads - 1) // threads, threads](
        d_close, cuda.to_device(band_windows), cuda.to_device(band_deviations), d_upper, d_lower)
    _rsi_windows[1, 32](d_close, d_rsi_windows, d_rsi)
    cuda.synchronize()
    indicator_seconds = perf_counter() - started

    count = len(grid)
    if period_index is None:
        periods = 1
        bar_periods = np.full(n, -1, dtype=np.int32)
    else:
        if len(period_index) != n:
            raise ValueError("period_index needs one entry per bar")
        bar_periods = np.ascontiguousarray(period_index.astype(np.int32))
        periods = max(int(bar_periods.max()) + 1, 1)
    d_period_rs = cuda.to_device(np.zeros((count if period_index is not None else 1, periods), dtype=np.float32))
    d_period_counts = cuda.to_device(np.zeros((count if period_index is not None else 1, periods), dtype=np.int32))
    outputs = [cuda.device_array(count, dtype=np.float32) for _ in OUTPUT_COLUMNS]
    counts = [cuda.device_array(count, dtype=np.int32) for _ in COUNT_COLUMNS]
    _grid_kernel[(count + threads - 1) // threads, threads](
        np.int32(side), d_close, cuda.to_device(high), cuda.to_device(low), cuda.to_device(elapsed_days),
        d_upper, d_lower, d_rsi, cuda.to_device(end_flags), elapsed_months,
        cuda.to_device(boll_indices), cuda.to_device(rsi_indices), d_rsi_windows,
        cuda.to_device(grid["rsi_threshold"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["sl_multiplier"].to_numpy(dtype=np.float32)),
        cuda.to_device(grid["tp_sl_ratio"].to_numpy(dtype=np.float32)),
        cuda.to_device(max_opens), cuda.to_device(trend_indices), cuda.to_device(np.ascontiguousarray(trend)),
        cuda.to_device(np.ascontiguousarray(entry_modes)),
        cuda.to_device(np.ascontiguousarray(max_holds.astype(np.int32))), cuda.to_device(band_caps),
        cuda.to_device(band_ratio_indices), cuda.to_device(np.ascontiguousarray(band_ratio)),
        cuda.to_device(year_index), cuda.to_device(weights),
        cuda.to_device(bar_periods), d_period_rs, d_period_counts, np.float32(resolved_fee_bars),
        *outputs, *counts)
    cuda.synchronize()
    result = grid.copy()
    for column, output in zip(OUTPUT_COLUMNS, outputs):
        result[column] = output.copy_to_host()
    for column, output in zip(COUNT_COLUMNS, counts):
        result[column] = output.copy_to_host()
    result = add_wilson_columns(result)
    result = score_result(result, score_formula)
    timing = {"indicators_and_transfer_s": indicator_seconds, "holding_fee_bars": resolved_fee_bars,
              "total_gpu_s": perf_counter() - started}
    if period_index is None:
        return result, timing, None, None
    return result, timing, d_period_rs.copy_to_host(), d_period_counts.copy_to_host()


# ---------------------------------------------------------------- float64 CPU replay

def _cpu_bands(close: np.ndarray, window: int, deviation: float) -> tuple[np.ndarray, np.ndarray]:
    upper = np.full(len(close), np.nan)
    lower = np.full(len(close), np.nan)
    if len(close) >= window:
        windows = np.lib.stride_tricks.sliding_window_view(close, window)
        mean = windows.mean(axis=1)
        spread = deviation * windows.std(axis=1)
        upper[window - 1:] = mean + spread
        lower[window - 1:] = mean - spread
    return upper, lower


def _cpu_rsi(close: np.ndarray, window: int) -> np.ndarray:
    rsi = np.full(len(close), np.nan)
    alpha = 1.0 / window
    up_average = down_average = 0.0
    for i in range(1, len(close)):
        delta = close[i] - close[i - 1]
        up_average = (1 - alpha) * up_average + alpha * max(delta, 0.0)
        down_average = (1 - alpha) * down_average + alpha * max(-delta, 0.0)
        if i >= window - 1:
            rsi[i] = 100.0 if down_average == 0 else 100.0 - 100.0 / (1.0 + up_average / down_average)
    return rsi


def cpu_trace(side: int, bars: pd.DataFrame, params: dict, timeframe: str,
              max_holding_bars: int = 288, fee_bars: float | None = None,
              max_open: int = MAX_SLOTS) -> pd.DataFrame:
    """Every trade of one parameter set, float64, same bar-by-bar rules as the GPU kernel.

    Rows are in closing order (exit bar, then entry order). attrs: skipped_entries.
    """
    fee_bars = resolve_fee_bars(timeframe, fee_bars)
    max_open = min(int(params.get("max_open", max_open)), MAX_SLOTS)
    max_holding_bars = int(params.get("max_holding_bars", max_holding_bars))
    band_cap = float(params.get("band_ratio_max", 0))
    band_ratio = (band_width_ratio(bars, int(params["boll_window"]), float(np.float32(params["boll_window_dev"])))
                  if band_cap > 0 else None)
    close = bars["close"].to_numpy(dtype=np.float64)
    high = bars["high"].to_numpy(dtype=np.float64)
    low = bars["low"].to_numpy(dtype=np.float64)
    upper, lower = _cpu_bands(close, int(params["boll_window"]), float(params["boll_window_dev"]))
    rsi = _cpu_rsi(close, int(params["rsi_window"]))
    threshold = float(params["rsi_threshold"])
    trend_window = int(params.get("trend_window", 0))
    trend = trend_series(bars, trend_window) if trend_window > 0 else None
    multiplier = float(params["sl_multiplier"])
    tp_ratio = float(params["tp_sl_ratio"])
    mode = int(params.get("entry_mode", 0))
    n = len(close)
    start = max(int(params["rsi_window"]), 1)
    open_trades: list[dict] = []
    rows: list[dict] = []
    skipped = 0
    for t in range(n):
        new = None
        if start <= t < n - 1:
            if mode == 0:
                if side == SIDE_LONG:
                    signal = close[t - 1] < upper[t] < close[t] and rsi[t] > threshold
                    risk = (close[t] - lower[t]) * multiplier
                else:
                    signal = close[t - 1] > lower[t] > close[t] and rsi[t] < threshold
                    risk = (upper[t] - close[t]) * multiplier
            else:
                # fade: long at the lower band with RSI below the threshold, short at the upper band above it
                band = lower[t] if side == SIDE_LONG else upper[t]
                extreme = rsi[t] < threshold if side == SIDE_LONG else rsi[t] > threshold
                leaves = close[t - 1] > band > close[t] if side == SIDE_LONG else close[t - 1] < band < close[t]
                returns = close[t - 1] < band < close[t] if side == SIDE_LONG else close[t - 1] > band > close[t]
                signal = bool((leaves if mode == 1 else returns) and extreme)
                risk = (upper[t] - lower[t]) * multiplier
            if signal and trend is not None:
                signal = bool(side * (close[t] - trend[t]) > 0)
            if signal and band_ratio is not None:
                # the GPU compares the float32 copy of the same ratio
                signal = bool(np.float32(band_ratio[t]) <= np.float32(band_cap))
            if signal and close[t] > 0 and risk > 0 and ENTRY_FEE_RATE / (risk / close[t]) <= MAX_FEE_R:
                entry = close[t]
                if tp_ratio < 0:
                    target = (upper[t] + lower[t]) / 2  # middle band of the entry bar
                    tp_r = side * (target - entry) / risk
                else:
                    target = entry + side * tp_ratio * risk if tp_ratio > 0 else np.nan
                    tp_r = tp_ratio
                if tp_ratio < 0 and not tp_r > 0:
                    pass  # middle band on the losing side of the entry: no trade
                elif len(open_trades) < max_open:
                    new = {"entry_index": t, "entry_price": entry, "risk": risk,
                           "stop_loss": entry - side * risk, "take_profit": target, "tp_r": tp_r,
                           "sl_ratio": risk / entry}
                else:
                    skipped += 1
        still_open = []
        for trade in open_trades:
            holding = t - trade["entry_index"]
            entry, risk = trade["entry_price"], trade["risk"]
            stop_hit = low[t] <= trade["stop_loss"] if side == SIDE_LONG else high[t] > trade["stop_loss"]
            tp_hit = tp_ratio != 0 and (high[t] >= trade["take_profit"] if side == SIDE_LONG
                                        else low[t] < trade["take_profit"])
            if holding >= max_holding_bars:
                outcome, gross = "timeout", side * (close[t] - entry) / risk
            elif stop_hit:
                outcome, gross = "stop", -1.0
            elif tp_hit:
                outcome, gross = "take_profit", trade["tp_r"]
            elif t == n - 1:
                outcome, gross = "end_of_data", side * (close[t] - entry) / risk
            else:
                still_open.append(trade)
                continue
            entry_fee = ENTRY_FEE_RATE / trade["sl_ratio"]
            holding_fee = HOLDING_FEE_RATE * (holding / fee_bars) / trade["sl_ratio"]
            rows.append({**trade, "exit_index": t, "holding_bars": holding, "outcome": outcome,
                         "gross_r": gross, "entry_fee": entry_fee, "holding_fee": holding_fee,
                         "net_r": gross - entry_fee - holding_fee})
        open_trades = still_open
        if new is not None:
            new["open_at_entry"] = len(open_trades) + 1  # open trades after this bar's exits, itself included
            open_trades.append(new)
    columns = ["entry_index", "exit_index", "entry_price", "risk", "stop_loss", "take_profit", "tp_r",
               "holding_bars", "open_at_entry", "outcome", "gross_r", "entry_fee", "holding_fee", "net_r"]
    trades = pd.DataFrame(rows, columns=columns + ["sl_ratio"]).drop(columns="sl_ratio")
    trades.insert(1, "entry_timestamp", bars["timestamp"].to_numpy()[trades["entry_index"].to_numpy(dtype=int)])
    trades.insert(3, "close_timestamp", bars["timestamp"].to_numpy()[trades["exit_index"].to_numpy(dtype=int)])
    trades.attrs["skipped_entries"] = skipped
    return trades


def mtm_equity(bars: pd.DataFrame, trades: pd.DataFrame, timeframe: str,
               fee_bars: float | None = None) -> pd.DataFrame:
    """Per bar: closed-trade equity, mark-to-market equity (open trades at the close) and open count."""
    fee_bars = resolve_fee_bars(timeframe, fee_bars)
    close = bars["close"].to_numpy(dtype=np.float64)
    n = len(close)
    realized_steps = np.zeros(n + 1)
    unrealized = np.zeros(n)
    open_steps = np.zeros(n + 1, dtype=np.int64)
    for row in trades.itertuples():
        entry, exit_ = int(row.entry_index), int(row.exit_index)
        realized_steps[exit_] += row.net_r
        open_steps[entry] += 1
        open_steps[exit_] -= 1
        bars_held = np.arange(entry, exit_)
        sl_ratio = row.risk / row.entry_price
        side = 1.0 if row.stop_loss < row.entry_price else -1.0
        unrealized[entry:exit_] += (side * (close[entry:exit_] - row.entry_price) / row.risk
                                    - row.entry_fee
                                    - HOLDING_FEE_RATE * ((bars_held - entry) / fee_bars) / sl_ratio)
    realized = np.cumsum(realized_steps[:n])
    return pd.DataFrame({"timestamp": bars["timestamp"], "closed_equity": realized,
                         "mtm_equity": realized + unrealized,
                         "open_trades": np.cumsum(open_steps[:n])})


def trade_metrics(bars: pd.DataFrame, trades: pd.DataFrame, timeframe: str = "15min",
                  fee_bars: float | None = None) -> dict:
    """The GPU's result columns from a cpu_trace trade list, in float64."""
    elapsed_days = ((bars["timestamp"] - bars["timestamp"].iloc[0]).dt.total_seconds() / 86400).to_numpy()
    months = elapsed_days[-1] / MONTH_DAYS
    net = trades["net_r"].to_numpy(dtype=np.float64)
    cumulative = np.cumsum(net)
    peaks = np.maximum.accumulate(np.maximum(cumulative, 0.0)) if len(net) else np.zeros(0)
    drawdowns = peaks - cumulative
    final_dd = (peaks[-1] - cumulative[-1]) if len(net) else 0.0
    xs = np.concatenate([[0.0], elapsed_days[trades["exit_index"].to_numpy(dtype=int)], [elapsed_days[-1]]])
    ys = np.concatenate([[0.0], cumulative, [cumulative[-1] if len(net) else 0.0]])
    m2x, m2y = ((xs - xs.mean()) ** 2).sum(), ((ys - ys.mean()) ** 2).sum()
    cov = ((xs - xs.mean()) * (ys - ys.mean())).sum()
    r2 = 1.0 if m2y <= 0 else (0.0 if m2x <= 0 else cov * cov / (m2x * m2y))

    flags = day_end_flags(bars)
    daily_closed = np.zeros(len(bars))
    np.add.at(daily_closed, trades["exit_index"].to_numpy(dtype=int), net)
    equity_at_day_end = np.cumsum(daily_closed)[flags.astype(bool)]
    daily_r = np.diff(np.concatenate([[0.0], equity_at_day_end]))
    daily_dd = np.maximum.accumulate(np.maximum(equity_at_day_end, 0.0)) - equity_at_day_end
    r_per_month = cumulative[-1] / months if len(net) and months > 0 else (0.0 if months > 0 else math.nan)
    ulcer = math.sqrt((daily_dd ** 2).mean()) if len(daily_dd) else math.nan
    martin = r_per_month / ulcer if ulcer > 0 else math.nan
    std = daily_r.std(ddof=1) if len(daily_r) >= 2 else 0.0
    sharpe = daily_r.mean() / std * math.sqrt(MONTH_DAYS) if std > 0 else math.nan
    equity = mtm_equity(bars, trades, timeframe, fee_bars)
    mtm = equity["mtm_equity"].to_numpy()
    won = net > 0
    wins = int(won.sum())
    avg_win = float(net[won].mean()) if wins else math.nan
    avg_loss = float(-net[~won].mean()) if len(net) > wins else math.nan
    wilson = add_wilson_columns(pd.DataFrame({"number_of_trade": [len(net)], "number_of_win": [wins],
                                              "avg_win_r": [avg_win], "avg_loss_r": [avg_loss]})).iloc[0]
    return {
        "performance": float(cumulative[-1]) if len(net) else 0.0,
        "max_dd": round(float(drawdowns.max()) if len(net) else 0.0, 2),
        "avg_dd": round(float((drawdowns.sum() + final_dd) / (len(net) + 2)), 2),
        "r2": float(r2),
        "number_of_trade": len(net),
        "r_per_month": r_per_month,
        "ulcer_r": ulcer,
        "r_martin_monthly": martin,
        "r_sharpe_monthly": sharpe,
        "mtm_max_dd": float((np.maximum.accumulate(np.maximum(mtm, 0.0)) - mtm).max()),
        "max_concurrent": int(equity["open_trades"].max()),
        "avg_concurrent": float(equity["open_trades"].mean()),
        "skipped_entries": int(trades.attrs.get("skipped_entries", 0)),
        "number_of_win": wins,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "win_rate": float(wilson["win_rate"]),
        "wilson_p_lower": float(wilson["wilson_p_lower"]),
        "wilson_fitness": float(wilson["wilson_fitness"]),
        "min_trades_per_year": min_trades_per_year(bars, trades["exit_index"].to_numpy()),
    }


def verify_samples(side: int, bars: pd.DataFrame, result: pd.DataFrame, sample_count: int,
                   max_holding_bars: int = 288, fee_bars: float | None = None,
                   timeframe: str = "15min", max_open: int = MAX_SLOTS) -> pd.DataFrame:
    """Compare evenly spaced GPU rows with the float64 replay; prints and returns the differences."""
    if sample_count < 1:
        return pd.DataFrame()
    indices = np.unique(np.linspace(0, len(result) - 1, min(sample_count, len(result)), dtype=int))
    keys = ["performance", "r_per_month", "max_dd", "ulcer_r", "r_martin_monthly", "r_sharpe_monthly",
            "mtm_max_dd", "avg_concurrent", "avg_win_r", "avg_loss_r", "wilson_fitness", "min_trades_per_year"]
    rows = []
    for index in indices:
        row = result.iloc[index]
        params = row.to_dict()
        trades = cpu_trace(side, bars, params, timeframe, max_holding_bars, fee_bars, max_open)
        expected = trade_metrics(bars, trades, timeframe, fee_bars)
        # the Wilson bound at the confidence the result was scored with (add_wilson_columns records it)
        expected["wilson_fitness"] = float(add_wilson_columns(
            pd.DataFrame({k: [expected[k]] for k in ("number_of_trade", "number_of_win", "avg_win_r", "avg_loss_r")}),
            result.attrs.get("wilson_confidence", WILSON_CONFIDENCE))["wilson_fitness"].iloc[0])
        diff = {"row": int(index),
                "trade_count_diff": int(row["number_of_trade"]) - expected["number_of_trade"],
                "max_concurrent_diff": int(row["max_concurrent"]) - expected["max_concurrent"],
                "skipped_diff": int(row["skipped_entries"]) - expected["skipped_entries"],
                "win_count_diff": int(row["number_of_win"]) - expected["number_of_win"]}
        for key in keys:
            a, b = float(row[key]), float(expected[key])
            diff[key] = 0.0 if (math.isnan(a) and math.isnan(b)) else abs(a - b)
        rows.append(diff)
    table = pd.DataFrame(rows)
    print(f"Compared {len(table)} rows with float64 CPU: "
          f"{int((table['trade_count_diff'] != 0).sum())} trade-count differences, "
          f"{int((table['max_concurrent_diff'] != 0).sum())} max_concurrent differences, "
          f"{int((table['skipped_diff'] != 0).sum())} skipped differences, "
          f"{int((table['win_count_diff'] != 0).sum())} win-count differences; max |diff|: "
          + ", ".join(f"{key}={table[key].max():.4g}" for key in keys))
    return table


def main(doc: str, side: int, search_space: dict, script: Path) -> None:
    parser = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV[side])
    parser.add_argument("--timeframe", default="15min")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-07-01")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--max-open", type=int, default=MAX_SLOTS,
                        help=f"Open trades per parameter set (1 = the original one-position script; max {MAX_SLOTS})")
    parser.add_argument("--fee-bars", type=float, help="Bars per holding-fee charge (default 8 hours)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--verify", type=int, default=0)
    parser.add_argument("--score-formula", default=DEFAULT_SCORE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bars = load_bars(args.csv, args.timeframe, args.start, args.end)
    grid = parameter_grid(search_space, args.limit)
    result, timing = run_gpu_grid(side, bars, grid, args.timeframe, args.max_holding_bars,
                                  args.fee_bars, args.score_formula, args.max_open)
    verify_samples(side, bars, result, args.verify, args.max_holding_bars, args.fee_bars,
                   args.timeframe, args.max_open)
    output = args.output or script.with_name(f"full_gpu_grid_results_{args.timeframe}_fp32.csv")
    result.to_csv(output, index=False)
    print(f"Bars {len(bars):,}; parameter sets {len(grid):,}; timing {timing}")
    print(f"Signals skipped because {args.max_open} trades were open: {int(result['skipped_entries'].sum()):,}")
    print("Saved:", output)
    valid = result.dropna(subset=["score"])
    if not valid.empty:
        print("Best:", valid.loc[valid["score"].idxmax()].to_dict())
