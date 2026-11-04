"""Group coins into good / neutral / bad for one walk-forward run of walk_forward_multi_asset.py, out of sample.

Per coin (ranking --formula, variant 'all'): mean test R/month of the whole-grid median, the top 25% and the top-k
train picks, the share of test windows where top-k > 0, and the mean market return of the test windows.
Groups: good = top-k mean > 0, top 25% mean > 0 and top-k > 0 in at least half the windows; bad = top-k mean < 0
and top 25% mean < 0; neutral = the rest.

Persistence (--split QUARTER): label the coins on the test windows before QUARTER only, then report how each
group did on the windows from QUARTER on, plus the rank correlation of the coins' top-k between the two halves.
A grouping is only useful if it holds in the later half. Run from the repository root:

    uv run python reference/results/group_coins.py reference/results/<run> --split 2025-Q1
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def per_coin(table: pd.DataFrame) -> pd.DataFrame:
    grouped = table.groupby("symbol")
    out = pd.DataFrame({
        "windows": grouped["fold"].count(),
        "grid_median": grouped["test_median"].mean(),
        "top_25pct": grouped["top_25pct"].mean(),
        "top_k": grouped["top_k"].mean(),
        "top_k_pos": grouped["top_k"].apply(lambda s: (s > 0).mean()),
        "top10_sets_profitable": grouped["top10_share_pos"].mean(),
        "market": grouped["market_return"].mean(),
    })
    good = (out["top_k"] > 0) & (out["top_25pct"] > 0) & (out["top_k_pos"] >= 0.5)
    bad = (out["top_k"] < 0) & (out["top_25pct"] < 0)
    out["group"] = np.select([good, bad], ["good", "bad"], "neutral")
    return out.sort_values("top_k", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path, help="Output folder of walk_forward_multi_asset.py")
    parser.add_argument("--formula", default="r_martin_monthly")
    parser.add_argument("--split", help="First quarter of the later half, e.g. 2025-Q1")
    args = parser.parse_args()

    table = pd.read_csv(args.run / "per_fold.csv")
    table = table[(table["formula"] == args.formula) & (table["variant"] == "all")]
    pd.set_option("display.width", 220)
    whole = per_coin(table)
    print(f"===== {args.run.name}: all test windows ({args.formula})")
    print(whole.round(2).to_string())
    print("\ngroup means:")
    print(whole.groupby("group")[["grid_median", "top_25pct", "top_k", "top10_sets_profitable", "market"]]
          .agg(["mean", "count"]).round(2).to_string())
    print(f"\ncorr across coins: top_k vs market {whole['top_k'].corr(whole['market']):+.2f}, "
          f"top_25pct vs market {whole['top_25pct'].corr(whole['market']):+.2f}")
    whole.to_csv(args.run / "coin_groups.csv")

    if args.split:
        early = per_coin(table[table["quarter"] < args.split])
        late = per_coin(table[table["quarter"] >= args.split])
        both = early[["group", "top_k", "top_25pct"]].join(
            late[["top_k", "top_25pct", "grid_median", "market", "windows"]], lsuffix="_early", rsuffix="_late",
            how="inner")
        print(f"\n===== persistence: groups from windows before {args.split}, results from {args.split} on")
        print(both.sort_values("top_k_early", ascending=False).round(2).to_string())
        print("\nlater-half results by early group:")
        print(both.groupby("group")[["top_k_late", "top_25pct_late", "grid_median", "market"]]
              .agg(["mean", "count"]).round(2).to_string())
        print(f"\nrank corr of coins early vs late: top_k {both['top_k_early'].corr(both['top_k_late'], 'spearman'):+.2f}, "
              f"top_25pct {both['top_25pct_early'].corr(both['top_25pct_late'], 'spearman'):+.2f}")


if __name__ == "__main__":
    main()
