"""Random-entry benchmark: does a strategy's entry timing beat random entries with the same exit?

For each sampled parameter set the strategy is replayed (numba, float64, same rules as the GPU
scripts: one position at a time, stop, optional take profit, timeout, entry fee 0.001/sl_ratio
with the 0.2 R cap, holding fee every fee_bars bars). Its random twin keeps everything but the
timing: on every flat bar it enters with probability p, calibrated so the expected trade count
equals the strategy's, and sets the stop at sl_multiplier x k x (upper - lower), where k is the
median of (entry - lower)/(upper - lower) (short: (upper - entry)/(upper - lower)) over the
strategy's own entries, so stops track volatility the same way. The output is how often the
strategy beats the 95th percentile of its random twins (5% expected with no timing edge), the
edge in R/month, and the edge per calendar quarter. Run from the repository root:

    uv run python reference/random_entry_benchmark.py --out <dir>
    uv run python reference/random_entry_benchmark.py --logics "long RSI" --exits none donchian:100 --hold 288 1152 --out <dir>
"""

from __future__ import annotations

import argparse
import itertools
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

import long_logic_engine as engine

warnings.filterwarnings("ignore")
REFERENCE = Path(__file__).resolve().parent
CSV = REFERENCE / "long_bollinger+RSI" / "avaxusdt_futures_5min.csv"
MONTH_DAYS = 365.2425 / 12


@njit(cache=True)
def _indicator_exit(rule, param, j, close, rsi, donchian, atr, upper, lower, highest):
    """Long indicator exits of long_logic_engine (1 RSI, 2 Donchian, 3 Chandelier, 4 %B)."""
    if rule == 1:
        return rsi[j] < param
    if rule == 2:
        return close[j] < donchian[j]
    if rule == 3:
        return close[j] < highest - param * atr[j]
    if rule == 4:
        width = upper[j] - lower[j]
        if not width > 0.0:
            return False
        return (close[j] - lower[j]) / width < param
    return False


@njit(cache=True)
def simulate(close, high, low, direction, signal, risk, start, tp, max_hold, fee_bars,
             random_p, seed, out_entry, out_exit, out_net,
             exit_rule, exit_param, rsi, donchian, atr, upper, lower):
    """One sequential run; random_p > 0 replaces `signal` by a Bernoulli(random_p) draw per flat bar.
    exit_rule > 0 (long only) adds the engine's indicator exit after timeout/stop/TP/end checks."""
    np.random.seed(seed)
    n = close.size
    count = 0
    i = start
    while i < n - 1:
        if random_p > 0.0:
            enter = np.random.random() < random_p
        else:
            enter = signal[i]
        if enter:
            entry = close[i]
            r = risk[i]
            if entry > 0.0 and r > 0.0:
                sl_ratio = r / entry
                entry_fee = 0.001 / sl_ratio
                if entry_fee <= 0.2:
                    if direction == 1:
                        stop = entry - r
                        target = entry + tp * r
                    else:
                        stop = entry + r
                        target = entry - tp * r
                    gross = 0.0
                    highest = entry
                    j = i + 1
                    while j < n:
                        holding = j - i
                        if holding >= max_hold:
                            gross = (close[j] - entry) / r * direction
                            break
                        if direction == 1:
                            if low[j] <= stop:
                                gross = -1.0
                                break
                            if tp > 0.0 and high[j] >= target:
                                gross = tp
                                break
                        else:
                            if high[j] > stop:
                                gross = -1.0
                                break
                            if tp > 0.0 and low[j] < target:
                                gross = tp
                                break
                        if j == n - 1:
                            gross = (close[j] - entry) / r * direction
                            break
                        if exit_rule > 0:
                            if high[j] > highest:
                                highest = high[j]
                            if _indicator_exit(exit_rule, exit_param, j, close, rsi, donchian, atr,
                                               upper, lower, highest):
                                gross = (close[j] - entry) / r * direction
                                break
                        j += 1
                    holding_fee = 0.0001 * ((j - i) / fee_bars) / sl_ratio
                    out_entry[count] = i
                    out_exit[count] = j
                    out_net[count] = gross - entry_fee - holding_fee
                    count += 1
                    i = j + 1
                    continue
        i += 1
    return count


