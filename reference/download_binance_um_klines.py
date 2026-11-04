"""Download Binance USD-M perpetual 5min klines (and funding) from data.binance.vision into reference/data_5m/.

Free public data, no API key. Steps:

1. universe: list every symbol under data/futures/um/monthly/klines/ (delisted ones included, so no survivorship
   bias), keep USDT perpetuals whose first monthly file is <= --listed-before, drop stablecoins and renamed
   duplicates, rank by the median daily quote volume of --rank-year (1d klines) and keep the --new best that are
   not already in --existing (the 17 coins of reference/data). Writes universe.csv.
2. klines: for the existing + new symbols, download 5m monthly files from --start to the last full month and daily
   files for the current month, plus monthly funding rates. Writes <sym>_futures_5min.csv (timestamp = bar open,
   open, high, low, close, volume, quote_volume) and funding/<sym>.csv.

Run from the repository root:

    uv run python reference/download_binance_um_klines.py --out reference/data_5m
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd

BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILES = "https://data.binance.vision"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
STABLE = {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "UST", "USTC", "EUR", "GBP", "AEUR", "BTCDOM", "DEFI",
          "FOOTBALL", "BLUEBIRD"}
RENAMED = {"POLUSDT"}  # MATIC's successor; MATICUSDT is already one of the existing coins
KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
                 "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def get(url: str, tries: int = 3) -> bytes | None:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
    return None


def list_prefixes(prefix: str) -> list[str]:
    """Sub-'folders' under an S3 prefix (follows pagination)."""
    out, marker = [], ""
    while True:
        xml = get(f"{BUCKET}?delimiter=/&prefix={prefix}&marker={marker}")
        root = ElementTree.fromstring(xml)
        out += [p.find("s3:Prefix", NS).text for p in root.findall("s3:CommonPrefixes", NS)]
        if root.find("s3:IsTruncated", NS).text != "true":
            return out
        marker = root.find("s3:NextMarker", NS).text


def list_months(symbol: str, interval: str) -> list[str]:
    """YYYY-MM of every monthly kline zip of a symbol."""
    keys, marker = [], ""
    while True:
        xml = get(f"{BUCKET}?prefix=data/futures/um/monthly/klines/{symbol}/{interval}/&marker={marker}")
        root = ElementTree.fromstring(xml)
        contents = [c.find("s3:Key", NS).text for c in root.findall("s3:Contents", NS)]
        keys += contents
        if root.find("s3:IsTruncated", NS).text != "true" or not contents:
            break
        marker = contents[-1]
    return sorted({m.group(1) for k in keys if (m := re.search(r"-(\d{4}-\d{2})\.zip$", k))})


def read_zip(blob: bytes, columns: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        raw = archive.read(archive.namelist()[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if not str(frame.iloc[0, 0]).lstrip("-").isdigit():  # newer files carry a header row
        frame = frame.iloc[1:]
    frame = frame.iloc[:, : len(columns)]
    frame.columns = columns[: frame.shape[1]]
    return frame.apply(pd.to_numeric, errors="coerce")


def to_datetime_ms(values: pd.Series) -> pd.Series:
    unit = "us" if values.max() > 1e14 else "ms"
    return pd.to_datetime(values, unit=unit)


def klines(symbol: str, interval: str, months: list[str], days: list[str]) -> tuple[pd.DataFrame, list[str]]:
    urls = [f"{FILES}/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{m}.zip" for m in months]
    urls += [f"{FILES}/data/futures/um/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{d}.zip" for d in days]
    with ThreadPoolExecutor(8) as pool:
        blobs = list(pool.map(get, urls))
    missing = [u.rsplit("/", 1)[-1] for u, b in zip(urls, blobs) if b is None]
    frames = [read_zip(b, KLINE_COLUMNS) for b in blobs if b is not None]
    if not frames:
        return pd.DataFrame(), missing
    frame = pd.concat(frames, ignore_index=True).dropna(subset=["open_time", "close"])
    frame["timestamp"] = to_datetime_ms(frame["open_time"])
    frame = frame.drop_duplicates("timestamp").sort_values("timestamp")
    return frame[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]], missing


def funding(symbol: str, months: list[str]) -> pd.DataFrame:
    urls = [f"{FILES}/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip" for m in months]
    with ThreadPoolExecutor(8) as pool:
        blobs = [b for b in pool.map(get, urls) if b is not None]
    if not blobs:
        return pd.DataFrame()
    frame = pd.concat([read_zip(b, ["calc_time", "funding_interval_hours", "last_funding_rate"]) for b in blobs])
    frame = frame.dropna(subset=["calc_time"])
    frame["timestamp"] = to_datetime_ms(frame["calc_time"])
    return frame.drop_duplicates("timestamp").sort_values("timestamp")[
        ["timestamp", "funding_interval_hours", "last_funding_rate"]]


def build_universe(args, existing: list[str]) -> pd.DataFrame:
    symbols = [p.rstrip("/").rsplit("/", 1)[-1] for p in list_prefixes("data/futures/um/monthly/klines/")]
    candidates = [s for s in symbols if re.fullmatch(r"[A-Z0-9]+USDT", s) and s not in RENAMED
                  and s[:-4] not in STABLE and s.lower() not in existing]
    print(f"{len(symbols)} symbols listed, {len(candidates)} USDT perpetual candidates", flush=True)
    with ThreadPoolExecutor(16) as pool:
        months = dict(zip(candidates, pool.map(lambda s: list_months(s, "1d"), candidates)))
    early = [s for s in candidates if months[s] and months[s][0] <= args.listed_before]
    print(f"{len(early)} listed by {args.listed_before}", flush=True)
    year = [f"{args.rank_year}-{m:02d}" for m in range(1, 13)]

    def volume(symbol: str) -> float:
        frame, _ = klines(symbol, "1d", [m for m in year if m in months[symbol]], [])
        return float(frame["quote_volume"].median()) if len(frame) else 0.0

    with ThreadPoolExecutor(8) as pool:
        volumes = dict(zip(early, pool.map(volume, early)))
    table = pd.DataFrame({"symbol": early, f"median_daily_quote_volume_{args.rank_year}": [volumes[s] for s in early],
                          "first_month": [months[s][0] for s in early], "last_month": [months[s][-1] for s in early]})
    table = table.sort_values(table.columns[1], ascending=False).reset_index(drop=True)
    table.insert(0, "rank", table.index + 1)
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--existing-dir", type=Path, default=Path("reference/data"))
    parser.add_argument("--new", type=int, default=30)
    parser.add_argument("--listed-before", default="2022-12")
    parser.add_argument("--rank-year", default="2022")
    parser.add_argument("--start", default="2021-01")
    parser.add_argument("--symbols", nargs="*", help="Only download these (skip the universe step)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "funding").mkdir(exist_ok=True)

    existing = sorted(p.name.split("_futures")[0] for p in args.existing_dir.glob("*_futures_*.csv"))
    universe_path = args.out / "universe.csv"
    if args.symbols:
        chosen = [s.upper() for s in args.symbols]
    else:
        if universe_path.exists():
            universe = pd.read_csv(universe_path)
        else:
            universe = build_universe(args, existing)
            universe.to_csv(args.out / "universe_all_candidates.csv", index=False)
            universe = universe.head(args.new)
            universe.to_csv(universe_path, index=False)
        print(universe.to_string(index=False), flush=True)
        chosen = [s.upper() for s in existing] + universe["symbol"].tolist()

    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    month_start = today.to_period("M").start_time
    all_months = [str(p) for p in pd.period_range(args.start, month_start - pd.Timedelta(days=1), freq="M")]
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(month_start, today - pd.Timedelta(days=1))]
    for symbol in chosen:
        path = args.out / f"{symbol.lower()}_futures_5min.csv"
        if path.exists():
            print(f"{symbol}: exists, skipped", flush=True)
            continue
        free_gb = shutil.disk_usage(args.out).free / 1e9
        if free_gb < 5:
            sys.exit(f"Only {free_gb:.1f} GB free; stopping before {symbol}")
        months = [m for m in list_months(symbol, "5m") if m >= args.start]
        frame, missing = klines(symbol, "5m", months, days)
        if frame.empty:
            print(f"{symbol}: no 5m data", flush=True)
            continue
        step = frame["timestamp"].diff().dropna()
        gaps = step[step > pd.Timedelta(hours=1)]
        frame.to_csv(path, index=False)
        rates = funding(symbol, months)
        if len(rates):
            rates.to_csv(args.out / "funding" / f"{symbol.lower()}.csv", index=False)
        print(f"{symbol}: {len(frame):,} bars {frame['timestamp'].iloc[0]} -> {frame['timestamp'].iloc[-1]}; "
              f"{len(gaps)} gaps > 1h (longest {gaps.max() if len(gaps) else '-'}); "
              f"{len(missing)} missing files {missing[:3]}; funding rows {len(rates)}", flush=True)


if __name__ == "__main__":
    main()
