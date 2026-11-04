"""Download FXCM's free weekly 1-minute bid/ask candles and build 5min CSVs (UTC) for the Waka Waka reproduction.

Source: https://candledata.fxcorporate.com/m1/{PAIR}/{YEAR}/{WEEK}.csv.gz (gzip CSV: DateTime as MM/DD/YYYY
HH:MM:SS.fff, BidOpen, BidHigh, BidLow, BidClose, AskOpen, AskHigh, AskLow, AskClose). Some weeks are missing (404);
they are listed in data_fx/missing_weeks.csv. Raw weeks are cached in data_fx/raw/<PAIR>/<YEAR>-<WEEK>.csv.gz so the
download resumes. (Dukascopy's datafeed was tried first and answered 503 after a short burst.)

Output: reference/data_fx/<pair>_5min.csv with timestamp (bar open, UTC), bid_open..bid_close, ask_open..ask_close,
minutes (1m bars in the 5m bar).

    uv run python reference/waka/download_fx.py --pairs AUDCAD AUDNZD NZDCAD EURUSD --start-year 2018
"""

from __future__ import annotations

import argparse
import gzip
import io
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data_fx"
URL = "https://candledata.fxcorporate.com/m1/{pair}/{year}/{week}.csv.gz"


def is_gzip(raw: bytes) -> bool:
    try:
        gzip.decompress(raw)
        return True
    except (OSError, EOFError, gzip.BadGzipFile):
        return False


def fetch_week(pair: str, year: int, week: int) -> bytes | None:
    """The week's gzip bytes, or None when the file is absent or still corrupt after a second download."""
    path = OUT / "raw" / pair / f"{year}-{week:02d}.csv.gz"
    if path.is_file():
        raw = path.read_bytes()
        if is_gzip(raw):
            return raw
        path.unlink()
    url = URL.format(pair=pair, year=year, week=week)
    bad = 0
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=60).read()
            if not is_gzip(raw):
                bad += 1
                if bad >= 2:
                    return None
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                return None
            time.sleep(5 * 2 ** attempt)
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(5 * 2 ** attempt)
    raise RuntimeError(f"failed: {url}")


def parse(raw: bytes | None) -> pd.DataFrame | None:
    """None for an absent, empty or unreadable week."""
    if raw is None:
        return None
    try:
        frame = pd.read_csv(io.BytesIO(gzip.decompress(raw)))
        frame["DateTime"] = pd.to_datetime(frame["DateTime"], format="%m/%d/%Y %H:%M:%S.%f")
    except (pd.errors.EmptyDataError, pd.errors.ParserError, KeyError, ValueError):
        return None
    return frame.set_index("DateTime").sort_index() if len(frame) else None


def to_5min(frame: pd.DataFrame) -> pd.DataFrame:
    agg = {}
    for side in ("Bid", "Ask"):
        agg.update({f"{side}Open": "first", f"{side}High": "max", f"{side}Low": "min", f"{side}Close": "last"})
    bars = frame.resample("5min", label="left", closed="left").agg(agg)
    bars["minutes"] = frame["BidClose"].resample("5min", label="left", closed="left").count()
    bars = bars.dropna(subset=["BidOpen", "AskOpen"])
    bars.columns = ["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
                    "minutes"]
    bars.index.name = "timestamp"
    return bars


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", default=["AUDCAD", "AUDNZD", "NZDCAD", "EURUSD"])
    ap.add_argument("--start-year", type=int, default=2018)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    this_year = pd.Timestamp.now().year
    missing = []
    for pair in args.pairs:
        keys = [(y, w) for y in range(args.start_year, this_year + 1) for w in range(1, 54)]
        t0 = time.time()
        with ThreadPoolExecutor(args.workers) as pool:
            raws = list(pool.map(lambda k: fetch_week(pair, *k), keys))
        parsed = [parse(r) for r in raws]
        frames = [f for f in parsed if f is not None]
        missing += [(pair, y, w) for (y, w), f in zip(keys, parsed) if f is None]
        minutes = pd.concat(frames).sort_index()
        minutes = minutes[~minutes.index.duplicated()]
        bars = to_5min(minutes)
        bars.to_csv(OUT / f"{pair.lower()}_5min.csv", float_format="%.5f")
        spread = (bars["ask_close"] - bars["bid_close"]) * 1e4
        print(f"{pair}: {len(frames)} weeks ({time.time() - t0:.0f}s), {len(bars)} 5m bars {bars.index[0]} -> "
              f"{bars.index[-1]}; median spread {spread.median():.2f} pips, p95 {spread.quantile(0.95):.2f}",
              flush=True)
    path = OUT / "missing_weeks.csv"
    new = pd.DataFrame(missing, columns=["pair", "year", "week"])
    if path.is_file():  # keep other pairs' rows
        old = pd.read_csv(path)
        new = pd.concat([old[~old["pair"].isin(args.pairs)], new], ignore_index=True)
    new.to_csv(path, index=False)


if __name__ == "__main__":
    main()