class Market:
    def __init__(self, start: str, end: str, timeframe: str = "15min"):
        self.bars = engine.load_bars(CSV, timeframe, start, end)
        self.close = self.bars["close"].to_numpy(np.float64)
        self.high = self.bars["high"].to_numpy(np.float64)
        self.low = self.bars["low"].to_numpy(np.float64)
        self.volume = self.bars["volume"].to_numpy(np.float64)
        self.months = (self.bars["timestamp"].iloc[-1] - self.bars["timestamp"].iloc[0]).total_seconds() / 86400 / MONTH_DAYS
        quarters = self.bars["timestamp"].dt.to_period("Q").astype(str).to_numpy()
        self.quarter_labels, self.quarter_of_bar = np.unique(quarters, return_inverse=True)
        bars_per_quarter = np.bincount(self.quarter_of_bar)
        self.quarter_months = bars_per_quarter / len(self.close) * self.months
        self._cache: dict = {}
        n = len(self.close)
        self.buf = (np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n, np.float64))

    def bands(self, window: int, dev: float):
        key = ("band", window, round(dev, 6))
        if key not in self._cache:
            rolling = pd.Series(self.close).rolling(window)
            mean, spread = rolling.mean().to_numpy(), dev * rolling.std(ddof=0).to_numpy()
            self._cache[key] = (mean + spread, mean - spread)
        return self._cache[key]

    def rsi(self, window: int):
        key = ("rsi", window)
        if key not in self._cache:
            self._cache[key] = engine._cpu_rsi(self.close, window)
        return self._cache[key]

    def sma(self, values: str, window: int):
        key = ("sma", values, window)
        if key not in self._cache:
            self._cache[key] = engine.rolling_mean(getattr(self, values), window)
        return self._cache[key]

    def donchian(self, window: int):
        key = ("donchian", window)
        if key not in self._cache:
            self._cache[key] = engine.donchian_low(self.low, window)
        return self._cache[key]

    def atr(self):
        key = ("atr", engine.CHANDELIER_ATR)
        if key not in self._cache:
            self._cache[key] = engine.atr_wilder(self.high, self.low, self.close, engine.CHANDELIER_ATR)
        return self._cache[key]

    def nan_row(self):
        if "nan" not in self._cache:
            self._cache["nan"] = np.full(len(self.close), np.nan)
        return self._cache["nan"]


def strategy_arrays(market: Market, side: str, params: dict):
    """Entry signal, strategy risk and the band width, per bar (float64, NaN-safe comparisons)."""
    upper, lower = market.bands(int(params["boll_window"]), float(params["boll_window_dev"]))
    close = market.close
    previous = np.r_[np.nan, close[:-1]]
    rsi = market.rsi(int(params["rsi_window"]))
    with np.errstate(invalid="ignore"):
        if side == "long":
            signal = (previous < upper) & (close > upper) & (rsi > params["rsi_threshold"])
            if params.get("trend_window", 0):
                signal &= close > market.sma("close", int(params["trend_window"]))
            if params.get("vol_mult", 0):
                average = np.r_[np.nan, market.sma("volume", int(params["vol_window"]))[:-1]]
                signal &= market.volume > params["vol_mult"] * average
            risk = params["sl_multiplier"] * (close - lower)
            reach = (close - lower) / (upper - lower)
        else:
            signal = (previous > lower) & (close < lower) & (rsi < params["rsi_threshold"])
            risk = params["sl_multiplier"] * (upper - close)
            reach = (upper - close) / (upper - lower)
    return signal, risk, upper - lower, reach


def parse_exit(token: str, hold: int = 288) -> tuple[float, int, float, int]:
    """'tp:1.1' | 'none' | '<rule>:<param>' -> (tp, exit_rule, exit_param, max_hold)."""
    name, _, value = token.partition(":")
    if name == "tp":
        return float(value), 0, 0.0, hold
    if name == "none":
        return 0.0, 0, 0.0, hold
    return 0.0, engine.EXIT_RULES[name], float(value), hold


