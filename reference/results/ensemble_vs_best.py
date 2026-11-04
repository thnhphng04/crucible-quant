"""Out-of-sample R/month: best-1, top-10, a broad group, a plateau centre, and the whole grid."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

WF = Path(sys.argv[1])
FOLDERS = ["long_bollinger+RSI", "long_bollinger+atr_ratio", "long_bollinger+band_ratio",
           "short_bollinger+RSI", "short_bollinger+atr_ratio", "short_bollinger+band_ratio"]
MIN_TRADES = 50
rows = []
for folder in FOLDERS:
    for fold in ("F1", "F2", "F3"):
        df = pd.read_csv(WF / f"{folder}_{fold}.csv")
        if "vol_threshold" in df:
            df = df[df["vol_threshold"] < 99]
        params = [c for c in ("boll_window", "rsi_window", "rsi_threshold", "vol_fast", "vol_slow",
                              "vol_threshold", "sl_multiplier") if c in df and df[c].nunique() > 1]
        rank_by = "r_martin_monthly" if folder.startswith("long") else "r_sharpe_monthly"
        eligible = df[(df["number_of_trade"] >= MIN_TRADES) & df[rank_by].notna()]
        ranked = eligible.sort_values(rank_by, ascending=False)

        # Plateau centre: average each set's train Sharpe with its neighbours one grid step
        # away in every parameter, then pick the best smoothed value.
        levels = {p: sorted(df[p].unique()) for p in params}
        position = {p: df[p].map({v: k for k, v in enumerate(levels[p])}) for p in params}
        keys = pd.DataFrame(position)
        lookup = dict(zip(map(tuple, keys.to_numpy()), df[rank_by].to_numpy()))
        smoothed = []
        for key in map(tuple, keys.to_numpy()):
            values = []
            for offsets in np.ndindex(*(3,) * len(params)):
                neighbour = tuple(k + o - 1 for k, o in zip(key, offsets))
                value = lookup.get(neighbour)
                if value is not None and np.isfinite(value):
                    values.append(value)
            smoothed.append(np.mean(values) if values else np.nan)
        df = df.assign(smoothed=smoothed)
        centre = df.loc[df.loc[eligible.index, "smoothed"].idxmax()]

        rows.append({
            "strategy": folder.split("_", 1)[0] + " " + folder.split("+", 1)[1], "fold": fold,
            "best_1": ranked["r_per_month_test"].iloc[0],
            "top_10": ranked["r_per_month_test"].head(10).mean(),
            "top_25pct": ranked["r_per_month_test"].head(len(ranked) // 4).mean(),
            "plateau_centre": centre["r_per_month_test"],
            "whole_grid": df["r_per_month_test"].mean(),
            "best_1_train": ranked["r_per_month"].iloc[0],
        })

table = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(table.round(2).to_string(index=False))
print()
print(table.drop(columns="fold").groupby("strategy", sort=False).agg(["mean", "std"]).round(2).to_string())
