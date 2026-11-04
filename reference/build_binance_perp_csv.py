"""Build 5min CSVs for the reference harnesses from the lab's Binance USD-M perp 1-minute trade bars.

Reads data/perp-minutes/<SYM>-USDT-USDT_1m.trade.parquet plus its .part-*.parquet files (ts in ms = bar open),
keeps only rows up to --end (the lab's in-sample end; perp-minutes runs into the holdout period, which must never
be read into an experiment: parts whose ts statistics start after --end are skipped unopened, the rest are
filtered on read), reports missing minutes, resamples to 5min (left label) and writes
<out>/<sym>usdt_futures_5min.csv with columns timestamp, open, high, low, close, volume. Checks the result
against data/perp/<SYM>-USDT-USDT_1h.parquet. Run from the repository root:

    uv run python reference/build_binance_perp_csv.py --out reference/data_binance_perp
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

SYMBOLS = ["BTC", "ETH", "BNB", "SOL", "XRP"]
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def part_files(source: Path, symbol: str) -> list[Path]:
    stem = f"{symbol}-USDT-USDT_1m.trade"
    return [source / f"{stem}.parquet", *sorted(source.glob(f"{stem}.part-*.parquet"))]


def read_minutes(paths: list[Path], end_ms: int) -> tuple[pd.DataFrame, int]:
    tables, skipped = [], 0
    for path in paths:
        meta = pq.ParquetFile(path).metadata
        column = meta.schema.names.index("ts")
        starts = [meta.row_group(i).column(column).statistics for i in range(meta.num_row_groups)]
        if all(s is not None and s.has_min_max and s.min > end_ms for s in starts):
            skipped += 1
            continue
        table = pq.read_table(path, filters=[("ts", "<=", end_ms)])
        assert table.num_rows == 0 or pc.max(table["ts"]).as_py() <= end_ms
        tables.append(table.to_pandas())
    frame = pd.concat(tables, ignore_index=True).drop_duplicates("ts").sort_values("ts")
    frame["timestamp"] = pd.to_datetime(frame["ts"], unit="ms")
    return frame.set_index("timestamp")[list(AGG)], skipped


def gaps(minutes: pd.DataFrame) -> tuple[int, list[str]]:
    step = minutes.index.to_series().diff().dropna()
    missing = int((step / pd.Timedelta(minutes=1) - 1).sum())
    long = step[step > pd.Timedelta(hours=1)]
    return missing, [f"{t - d} -> {t} ({d})" for t, d in long.items()]


def check_hourly(bars_5min: pd.DataFrame, hourly_path: Path) -> str:
    """Compare 1h bars rebuilt from 5min with the lab's 1h file, trying open-time and close-time labels."""
    lab = pd.read_parquet(hourly_path)
    stamps = lab["ts"] if pd.api.types.is_datetime64_any_dtype(lab["ts"]) else pd.to_datetime(lab["ts"], unit="ms")
    lab.index = pd.DatetimeIndex(stamps).tz_localize(None) if stamps.dt.tz is not None else pd.DatetimeIndex(stamps)
    rebuilt = bars_5min.resample("1h").agg(AGG).dropna()
    best = None
    for label, shift in (("open-time", pd.Timedelta(0)), ("close-time", pd.Timedelta(hours=1))):
        mine = rebuilt.set_axis(rebuilt.index + shift)
        common = lab.index.intersection(mine.index)
        diff = max(float((mine.loc[common, c] / lab.loc[common, c] - 1).abs().max()) for c in ("open", "high", "low", "close"))
        if best is None or diff < best[1]:
            best = (label, diff, len(common), len(lab))
    return f"1h check vs {hourly_path.name}: {best[0]} labels, max rel diff {best[1]:.2e} over {best[2]:,}/{best[3]:,} bars"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path("data/perp-minutes"))
    parser.add_argument("--hourly", type=Path, default=Path("data/perp"))
    parser.add_argument("--end", default="2025-10-01 23:59", help="Last minute kept (lab in-sample end)")
    parser.add_argument("--symbols", nargs="*", default=SYMBOLS)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    end_ms = int(pd.Timestamp(args.end).value // 1_000_000)

    for symbol in args.symbols:
        minutes, skipped = read_minutes(part_files(args.source, symbol), end_ms)
        missing, long_gaps = gaps(minutes)
        bars = minutes.resample("5min").agg(AGG).dropna()
        path = args.out / f"{symbol.lower()}usdt_futures_5min.csv"
        bars.reset_index().to_csv(path, index=False)
        print(f"{symbol}: {len(minutes):,} minutes {minutes.index[0]} -> {minutes.index[-1]}; {skipped} parts after "
              f"the end skipped; {missing:,} missing minutes; {len(bars):,} 5min bars -> {path}")
        for line in long_gaps:
            print("   gap", line)
        print("  ", check_hourly(bars, args.hourly / f"{symbol}-USDT-USDT_1h.parquet"))


if __name__ == "__main__":
    main()