def run(market: Market, side: str, params: dict, exit_spec, risk, signal=None, p=0.0, seed=0,
        fee_bars=32.0):
    tp, rule, param, max_hold = exit_spec
    direction = 1 if side == "long" else -1
    if rule and side != "long":
        raise ValueError("Indicator exits are long-only")
    start = max(int(params["rsi_window"]), 1)
    if signal is None:
        signal = np.zeros(len(market.close), np.bool_)
    upper, lower = market.bands(int(params["boll_window"]), float(params["boll_window_dev"]))
    donchian = market.donchian(int(param)) if rule == engine.EXIT_RULES["donchian"] else market.nan_row()
    atr = market.atr() if rule == engine.EXIT_RULES["chandelier"] else market.nan_row()
    entries, exits, net = market.buf
    count = simulate(market.close, market.high, market.low, direction, signal, risk, start, tp,
                     max_hold, fee_bars, p, seed, entries, exits, net,
                     rule, param, market.rsi(int(params["rsi_window"])), donchian, atr, upper, lower)
    return entries[:count].copy(), exits[:count].copy(), net[:count].copy()


def quarterly(market: Market, exits, net):
    sums = np.bincount(market.quarter_of_bar[exits], weights=net, minlength=len(market.quarter_labels))
    return sums / market.quarter_months


def benchmark(market: Market, side: str, params: dict, tp, runs: int, seed: int) -> dict | None:
    """`tp` is an exit spec from parse_exit (kept positional for the earlier call sites)."""
    signal, risk, width, reach = strategy_arrays(market, side, params)
    entries, exits, net = run(market, side, params, tp, risk, signal)
    if len(net) < 30:
        return None
    k = float(np.nanmedian(reach[entries]))
    random_risk = params["sl_multiplier"] * k * width
    target = len(net)
    holding = float(np.mean(exits - entries))
    p = target / max(len(market.close) - target * holding, 1.0)
    for _ in range(4):  # calibrate the entry probability to the strategy's trade count
        counts = [len(run(market, side, params, tp, random_risk, p=p, seed=seed + s)[2]) for s in range(16)]
        p = min(p * target / max(np.mean(counts), 1.0), 1.0)
    totals, quarters, counts = [], [], []
    for s in range(runs):
        _, r_exits, r_net = run(market, side, params, tp, random_risk, p=p, seed=seed + 1000 + s)
        totals.append(r_net.sum() / market.months)
        quarters.append(quarterly(market, r_exits, r_net))
        counts.append(len(r_net))
    totals = np.asarray(totals)
    strategy = net.sum() / market.months
    return {
        "strategy_r_month": strategy,
        "random_mean": totals.mean(),
        "random_p95": float(np.quantile(totals, 0.95)),
        "edge": strategy - totals.mean(),
        "z": (strategy - totals.mean()) / totals.std() if totals.std() > 0 else np.nan,
        "percentile": float((totals < strategy).mean()),
        "beats_p95": strategy > np.quantile(totals, 0.95),
        "trades": target, "random_trades": float(np.mean(counts)), "k": k,
        "q_strategy": quarterly(market, exits, net), "q_random": np.mean(quarters, axis=0),
    }


def notebook_space(folder: str) -> dict:
    cells = json.loads((REFERENCE / folder / "train_full_grid_gpu.ipynb").read_text(encoding="utf-8"))["cells"]
    source = next("".join(c["source"]) for c in cells
                  if c["cell_type"] == "code" and "SEARCH_SPACES" in "".join(c["source"]))
    namespace: dict = {}
    exec(source, {}, namespace)
    return namespace["SEARCH_SPACES"]


def engine_space(folder: str) -> dict:
    namespace: dict = {}
    source = (REFERENCE / folder / "train_full_grid_gpu.py").read_text(encoding="utf-8")
    start = source.index("SEARCH_SPACE = {")
    exec(source[start: source.index("}\n", start) + 1], {}, namespace)
    return namespace["SEARCH_SPACE"]


