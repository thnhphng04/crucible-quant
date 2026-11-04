"""Why does 24m/6m pick better top-10 short trend sets than 18m/6m? Controlled study on AVAX 5min.

Same grid (the short_bollinger+RSI+trend notebook's), same 6-month test windows 2023-01 -> 2026-10, train
lengths 6/9/12/18/24 months, several score formulas. A test window is run on its own, so a parameter set has
the same test result whatever the train length: only the selection differs. Every GPU run is cached as a
parquet file in --out/runs, so re-running the analysis is cheap. Run from the repository root:

    uv run python reference/results/train_len_study.py --out reference/results/train_len_study
"""

from __future__ import annotations

import argparse
import itertools
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REFERENCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REFERENCE))
import walk_forward_15min as wf  # noqa: E402
from walk_forward_multi_asset import window  # noqa: E402

warnings.filterwarnings("ignore")
FOLDER = "short_bollinger+RSI+trend"
KEEP = ["number_of_trade", "performance", "max_dd", "r2", "r_per_month", "r_martin_monthly", "r_sharpe_monthly",
        "ulcer_r", "win_rate", "avg_win_r", "avg_loss_r", "wilson_p_lower", "wilson_fitness", "min_trades_per_year",
        "number_of_win", "mtm_max_dd", "avg_dd", "max_concurrent", "avg_concurrent"]
FORMULAS = ["r_sharpe_monthly", "r_martin_monthly", "performance", "performance/max_dd",
            "performance/number_of_trade*r2**5", "wilson_fitness", "r2"]
TRAIN_MONTHS = [6, 9, 12, 18, 24]
TOP_KS = [1, 5, 10, 25, 50, 100]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=REFERENCE / "data_5m" / "avaxusdt_futures_5min.csv")
    parser.add_argument("--timeframe", default="5min")
    parser.add_argument("--first-test", default="2023-01-01")
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs_dir = args.out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    module = wf.load_strategy(FOLDER)
    space, _ = wf.grid_and_formula(FOLDER, module)
    grid = pd.DataFrame(itertools.product(*space.values()), columns=list(space))
    bars = module.load_bars(args.data, args.timeframe, None, None)
    data_end = bars["timestamp"].iloc[-1].normalize()
    print(f"{len(grid):,} combos; {len(bars):,} bars to {data_end.date()}", flush=True)

    def run(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        path = runs_dir / f"{s}_{e}.parquet"
        if path.is_file():
            return pd.read_parquet(path)
        result, _ = module.run_gpu_grid(window(bars, s, e), grid, args.timeframe)
        result = module.add_wilson_columns(result, 0.9)
        frame = result[[c for c in KEEP if c in result]].astype("float64")
        frame.to_parquet(path)
        print(f"  ran {s}..{e}", flush=True)
        return frame

    tests = []
    for test_start in pd.date_range(args.first_test, data_end, freq="6MS"):
        test_end = min(test_start + pd.DateOffset(months=6) - pd.Timedelta(days=1), data_end)
        if (test_end - test_start).days + 1 >= 30:
            tests.append((test_start, test_end))

    rows, picks = [], []
    for fold, (test_start, test_end) in enumerate(tests, 1):
        test = run(test_start, test_end)
        for months in TRAIN_MONTHS:
            train = run(test_start - pd.DateOffset(months=months), test_start - pd.Timedelta(days=1))
            # fixed 100 trades (as the harness) and the same bar scaled to the train length (100 per 18 months)
            for rule, floor in (("fixed", args.min_trades), ("scaled", args.min_trades * months / 18)):
                eligible = train["number_of_trade"] >= floor
                for formula in FORMULAS:
                    score = module.score_result(train, formula)["score"].where(eligible)
                    order = score.dropna().sort_values(ascending=False).index
                    row = {"fold": f"F{fold}", "test": f"{test_start.date()}..{test_end.date()}",
                           "train_months": months, "eligibility": rule, "formula": formula,
                           "eligible": len(order),
                           "eligible_mean": test.loc[order, "r_per_month"].mean(),
                           "grid_median": test["r_per_month"].median(),
                           "spearman": score.corr(test["r_per_month"], method="spearman")}
                    for k in TOP_KS:
                        row[f"top{k}"] = test.loc[order[:k], "r_per_month"].mean()
                    row["top25pct"] = test.loc[order[: max(1, len(order) // 4)], "r_per_month"].mean()
                    rows.append(row)
                    if rule == "fixed":
                        for rank, index in enumerate(order[:10], 1):
                            picks.append({"fold": f"F{fold}", "train_months": months, "formula": formula,
                                          "rank": rank, "set": int(index), **grid.loc[index].to_dict(),
                                          "train_r_per_month": train.loc[index, "r_per_month"],
                                          "train_trades": train.loc[index, "number_of_trade"],
                                          "train_sharpe": train.loc[index, "r_sharpe_monthly"],
                                          "test_r_per_month": test.loc[index, "r_per_month"],
                                          "test_trades": test.loc[index, "number_of_trade"]})
    table = pd.DataFrame(rows)
    table.to_csv(args.out / "per_fold.csv", index=False)
    pd.DataFrame(picks).to_csv(args.out / "top10_picks.csv", index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    fixed = table[table["eligibility"] == "fixed"]
    for metric in ("top10", "top1", "top25pct", "spearman"):
        print(f"\n=== {metric}: mean over {len(tests)} test windows (R/month; fixed {args.min_trades} trades)")
        print(fixed.pivot_table(index="formula", columns="train_months", values=metric).round(3).to_string())
    print("\n=== top10, scaled trade floor")
    scaled = table[table["eligibility"] == "scaled"]
    print(scaled.pivot_table(index="formula", columns="train_months", values="top10").round(3).to_string())
    print("\n=== r_sharpe_monthly, top-k by train length (fixed floor)")
    sharpe = fixed[fixed["formula"] == "r_sharpe_monthly"]
    print(sharpe.groupby("train_months")[[f"top{k}" for k in TOP_KS] + ["top25pct", "eligible_mean"]]
          .mean().round(3).to_string())
    print("\n=== r_sharpe_monthly top10 per fold")
    print(sharpe.pivot(index="fold", columns="train_months", values="top10").round(2).to_string())
    paired = (sharpe.pivot(index="fold", columns="train_months", values="top10"))
    diff = paired[24] - paired[18]
    print(f"\n24m - 18m top10 (sharpe): mean {diff.mean():+.3f}, sd {diff.std():.3f}, "
          f"t {diff.mean() / (diff.std() / np.sqrt(len(diff))):.2f}, folds 24m better {int((diff > 0).sum())}/{len(diff)}")
    every = fixed.pivot_table(index=["formula", "fold"], columns="train_months", values="top10")
    d = (every[24] - every[18]).groupby("formula")
    print("24m - 18m top10 per formula:")
    print(pd.DataFrame({"mean": d.mean(), "folds_better": d.apply(lambda s: f"{int((s > 0).sum())}/{len(s)}")})
          .round(3).to_string())


if __name__ == "__main__":
    main()
