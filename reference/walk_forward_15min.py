"""Rolling walk-forward over the reference strategy folders.

For each folder: read the grid and SCORE_FORMULA from its notebook's configuration cell (or the
script's SEARCH_SPACE / DEFAULT_SCORE when there is no notebook), run every train and test window on
the GPU, and report, per fold and averaged: the whole-grid test median R/month and share profitable,
the test result of the train best-1 / top-k / top 25%, and the train-test Spearman rank correlation.
Rows whose filter is switched off (vol_threshold 99, trend_window 0, vol_mult 0) form a separate
"(filter off)" line. Run from the repository root:

    uv run python reference/walk_forward_15min.py --folders long_bollinger+RSI short_bollinger+RSI --fee-bars 32
    uv run python reference/walk_forward_15min.py --folders long_bollinger+RSI+regime --fee-bars 32 \\
        --exits tp:1.1 none rsi:50 donchian:50 --set max_holding_bars=288,1152 \\
        --split-by exit_rule,max_holding_bars --split-by exit_rule,exit_param,max_holding_bars
    uv run python reference/walk_forward_15min.py --folders multi_position/short_bollinger+RSI \\
        --fee-bars 32 --set max_open=1,64 --split-by max_open
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import re
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
REFERENCE = Path(__file__).resolve().parent
DATA_END = "2025-06-20"
FILTER_OFF = {"vol_threshold": 99, "trend_window": 0, "vol_mult": 0}
# Columns that do nothing while the filter is off: keep one value so the baseline has no duplicates.
INACTIVE_WHEN_OFF = {"vol_threshold": ("vol_fast", "vol_slow"), "trend_window": (), "vol_mult": ("vol_window",)}


def folds(train_months: int, test_months: int, first_test: str, last_test: str) -> list[tuple[str, ...]]:
    out = []
    for k, test_start in enumerate(pd.date_range(first_test, last_test, freq=f"{test_months}MS"), 1):
        train_start = test_start - pd.DateOffset(months=train_months)
        test_end = min(test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1),
                       pd.Timestamp(DATA_END))
        out.append((f"F{k}", *(d.strftime("%Y-%m-%d") for d in
                               (train_start, test_start - pd.Timedelta(days=1), test_start, test_end))))
    return out


def load_strategy(folder: str):
    spec = importlib.util.spec_from_file_location(re.sub(r"\W+", "_", folder), REFERENCE / folder / "train_full_grid_gpu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grid_and_formula(folder: str, module) -> tuple[dict, str]:
    notebook = REFERENCE / folder / "train_full_grid_gpu.ipynb"
    if notebook.is_file():
        cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
        source = next("".join(c["source"]) for c in cells
                      if c["cell_type"] == "code" and "SEARCH_SPACES" in "".join(c["source"]))
        namespace: dict = {}
        exec(source, {}, namespace)
        return namespace["SEARCH_SPACES"], namespace["SCORE_FORMULA"]
    return module.SEARCH_SPACE, module.DEFAULT_SCORE


def families(grid: pd.DataFrame) -> dict[str, pd.Series]:
    """'logic' rows (filter on) and, when the grid has a switch-off value, '(filter off)' rows."""
    for column, value in FILTER_OFF.items():
        if column in grid:
            off = grid[column] == value
            for inactive in INACTIVE_WHEN_OFF[column]:
                off &= grid[inactive] == grid[inactive].min()
            return {"": grid[column] != value, " (filter off)": off}
    return {"": pd.Series(True, index=grid.index)}


EXIT_RULES = {"none": 0, "rsi": 1, "donchian": 2, "chandelier": 3, "pctb": 4}  # long_logic_engine.EXIT_RULES


def exit_variants(tokens: list[str]) -> pd.DataFrame:
    """Rows of (tp_sl_ratio, exit_rule, exit_param): the meaning of exit_param depends on the rule."""
    rows = []
    for token in tokens:
        name, _, value = token.partition(":")
        if name == "tp":
            rows.append({"tp_sl_ratio": float(value), "exit_rule": 0, "exit_param": 0.0})
        elif name == "none":
            rows.append({"tp_sl_ratio": 0.0, "exit_rule": 0, "exit_param": 0.0})
        elif name in EXIT_RULES:
            rows.append({"tp_sl_ratio": 0.0, "exit_rule": EXIT_RULES[name], "exit_param": float(value)})
        else:
            raise ValueError(f"Unknown exit variant {token!r}")
    return pd.DataFrame(rows)


def evaluate(train: pd.DataFrame, test: pd.DataFrame, min_trades: int, top_k: int) -> dict:
    eligible = train[(train["number_of_trade"] >= min_trades) & train["score"].notna()]
    order = eligible.sort_values("score", ascending=False).index
    both = pd.concat([train["score"], test["score"]], axis=1).dropna()
    row = {
        "test_median": test["r_per_month"].median(),
        "test_share_profit": (test["performance"] > 0).mean(),
        "eligible": len(order),
        "spearman": both.iloc[:, 0].corr(both.iloc[:, 1], method="spearman"),
    }
    if len(order):
        row.update({
            "best_1": test.loc[order[0], "r_per_month"],
            "best_1_train": train.loc[order[0], "r_per_month"],
            "top_k": test.loc[order[:top_k], "r_per_month"].mean(),
            "top_25pct": test.loc[order[: max(1, len(order) // 4)], "r_per_month"].mean(),
            "top_k_test_trades": test.loc[order[:top_k], "number_of_trade"].mean(),
        })
    # Scale-free views (unchanged when every trade's size is scaled): closed-trade Martin and, for
    # engines that report it, R/month over the mark-to-market max drawdown.
    views = {"martin": test["r_martin_monthly"]}
    if "mtm_max_dd" in test:
        views["calmar_mtm"] = test["r_per_month"] / test["mtm_max_dd"].where(test["mtm_max_dd"] > 0)
    for name, values in views.items():
        row[f"test_median_{name}"] = values.median()
        if len(order):
            row[f"top_k_{name}"] = values.loc[order[:top_k]].mean()
            row[f"top_25pct_{name}"] = values.loc[order[: max(1, len(order) // 4)]].mean()
    if "max_concurrent" in test and len(order):
        row["top_k_max_concurrent"] = test.loc[order[:top_k], "max_concurrent"].mean()
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folders", nargs="+", required=True)
    parser.add_argument("--fee-bars", type=float, help="Override every script's holding-fee bars")
    parser.add_argument("--timeframe", default="15min")
    parser.add_argument("--train-months", type=int, default=9)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--first-test", default="2023-04-01")
    parser.add_argument("--last-test", default="2025-04-01")
    parser.add_argument("--min-trades", type=int, default=25)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--set", action="append", default=[], metavar="COLUMN=V1,V2",
                        help="Replace (or add) a grid column's values, e.g. tp_sl_ratio=1.1,2,3")
    parser.add_argument("--split-by", action="append", default=[], metavar="COL[,COL...]",
                        help="Also report one line per value combination of these columns; repeat "
                             "for several groupings (e.g. exit_rule and exit_rule,exit_param)")
    parser.add_argument("--exits", nargs="*", default=[],
                        help="Exit variants crossed with the entry grid: tp:<ratio>, none (no take "
                             "profit), rsi:<level>, donchian:<bars>, chandelier:<k>, pctb:<b> "
                             "(engine scripts only); replaces the grid's tp_sl_ratio")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    overrides = {}
    for item in args.set:
        column, _, values = item.partition("=")
        overrides[column] = [float(v) if "." in v else int(v) for v in values.split(",")]
    args.out.mkdir(parents=True, exist_ok=True)
    schedule = folds(args.train_months, args.test_months, args.first_test, args.last_test)

    rows = []
    for folder in args.folders:
        module = load_strategy(folder)
        space, formula = grid_and_formula(folder, module)
        space = {**space, **overrides}
        if args.exits:
            space.pop("tp_sl_ratio", None)
        grid = pd.DataFrame(itertools.product(*space.values()), columns=list(space))
        if args.exits:
            grid = grid.merge(exit_variants(args.exits), how="cross")
        side = Path(folder).name.split("_", 1)[0]
        csv_path = REFERENCE / f"{side}_bollinger+RSI" / "avaxusdt_futures_5min.csv"
        groups = families(grid)
        if args.split_by:
            base_groups = list(groups.items())
            for split in args.split_by:
                columns = split.split(",")
                for suffix, mask in base_groups:
                    for key, part in grid[mask].groupby(columns):
                        key = key if isinstance(key, tuple) else (key,)
                        label_part = ", ".join(f"{c}={v:g}" for c, v in zip(columns, key))
                        groups[f"{suffix} [{label_part}]"] = grid.index.isin(part.index)
            groups = {k: pd.Series(v, index=grid.index) for k, v in groups.items()}
        label = folder.replace("_bollinger+", " ")
        print(f"{folder}: {len(grid):,} combos, score {formula!r}, fee_bars {args.fee_bars or 'script default'}",
              flush=True)

        def run(start: str, end: str) -> pd.DataFrame:
            bars = module.load_bars(csv_path, args.timeframe, start, end)
            result, _ = module.run_gpu_grid(bars, grid, args.timeframe, max_holding_bars=args.max_holding_bars,
                                            fee_bars=args.fee_bars, score_formula=formula)
            return result

        for fold, train_start, train_end, test_start, test_end in schedule:
            train, test = run(train_start, train_end), run(test_start, test_end)
            for suffix, mask in groups.items():
                rows.append({"strategy": label + suffix, "fold": fold, "test": f"{test_start}..{test_end}",
                             "combos": int(mask.sum()),
                             **evaluate(train[mask], test[mask], args.min_trades, args.top_k)})
            print(f"  {fold} test {test_start}..{test_end}", flush=True)

    table = pd.DataFrame(rows)
    table.to_csv(args.out / "per_fold.csv", index=False)
    metrics = ["test_median", "test_share_profit", "best_1", "top_k", "top_25pct", "spearman"]
    grouped = table.groupby("strategy", sort=False)
    extra = [c for c in table.columns if c.endswith(("_martin", "_calmar_mtm")) or c == "top_k_max_concurrent"]
    summary = grouped[metrics + ["best_1_train", "top_k_test_trades"] + extra].mean()
    for column in ("test_median", "best_1", "top_k", "top_25pct"):
        summary[f"{column}_pos"] = grouped[column].apply(lambda s: f"{(s > 0).sum()}/{s.notna().sum()}")
    summary.insert(0, "combos", grouped["combos"].first())
    summary.to_csv(args.out / "summary.csv")
    pd.set_option("display.width", 260)
    print("\n=== Mean over folds (test R/month)")
    print(summary.round(3).to_string())
    unsplit = table[~table["strategy"].str.contains(r"\[", regex=True)]
    print("\n=== top_k per fold")
    print(unsplit.pivot(index="fold", columns="strategy", values="top_k").round(2).to_string())
    print("\n=== whole-grid test median per fold")
    print(unsplit.pivot(index="fold", columns="strategy", values="test_median").round(2).to_string())


if __name__ == "__main__":
    main()
