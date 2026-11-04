"""Paired drawdown comparison across exits (same entry parameters, only the exit differs).

1. Whole grid on the GPU, full data: closed-trade max_dd / ulcer_r / performance per exit.
2. A random sample of entry parameter sets replayed on the CPU with mark-to-market equity (every bar's
   close values the open trade), which also counts open profit given back before the exit.
"""

import importlib.util
import itertools
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REFERENCE = Path(sys.argv[1]).resolve()
START, END = "2022-07-01", "2025-06-20"
EXITS = {"TP 1.1": 1.1, "TP 3": 3.0, "TP 5": 5.0, "no TP": 0.0}
SAMPLE = int(sys.argv[2]) if len(sys.argv) > 2 else 120
pd.set_option("display.width", 220)


def load(folder):
    spec = importlib.util.spec_from_file_location(folder.replace("+", "_"), REFERENCE / folder / "train_full_grid_gpu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mtm_drawdown(bars, trades, months):
    """Max drawdown and ulcer (daily) of bar-by-bar mark-to-market equity, in R, fees included."""
    close = bars["close"].to_numpy(dtype=np.float64)
    equity = np.zeros(len(close))
    realized = 0.0
    position = 0
    start = 0
    for row in trades.itertuples():
        equity[start:row.entry_index + 1] = realized
        risk = row.entry_price - row.stop_loss
        path = (close[row.entry_index + 1:row.exit_index] - row.entry_price) / risk
        equity[row.entry_index + 1:row.exit_index] = realized - row.entry_fee + path
        realized += row.net_r
        equity[row.exit_index] = realized
        start = row.exit_index + 1
    equity[start:] = realized
    peak = np.maximum.accumulate(np.maximum(equity, 0.0))
    drawdown = peak - equity
    daily = pd.Series(drawdown, index=bars["timestamp"]).resample("1D").last().dropna()
    return drawdown.max(), float(np.sqrt((daily ** 2).mean())), realized / months


for folder, base_filter in (("long_bollinger+RSI+regime", {"trend_window": 0}),
                            ("long_bollinger+RSI+regime", None),
                            ("long_bollinger+RSI+volume", None)):
    module = load(folder)
    label = folder.replace("long_bollinger+", "long ") + (" (filter off = long RSI)" if base_filter else "")
    bars = module.load_bars(module.DEFAULT_CSV, "15min", START, END)
    months = (bars["timestamp"].iloc[-1] - bars["timestamp"].iloc[0]).days / (365.2425 / 12)
    entry = pd.DataFrame(itertools.product(*module.SEARCH_SPACE.values()), columns=list(module.SEARCH_SPACE))
    entry = entry.drop(columns="tp_sl_ratio")
    if base_filter:
        for column, value in base_filter.items():
            entry = entry[entry[column] == value]
    elif "trend_window" in entry:
        entry = entry[entry["trend_window"] > 0]
    elif "vol_mult" in entry:
        entry = entry[entry["vol_mult"] > 0]
    entry = entry.reset_index(drop=True)

    # 1. closed-trade metrics, whole grid, paired by entry row
    closed = {}
    for name, tp in EXITS.items():
        result, _ = module.run_gpu_grid(bars, entry.assign(tp_sl_ratio=tp), "15min", fee_bars=32.0)
        closed[name] = result
    base = closed["TP 1.1"]
    rows = []
    for name, result in closed.items():
        ok = (result["number_of_trade"] >= 30) & (base["number_of_trade"] >= 30)
        rows.append({
            "exit": name,
            "median R/month": result.loc[ok, "r_per_month"].median(),
            "median max_dd (R)": result.loc[ok, "max_dd"].median(),
            "median ulcer (R)": result.loc[ok, "ulcer_r"].median(),
            "median max_dd ratio vs TP1.1": (result.loc[ok, "max_dd"] / base.loc[ok, "max_dd"]).median(),
            "share DD larger than TP1.1": (result.loc[ok, "max_dd"] > base.loc[ok, "max_dd"]).mean(),
            "median return/maxDD": (result.loc[ok, "performance"] / result.loc[ok, "max_dd"]).median(),
            "median martin": result.loc[ok, "r_martin_monthly"].median(),
            "median trades": result.loc[ok, "number_of_trade"].median(),
        })
    print(f"\n===== {label}: {len(entry):,} entry sets, closed-trade metrics, {START}..{END}")
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    # 2. mark-to-market on a random sample of entry sets
    eligible = entry.index[(base["number_of_trade"] >= 30).to_numpy()]
    sample = np.random.default_rng(11).choice(eligible, size=min(SAMPLE, len(eligible)), replace=False)
    mtm = []
    for index in sample:
        params = entry.loc[index].to_dict()
        for name, tp in EXITS.items():
            trades = module.cpu_trace(bars, {**params, "tp_sl_ratio": tp}, "15min", fee_bars=32.0)
            max_dd, ulcer, r_month = mtm_drawdown(bars, trades, months)
            mtm.append({"set": index, "exit": name, "mtm_max_dd": max_dd, "mtm_ulcer": ulcer,
                        "r_month": r_month, "closed_max_dd": float(closed[name].loc[index, "max_dd"])})
    mtm = pd.DataFrame(mtm)
    pivot = mtm.pivot(index="set", columns="exit", values="mtm_max_dd")
    summary = mtm.groupby("exit", sort=False).agg(
        median_mtm_max_dd=("mtm_max_dd", "median"), median_closed_max_dd=("closed_max_dd", "median"),
        median_mtm_ulcer=("mtm_ulcer", "median"), median_r_month=("r_month", "median"))
    summary["mtm/closed DD"] = summary["median_mtm_max_dd"] / summary["median_closed_max_dd"]
    summary["median MTM DD ratio vs TP1.1"] = [(pivot[e] / pivot["TP 1.1"]).median() for e in summary.index]
    summary["share MTM DD > TP1.1"] = [(pivot[e] > pivot["TP 1.1"]).mean() for e in summary.index]
    r = mtm.pivot(index="set", columns="exit", values="r_month")
    summary["median (R/month)/(MTM DD)"] = [(r[e] / pivot[e]).median() for e in summary.index]
    print(f"--- mark-to-market, random sample of {len(sample)} entry sets")
    print(summary.round(3).to_string())