LOGICS = {
    # name: (side, grid source, filter that must be on)
    "long RSI": ("long", lambda: notebook_space("long_bollinger+RSI"), None),
    "long RSI+regime": ("long", lambda: engine_space("long_bollinger+RSI+regime"), "trend_window"),
    "long RSI+volume": ("long", lambda: engine_space("long_bollinger+RSI+volume"), "vol_mult"),
    "short RSI": ("short", lambda: notebook_space("short_bollinger+RSI"), None),
}
# The first study (results/random_entry/): long logics without TP, long RSI with TP 1.1, short RSI with TP 1.1.
DEFAULT_CONFIGS = [("long RSI", "none"), ("long RSI", "tp:1.1"), ("long RSI+regime", "none"),
                   ("long RSI+volume", "none"), ("short RSI", "tp:1.1")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2022-07-01")
    parser.add_argument("--end", default="2025-06-20")
    parser.add_argument("--sets", type=int, default=200, help="Parameter sets sampled per configuration")
    parser.add_argument("--runs", type=int, default=200, help="Random twins per parameter set")
    parser.add_argument("--logics", nargs="*", choices=list(LOGICS),
                        help="With --exits: logics crossed with the exits (default: the first study's configs)")
    parser.add_argument("--exits", nargs="*", default=[],
                        help="Exit variants: tp:<ratio>, none, rsi:<level>, donchian:<bars>, chandelier:<k>, pctb:<b>")
    parser.add_argument("--hold", type=int, nargs="*", default=[288], help="max_holding_bars values")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pairs = ([(logic, token) for logic in (args.logics or list(LOGICS)) for token in args.exits]
             if args.exits else DEFAULT_CONFIGS)
    configs = [(logic, token, hold) for logic, token in pairs for hold in args.hold
               if not (LOGICS[logic][0] == "short" and token.split(":")[0] not in ("tp", "none"))]
    market = Market(args.start, args.end)
    rng = np.random.default_rng(2026)
    summary, quarter_rows = [], []
    for logic, token, hold in configs:
        side, space_fn, needed = LOGICS[logic]
        tp = parse_exit(token, hold)
        name = f"{logic}, {token}" + (f", hold {hold}" if hold != 288 else "")
        space = {k: v for k, v in space_fn().items() if k != "tp_sl_ratio"}
        grid = pd.DataFrame(itertools.product(*space.values()), columns=list(space))
        if needed:
            grid = grid[grid[needed] > 0]
        picks = grid.iloc[rng.choice(len(grid), size=min(args.sets * 3, len(grid)), replace=False)]
        rows = []
        for _, row in picks.iterrows():
            result = benchmark(market, side, row.to_dict(), tp, args.runs, seed=len(rows) * 7919)
            if result is not None:
                rows.append({**row.to_dict(), **result})
            if len(rows) == args.sets:
                break
        table = pd.DataFrame(rows)
        file_name = name.replace(" ", "_").replace(",", "").replace(":", "")
        table.drop(columns=["q_strategy", "q_random"]).to_csv(args.out / f"{file_name}.csv", index=False)
        summary.append({
            "config": name, "sets": len(table),
            "median strategy R/month": table["strategy_r_month"].median(),
            "median random R/month": table["random_mean"].median(),
            "median edge": table["edge"].median(),
            "share edge > 0": (table["edge"] > 0).mean(),
            "share beats random p95": table["beats_p95"].mean(),
            "median z": table["z"].median(),
            "median trades": table["trades"].median(),
            "median k": table["k"].median(),
        })
        q_strategy = np.vstack(table["q_strategy"].to_numpy())
        q_random = np.vstack(table["q_random"].to_numpy())
        for q, label in enumerate(market.quarter_labels):
            quarter_rows.append({"config": name, "quarter": label,
                                 "strategy": np.median(q_strategy[:, q]),
                                 "random": np.median(q_random[:, q]),
                                 "edge": np.median(q_strategy[:, q] - q_random[:, q])})
        print(f"done {name}: {len(table)} sets", flush=True)
    pd.set_option("display.width", 250)
    summary = pd.DataFrame(summary)
    summary.to_csv(args.out / "summary.csv", index=False)
    print("\n=== Strategy vs random entries (R/month, " + f"{args.start}..{args.end})")
    print(summary.round(3).to_string(index=False))
    quarters = pd.DataFrame(quarter_rows)
    quarters.to_csv(args.out / "quarters.csv", index=False)
    print("\n=== Median edge per quarter (strategy - random, R/month)")
    print(quarters.pivot(index="quarter", columns="config", values="edge").round(2).to_string())
    print("\n=== Median random-entry R/month per quarter (market drift under the same exit)")
    print(quarters.pivot(index="quarter", columns="config", values="random").round(2).to_string())


if __name__ == "__main__":
    main()
