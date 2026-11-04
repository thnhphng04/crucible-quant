"""Rolling walk-forward of one reference strategy folder over every coin in a data directory.

The grid and SCORE_FORMULA come from the folder's notebook (as in walk_forward_15min.py). Per coin: load
the CSV once (5min or 15min bars, resampled to --timeframe), build calendar-quarter test windows (the first
one is the first quarter with --train-months of data before it; a last window shorter than
--min-test-days is dropped), run the whole grid on the GPU for every train and test window, then rank the
train result once per --score-formulas entry and evaluate the test result with walk_forward_15min.evaluate.
Run from the repository root:

    uv run python reference/walk_forward_multi_asset.py --data-dir reference/data \\
        --folder short_bollinger+RSI --fee-bars 32 \\
        --score-formulas r_martin_monthly "performance/max_dd*r2**5" --out reference/results/<dir>
    # the old AVAX schedule (reproduces results/wf_9m3m_martin's short RSI line)
    uv run python reference/walk_forward_multi_asset.py --data-dir reference/short_bollinger+RSI \\
        --folder short_bollinger+RSI --fee-bars 32 --first-test 2023-04-01 --last-test 2025-04-01 \\
        --data-end 2025-06-20 --score-formulas r_martin_monthly --out <dir>
"""

from __future__ import annotations

import argparse
import itertools
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import walk_forward_15min as wf  # noqa: E402

warnings.filterwarnings("ignore")
METRICS = ["test_median", "test_share_profit", "best_1", "top_k", "top_25pct", "spearman",
           "test_median_martin", "top_k_martin", "top_25pct_martin", "top_k_test_trades"]


def schedule(data_start: pd.Timestamp, data_end: pd.Timestamp, train_months: int, test_months: int,
             first_test: str | None, last_test: str | None, min_test_days: int) -> list[tuple[str, ...]]:
    """(fold, train_start, train_end, test_start, test_end) with test windows on calendar quarters."""
    if first_test is None:
        earliest = data_start.normalize() + pd.DateOffset(months=train_months)
        first = earliest.to_period("Q").start_time
        if first < earliest:
            first += pd.DateOffset(months=3)
    else:
        first = pd.Timestamp(first_test)
    last = pd.Timestamp(last_test) if last_test else data_end
    out = []
    for test_start in pd.date_range(first, last, freq=f"{test_months}MS"):
        test_end = min(test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), data_end)
        if (test_end - test_start).days + 1 < min_test_days:
            continue
        train_start = test_start - pd.DateOffset(months=train_months)
        out.append((f"F{len(out) + 1}", *(d.strftime("%Y-%m-%d") for d in
                                          (train_start, test_start - pd.Timedelta(days=1), test_start, test_end))))
    return out


