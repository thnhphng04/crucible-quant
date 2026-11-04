"""Out-of-sample check of the pre-registered coin rule: trade a coin only when its train-window volatility is high.

Rule (fixed before the data_5m runs, INSIGHTS §9i/§9j): in each fold, a coin is *selected* when the standard
deviation of its daily log returns over the train window (the --train-months before the test start) is at least
the median of that number over every coin with a result in the same test window. Only train data is used.

For each test window it compares the test results of selected vs unselected coins (top-k, top 25%, grid median,
share of profitable sets), separately for the new coins (universe.csv, the confirmation set), the old 17 and all.
Pass (new coins): selected top-k > unselected top-k in >= 2/3 of the windows and the selected top-k mean > 0.
Run from the repository root:

    uv run python reference/results/vol_rule_eval.py reference/results/<run> --data-dir reference/data_5m
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

METRICS = ["top_k", "top_25pct", "test_median", "test_share_profit"]


def daily_returns(path: Path) -> pd.Series:
    bars = pd.read_csv(path, usecols=["timestamp", "close"], parse_dates=["timestamp"]).set_index("timestamp")
    return np.log(bars["close"].resample("1D").last().dropna()).diff().dropna()


def label_train_median(table: pd.DataFrame) -> pd.DataFrame:
    """--rule train_median: selected when the coin's whole-grid median R/month in the train window is > 0."""
    return table.assign(selected=table["train_median"] > 0)


def label(table: pd.DataFrame, data_dir: Path, train_months: int) -> pd.DataFrame:
    returns = {s: daily_returns(next(data_dir.glob(f"{s}_futures_*.csv"))) for s in table["symbol"].unique()}
    test_start = pd.to_datetime(table["test"].str[:10])
    train_start = test_start - pd.DateOffset(months=train_months)
    table = table.assign(train_vol=[returns[s].loc[a: b - pd.Timedelta(days=1)].std() * 100
                                    for s, a, b in zip(table["symbol"], train_start, test_start)])
    median = table.groupby("quarter")["train_vol"].transform("median")
    return table.assign(selected=table["train_vol"] >= median)


def report(part: pd.DataFrame, name: str) -> dict:
    by_window = part.groupby(["quarter", "selected"])[METRICS].mean().unstack("selected")
    by_window = by_window.dropna()
    if by_window.empty:
        print(f"\n== {name}: no windows with both groups")
        return {}
    wins = (by_window[("top_k", True)] > by_window[("top_k", False)])
    selected_mean = part.loc[part["selected"], "top_k"].mean()
    passed = wins.mean() >= 2 / 3 and selected_mean > 0
    print(f"\n== {name}: {part['symbol'].nunique()} coins, {len(by_window)} windows")
    print("per window, mean over coins (True = selected by train volatility):")
    print(by_window.round(3).to_string())
    print("pooled over coin-windows:")
    extra = [c for c in ("train_vol", "train_median", "market_return") if c in part]
    print(part.groupby("selected")[METRICS + extra].mean().round(3).to_string())
    print(f"selected top-k beats unselected in {int(wins.sum())}/{len(wins)} windows; selected top-k mean "
          f"{selected_mean:+.3f} -> {'PASS' if passed else 'FAIL'}")
    return {"set": name, "windows": len(wins), "wins": int(wins.sum()), "selected_top_k": selected_mean,
            "unselected_top_k": part.loc[~part["selected"], "top_k"].mean(), "pass": passed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--train-months", type=int, default=9)
    parser.add_argument("--formula", default="r_martin_monthly")
    parser.add_argument("--rule", choices=["vol", "train_median"], default="vol",
                        help="vol: train volatility >= the window's median; train_median: train whole-grid median > 0")
    args = parser.parse_args()
    pd.set_option("display.width", 250)

    table = pd.read_csv(args.run / "per_fold.csv")
    table = table[(table["formula"] == args.formula) & (table["variant"] == "all")]
    if args.rule == "vol":
        table = label(table, args.data_dir, args.train_months)
        table.to_csv(args.run / "vol_rule_labels.csv", index=False)
    else:
        table = label_train_median(table)
        print(f"selected coin-windows: {int(table['selected'].sum())}/{len(table)}")
    new = set(pd.read_csv(args.data_dir / "universe.csv")["symbol"].str.lower())
    rows = [report(table[table["symbol"].isin(new)], "new coins (confirmation)"),
            report(table[~table["symbol"].isin(new)], "old 17 coins (discovery)"),
            report(table, "all coins")]
    print("\n" + pd.DataFrame([r for r in rows if r]).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
