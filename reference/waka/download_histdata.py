"""Download a whole history of histdata.com free 1-minute bid bars (yearly zips for past years, monthly for the
current one) and write bars in the walk-forward harness's format (<out-dir>/<pair>_futures_<timeframe>.csv:
timestamp, open, high, low, close, volume = 1m bars in the bar). Timestamps: histdata's EST without daylight saving
+ 5h = UTC (checked against FXCM on forex, see fill_gaps_histdata). Years histdata does not have are skipped.

    uv run python reference/waka/download_histdata.py --pairs XAUUSD XAGUSD --start-year 2018
    uv run python reference/waka/download_histdata.py --pairs AUDCAD ... --start-year 2005 --timeframe 1h \\
        --out-dir reference/data_fx_long --no-cache
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fill_gaps_histdata as fg  # noqa: E402

OFFSET_HOURS = 5


def cache_path(pair: str, year: int, month: int | None) -> Path:
    return fg.FX_DIR / "raw_histdata" / f"{pair}_{year}{'' if month is None else f'{month:02d}'}.zip"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True)
    ap.add_argument("--start-year", type=int, default=2018)
    ap.add_argument("--timeframe", default="5min")
    ap.add_argument("--out-dir", type=Path, default=HERE.parent / "data_fx_wf")
    ap.add_argument("--no-cache", action="store_true", help="delete each downloaded zip once parsed")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now()
    for pair in args.pairs:
        parts = []
        for year in range(args.start_year, now.year + 1):
            keys = [(year, None)] if year < now.year else [(year, m) for m in range(1, now.month + 1)]
            raws = [(k, fg.fetch_zip(pair, *k)) for k in keys]
            if year < now.year and raws[0][1] is None:  # no yearly file: try months
                raws = [((year, m), fg.fetch_zip(pair, year, m)) for m in range(1, 13)]
            for key, raw in raws:
                if raw is None:
                    continue
                m1 = fg.month_bars(raw, OFFSET_HOURS)
                bars = m1.resample(args.timeframe, label="left", closed="left").agg(
                    {"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
                bars["volume"] = m1["c"].resample(args.timeframe, label="left", closed="left").count()
                parts.append(bars)
                if args.no_cache:
                    cache_path(pair, *key).unlink(missing_ok=True)
            print(f"{pair} {year}: {sum(len(p) for p in parts)} {args.timeframe} bars so far", flush=True)
        if not parts:
            print(f"{pair}: no data", flush=True)
            continue
        out = pd.concat(parts).sort_index()
        out = out[~out.index.duplicated()].rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
        out.index.name = "timestamp"
        out.to_csv(args.out_dir / f"{pair.lower()}_futures_{args.timeframe}.csv", float_format="%.5f")
        print(f"{pair}: {len(out)} bars {out.index[0]} -> {out.index[-1]}", flush=True)


if __name__ == "__main__":
    main()