def window(bars: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Rows from the start date to the end of the end date, as the scripts' load_bars filters."""
    stamps = bars["timestamp"]
    end_of_day = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return bars[(stamps >= pd.Timestamp(start)) & (stamps <= end_of_day)].reset_index(drop=True)


def top_n_stats(train: pd.DataFrame, test: pd.DataFrame, min_trades: int, sizes: list[int]) -> dict:
    """For the N best train sets (same eligibility as evaluate): median and mean test R/month and the
    share of those N sets that are profitable in the test window."""
    eligible = train[(train["number_of_trade"] >= min_trades) & train["score"].notna()]
    order = eligible.sort_values("score", ascending=False).index
    row = {}
    for n in sizes:
        chosen = test.loc[order[:n], "r_per_month"]
        row[f"top{n}_count"] = len(chosen)
        row[f"top{n}_median"] = chosen.median() if len(chosen) else float("nan")
        row[f"top{n}_mean"] = chosen.mean() if len(chosen) else float("nan")
        row[f"top{n}_share_pos"] = (chosen > 0).mean() if len(chosen) else float("nan")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Folder of <symbol>_futures_*.csv files")
    parser.add_argument("--symbols", nargs="*", help="Subset of symbols (file name before '_futures')")
    parser.add_argument("--folder", default="short_bollinger+RSI", help="Strategy folder under reference/")
    parser.add_argument("--score-formulas", nargs="+", help="Train rankings (default: the notebook's)")
    parser.add_argument("--fee-bars", type=float)
    parser.add_argument("--timeframe", default="15min")
    parser.add_argument("--train-months", type=int, default=9)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--first-test", help="Override the per-coin first test start")
    parser.add_argument("--last-test", help="Override the last test start")
    parser.add_argument("--data-end", help="Override the last data day")
    parser.add_argument("--min-test-days", type=int, default=30)
    parser.add_argument("--max-folds", type=int, help="Only the first N folds per coin (smoke test)")
    parser.add_argument("--min-trades", type=int, default=25)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--top-n", type=int, nargs="*", default=[10, 100],
                        help="Also report median test R/month and share profitable of the N best train sets")
    parser.add_argument("--max-holding-bars", type=int, default=288)
    parser.add_argument("--save-top", type=int, default=0,
                        help="Write the N best train sets of every fold (whole grid) to top_sets.csv")
    parser.add_argument("--split-by", action="append", default=[], metavar="COL[,COL]",
                        help="Also evaluate each value (combination) of these grid columns on its own; repeatable, "
                             "e.g. --split-by trend_window --split-by entry_mode,tp_kind. tp_kind is derived from "
                             "tp_sl_ratio: 'R' (> 0, fixed R multiple), 'mid' (< 0, middle band), 'none' (0)")
    parser.add_argument("--entry-fee", type=float, help="Round-trip entry fee as a fraction of price (crypto 0.001)")
    parser.add_argument("--holding-fee", type=float, help="Holding fee per 8 hours as a fraction of price (0.0001)")
    parser.add_argument("--max-fee-r", type=float, help="Skip signals whose entry fee exceeds this fraction of R (0.2)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    module = wf.load_strategy(args.folder)
    # the fee constants live in the module with the kernels (the folder's script or multi_position_engine)
    for target in (module, getattr(module, "engine", None)):
        if target is not None and hasattr(target, "ENTRY_FEE_RATE"):
            if args.entry_fee is not None:
                target.ENTRY_FEE_RATE = args.entry_fee
            if args.holding_fee is not None:
                target.HOLDING_FEE_RATE = args.holding_fee
            if args.max_fee_r is not None:
                if not hasattr(target, "MAX_FEE_R"):
                    raise SystemExit(f"{args.folder}: its kernel has no MAX_FEE_R")
                target.MAX_FEE_R = args.max_fee_r
            print(f"fees: entry {target.ENTRY_FEE_RATE}, holding {target.HOLDING_FEE_RATE} per 8h, "
                  f"max fee {getattr(target, 'MAX_FEE_R', 0.2)} R", flush=True)
    space, notebook_formula = wf.grid_and_formula(args.folder, module)
    formulas = args.score_formulas or [notebook_formula]
    grid = pd.DataFrame(itertools.product(*space.values()), columns=list(space))
    files = sorted(args.data_dir.glob("*_futures_*.csv"))
    if args.symbols:
        files = [f for f in files if f.name.split("_futures")[0] in args.symbols]
    if not files:
        raise SystemExit(f"No CSV files in {args.data_dir}")
    print(f"{args.folder}: {len(grid):,} combos; formulas {formulas}; fee_bars {args.fee_bars or 'script default'}; "
          f"{len(files)} coins", flush=True)

    labels = grid.copy()
    if "tp_sl_ratio" in grid:
        labels["tp_kind"] = np.select([grid["tp_sl_ratio"] > 0, grid["tp_sl_ratio"] < 0], ["R", "mid"], "none")
    variants = {"all": np.ones(len(grid), dtype=bool)}
    for spec in args.split_by:
        columns = spec.split(",")
        for key, part in labels.groupby(columns):
            key = key if isinstance(key, tuple) else (key,)
            name = ", ".join(f"{c}={v:g}" if isinstance(v, (int, float, np.number)) else f"{c}={v}"
                             for c, v in zip(columns, key))
            variants[name] = grid.index.isin(part.index)

    rows = []
    top_rows = []
    for path in files:
        symbol = path.name.split("_futures")[0]
        bars = module.load_bars(path, args.timeframe, None, None)
        if "trend_window" in grid and hasattr(module, "add_trend_columns"):
            # SMA over the full history, so long trend windows are warm at the start of every slice.
            bars = module.add_trend_columns(bars, grid["trend_window"].unique())
        data_end = pd.Timestamp(args.data_end) if args.data_end else bars["timestamp"].iloc[-1].normalize()
        folds = schedule(bars["timestamp"].iloc[0], data_end, args.train_months, args.test_months,
                         args.first_test, args.last_test, args.min_test_days)[: args.max_folds]
        print(f"{symbol}: {len(bars):,} bars {bars['timestamp'].iloc[0]} -> {bars['timestamp'].iloc[-1]}; "
              f"{len(folds)} folds", flush=True)

        def run(start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
            part = window(bars, start, end)
            result, _ = module.run_gpu_grid(part, grid, args.timeframe, max_holding_bars=args.max_holding_bars,
                                            fee_bars=args.fee_bars, score_formula=formulas[0])
            return part, result

        for fold, train_start, train_end, test_start, test_end in folds:
            _, train = run(train_start, train_end)
            test_bars, test = run(test_start, test_end)
            market = float(test_bars["close"].iloc[-1] / test_bars["close"].iloc[0] - 1)
            for formula in formulas:
                train = train.assign(score=module.apply_score_formula(train, formula))
                test = test.assign(score=module.apply_score_formula(test, formula))
                for variant, mask in variants.items():
                    rows.append({"symbol": symbol, "formula": formula, "variant": variant, "fold": fold,
                                 "quarter": pd.Timestamp(test_start).to_period("Q").strftime("%Y-Q%q"),
                                 "test": f"{test_start}..{test_end}", "test_bars": len(test_bars),
                                 "market_return": market,
                                 "train_median": train.loc[mask, "r_per_month"].median(),
                                 "train_share_profit": (train.loc[mask, "performance"] > 0).mean(),
                                 **wf.evaluate(train[mask], test[mask], args.min_trades, args.top_k),
                                 **top_n_stats(train[mask], test[mask], args.min_trades, args.top_n)})
                if args.save_top:
                    eligible = train[(train["number_of_trade"] >= args.min_trades) & train["score"].notna()]
                    for rank, index in enumerate(eligible.sort_values("score", ascending=False).index[: args.save_top], 1):
                        top_rows.append({"symbol": symbol, "formula": formula, "fold": fold, "rank": rank,
                                         "train_start": train_start, "train_end": train_end,
                                         "test_start": test_start, "test_end": test_end,
                                         **grid.loc[index].to_dict(),
                                         "train_r_per_month": train.loc[index, "r_per_month"],
                                         "train_trades": int(train.loc[index, "number_of_trade"]),
                                         "test_r_per_month": test.loc[index, "r_per_month"],
                                         "test_trades": int(test.loc[index, "number_of_trade"])})
            line = ", ".join(f"{r['variant']} {r['test_median']:+.2f}/{r.get('top_25pct', float('nan')):+.2f}"
                             for r in rows[-len(variants):])
            print(f"  {fold} test {test_start}..{test_end} ({len(test_bars):,} bars): market {market:+.1%}; "
                  f"grid median / top 25% R/month: {line}", flush=True)

    table = pd.DataFrame(rows)
    table.to_csv(args.out / "per_fold.csv", index=False)
    if top_rows:
        pd.DataFrame(top_rows).to_csv(args.out / "top_sets.csv", index=False)
    top_n_columns = [f"top{n}_{stat}" for n in args.top_n for stat in ("median", "mean", "share_pos")]
    metrics = [m for m in METRICS if m in table] + top_n_columns

    grouped = table.groupby(["formula", "variant", "symbol"], sort=False)
    summary = grouped[metrics + ["market_return"]].mean()
    for column in ("test_median", "best_1", "top_k", "top_25pct", *(f"top{n}_median" for n in args.top_n)):
        summary[f"{column}_pos"] = grouped[column].apply(lambda s: f"{(s > 0).sum()}/{s.notna().sum()}")
    summary.insert(0, "folds", grouped["fold"].count())
    summary.to_csv(args.out / "summary.csv")

    by_quarter = table.groupby(["formula", "variant", "quarter"])
    quarters = by_quarter[["test_median", "best_1", "top_k", "top_25pct", "market_return"]].mean()
    quarters.insert(0, "coins", by_quarter["symbol"].count())
    for column in ("test_median", "top_k", "top_25pct"):
        quarters[f"{column}_coins_pos"] = by_quarter[column].apply(lambda s: int((s > 0).sum()))
    quarters.to_csv(args.out / "by_quarter.csv")

    pd.set_option("display.width", 260)
    pd.set_option("display.max_rows", 500)
    pd.set_option("display.max_columns", 60)
    regime_labels = ["market < -15%", "flat", "market > +15%"]
    for formula in formulas:
        print(f"\n===== ranking: {formula}")
        compare = []
        for variant in variants:
            part = summary.loc[(formula, variant)]
            q = quarters.loc[(formula, variant)]
            pooled = table[(table["formula"] == formula) & (table["variant"] == variant)]
            regime = pd.cut(pooled["market_return"], [-float("inf"), -0.15, 0.15, float("inf")], labels=regime_labels)
            coins_ok = (part["test_median"] > 0).mean()
            quarters_ok = (q["top_25pct"] > 0).mean()
            line = {
                "coins median>0": f"{(part['test_median'] > 0).sum()}/{len(part)}",
                "quarters top25>0": f"{(q['top_25pct'] > 0).sum()}/{len(q)}",
                "transfer bar": "PASS" if coins_ok >= 2 / 3 and quarters_ok >= 2 / 3 else "FAIL",
                "grid median": pooled["test_median"].mean(),
                "top 25%": pooled["top_25pct"].mean(),
                "top 10": pooled["top_k"].mean(),
                "top25 folds>0": (pooled["top_25pct"] > 0).mean(),
                "corr(median, mkt)": pooled["test_median"].corr(pooled["market_return"]),
                "corr(top25, mkt)": pooled["top_25pct"].corr(pooled["market_return"]),
            }
            for label in regime_labels:
                line[f"top25 | {label}"] = pooled.loc[regime == label, "top_25pct"].mean()
            for n in args.top_n:
                line[f"top{n} sets profitable"] = pooled[f"top{n}_share_pos"].mean()
            line["top10 test trades"] = pooled["top_k_test_trades"].mean()
            compare.append(pd.Series(line, name=variant))
        compare = pd.DataFrame(compare)
        print(compare.round(3).T.to_string())
        if len(variants) > 1:
            for metric in ("top_25pct", "test_median"):
                pivot = summary.loc[formula][metric].unstack("variant")[list(variants)]
                print(f"\n{metric} by coin (mean over folds, test R/month):")
                print(pivot.round(2).to_string())
                base = next((v for v in variants if v.endswith("=0")), None)
                if base is not None:
                    better = {v: f"{int((pivot[v] >= pivot[base]).sum())}/{len(pivot)}" for v in variants if v != base}
                    print(f"coins where the variant's {metric} >= {base}: {better}")
        else:
            part = summary.loc[(formula, "all")]
            print(part[["folds", "test_median", "top_k", "top_25pct", "best_1", "market_return",
                        "test_median_pos", "top_k_pos", "top_25pct_pos"]].round(3).to_string())


if __name__ == "__main__":
    main()
