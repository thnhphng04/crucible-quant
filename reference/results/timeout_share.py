"""How often do the walk-forward's selected sets close by timeout (max_holding_bars)?

Reads <wf dir>/top_sets.csv (walk_forward_multi_asset.py --save-top N), replays every selected set on its
train and test windows with the float64 trade list of reference/multi_position/multi_position_engine.py
(one position at a time = the original short script) and reports, per window: share of trades by outcome
(stop / take_profit / timeout / end_of_data), and how the timeout trades stood when they were cut (gross R).

Usage: python timeout_share.py <wf dir> [--side short] [--hold 288] [--fee-bars 32] [--data-dir reference/data]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

REFERENCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REFERENCE / "multi_position"))
import multi_position_engine as engine  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("wf_dir", type=Path)
parser.add_argument("--side", choices=["long", "short"], default="short")
parser.add_argument("--hold", type=int, default=288)
parser.add_argument("--fee-bars", type=float, default=32.0)
parser.add_argument("--data-dir", type=Path, default=REFERENCE / "data")
parser.add_argument("--formula", default="r_martin_monthly")
args = parser.parse_args()
side = engine.SIDE_LONG if args.side == "long" else engine.SIDE_SHORT

top = pd.read_csv(args.wf_dir / "top_sets.csv")
top = top[top["formula"] == args.formula]
meta = {"symbol", "formula", "fold", "rank", "train_start", "train_end", "test_start", "test_end",
        "train_r_per_month", "train_trades", "test_r_per_month", "test_trades"}
param_columns = [c for c in top.columns if c not in meta]  # grid columns, incl. entry_mode / trend_window
bars_cache: dict[str, pd.DataFrame] = {}
trades = []
for row in top.itertuples(index=False):
    if row.symbol not in bars_cache:
        path = next(args.data_dir.glob(f"{row.symbol}_futures_*.csv"))
        bars_cache[row.symbol] = engine.load_bars(path, "15min", None, None)
    bars = bars_cache[row.symbol]
    params = {c: getattr(row, c) for c in param_columns}
    for window, start, end in (("train", row.train_start, row.train_end), ("test", row.test_start, row.test_end)):
        stamps = bars["timestamp"]
        part = bars[(stamps >= start) & (stamps < pd.Timestamp(end) + pd.Timedelta(days=1))].reset_index(drop=True)
        trace = engine.cpu_trace(side, part, params, "15min", args.hold, args.fee_bars, max_open=1)
        trace = trace.assign(symbol=row.symbol, fold=row.fold, rank=row.rank, window=window)
        trades.append(trace[["symbol", "fold", "rank", "window", "outcome", "holding_bars", "gross_r", "net_r"]])

trades = pd.concat(trades, ignore_index=True)
trades.to_csv(args.wf_dir / f"top_sets_trades_{args.formula.replace('/', '_').replace('*', 'x')}.csv", index=False)
pd.set_option("display.width", 220)
print(f"{args.wf_dir.name}: {top['symbol'].nunique()} coins, {len(top)} selected sets ({args.formula}), "
      f"hold {args.hold} bars; {len(trades):,} trades replayed")
for window in ("train", "test"):
    part = trades[trades["window"] == window]
    share = part["outcome"].value_counts(normalize=True).rename("share of trades")
    timeouts = part[part["outcome"] == "timeout"]
    print(f"\n== {window}: {len(part):,} trades")
    print(share.round(3).to_string())
    print(f"timeout trades: mean gross R at the cut {timeouts['gross_r'].mean():+.3f}, "
          f"share in profit {(timeouts['gross_r'] > 0).mean():.1%}, share beyond +1R {(timeouts['gross_r'] > 1).mean():.1%}; "
          f"net R of timeouts / all trades: {timeouts['net_r'].sum():+.1f} / {part['net_r'].sum():+.1f}")
per_coin = (trades[trades["window"] == "test"].groupby("symbol")["outcome"]
            .apply(lambda s: (s == "timeout").mean()).rename("test timeout share"))
print("\n" + per_coin.round(3).to_string())
