"""Walk-forward comparison at 15min: Bollinger breakout + RSI vs + atr_ratio squeeze vs + band_ratio squeeze.

For each side and fold: run every script's default 15min grid on the train and the test window,
then report whole-grid statistics, the out-of-sample result of the train top-K, and the rank
correlation between train and test.
"""

import importlib.util
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REFERENCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
TIMEFRAME = "15min"
MAX_HOLDING_BARS = 288
TOP_K = 10
MIN_TRADES = 50
RANK_BY_SIDE = {"long": "r_martin_monthly", "short": "r_sharpe_monthly"}
METRICS = ["performance", "number_of_trade", "r_per_month", "r_sharpe_monthly", "r_martin_monthly"]
FOLDS = [
    ("F1", "2022-07-01", "2023-12-31", "2024-01-01", "2024-06-30"),
    ("F2", "2023-01-01", "2024-06-30", "2024-07-01", "2024-12-31"),
    ("F3", "2023-07-01", "2024-12-31", "2025-01-01", "2025-06-20"),
]
STRATEGIES = {
    "long": ["long_bollinger+RSI", "long_bollinger+atr_ratio", "long_bollinger+band_ratio"],
    "short": ["short_bollinger+RSI", "short_bollinger+atr_ratio", "short_bollinger+band_ratio"],
}
PARAMS = ["boll_window", "boll_window_dev", "rsi_window", "rsi_threshold",
          "vol_fast", "vol_slow", "vol_threshold", "sl_multiplier", "tp_sl_ratio"]


def load(folder: str):
    path = REFERENCE / folder / "train_full_grid_gpu.py"
    spec = importlib.util.spec_from_file_location(folder.replace("+", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_grid(module, side: str) -> pd.DataFrame:
    return module.parameter_grid() if side == "long" else module.parameter_grid(TIMEFRAME)


def run(module, bars, grid, rank_by: str) -> pd.DataFrame:
    result, _ = module.run_gpu_grid(bars, grid, TIMEFRAME, max_holding_bars=MAX_HOLDING_BARS,
                                    score_formula=rank_by)
    return result


def families(folder: str, train: pd.DataFrame, test: pd.DataFrame):
    """Split a squeeze grid into the filtered logic and the unfiltered baseline (threshold 99)."""
    if "vol_threshold" not in train:
        yield folder.split("_", 1)[1], train, test
        return
    filtered = train["vol_threshold"] < 99
    yield folder.split("_", 1)[1], train[filtered], test[filtered]
    if folder.endswith("atr_ratio"):
        # With the filter off only the warm-up depends on vol_fast/vol_slow: keep one pair.
        base = (~filtered) & (train["vol_fast"] == 5) & (train["vol_slow"] == 60)
        yield "bollinger (không lọc)", train[base], test[base]


def stats(name: str, side: str, fold: str, train: pd.DataFrame, test: pd.DataFrame) -> dict:
    rank_by = RANK_BY_SIDE[side]
    eligible = train[(train["number_of_trade"] >= MIN_TRADES) & train[rank_by].notna()]
    top = eligible.sort_values(rank_by, ascending=False).head(TOP_K)
    top_test = test.loc[top.index]
    both = pd.concat([train[rank_by].rename("train"), test[rank_by].rename("test")], axis=1).dropna()
    return {
        "side": side, "logic": name, "fold": fold, "rank_by": rank_by, "combos": len(train),
        "train_med_r_month": train["r_per_month"].median(),
        "train_share_profit": (train["performance"] > 0).mean(),
        "test_med_r_month": test["r_per_month"].median(),
        "test_share_profit": (test["performance"] > 0).mean(),
        "test_med_trades": test["number_of_trade"].median(),
        "top_train_r_month": top["r_per_month"].mean(),
        "top_test_r_month": top_test["r_per_month"].mean(),
        "top_test_share_profit": (top_test["performance"] > 0).mean(),
        "top_test_trades": top_test["number_of_trade"].mean(),
        "spearman": both["train"].corr(both["test"], method="spearman"),
        "eligible": len(eligible),
    }


rows = []
for side, folders in STRATEGIES.items():
    for folder in folders:
        module = load(folder)
        grid = default_grid(module, side)
        csv_path = REFERENCE / f"{side}_bollinger+RSI" / "avaxusdt_futures_5min.csv"
        for fold, train_start, train_end, test_start, test_end in FOLDS:
            train_bars = module.load_bars(csv_path, TIMEFRAME, train_start, train_end)
            test_bars = module.load_bars(csv_path, TIMEFRAME, test_start, test_end)
            train = run(module, train_bars, grid, RANK_BY_SIDE[side])
            test = run(module, test_bars, grid, RANK_BY_SIDE[side])
            keep = [column for column in PARAMS if column in train]
            saved = train[keep + METRICS].join(test[METRICS], rsuffix="_test")
            saved.to_csv(OUT / f"{folder}_{fold}.csv", index=False)
            for name, train_part, test_part in families(folder, train, test):
                rows.append(stats(name, side, fold, train_part, test_part))
            print(f"{folder} {fold}: train {len(train_bars)} bars, test {len(test_bars)} bars, "
                  f"{len(grid)} combos", flush=True)

table = pd.DataFrame(rows)
table.to_csv(OUT / "walk_forward_15min_summary.csv", index=False)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
print("\n=== Per fold")
print(table.round(3).to_string(index=False))
print("\n=== Mean over the three folds")
summary = table.drop(columns=["fold", "rank_by"]).groupby(["side", "logic"], sort=False).mean(numeric_only=True)
print(summary.round(3).to_string())
