"""Fill the weekdays missing from the FXCM 5min files with histdata.com's free 1-minute bid bars.

A weekday (not Dec 25 / Jan 1) with fewer than 100 FXCM 5m bars is taken from the month's histdata.com ASCII M1 zip
(page https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/<pair>/<y>/<m>,
form token posted to get.php; cached in data_fx/raw_histdata/). histdata stamps bars in EST without daylight saving;
the hour offset to UTC is checked against FXCM on months both sources cover. histdata has bids only, so the ask is
bid + the pair's median FXCM spread for that hour of day (UTC). (Dukascopy throttled to ~1 file per 4 minutes.)
The 5m CSV gains the filled bars and a `source` column (fxcm / histdata).

    uv run python reference/waka/fill_gaps_histdata.py --pairs AUDCAD AUDNZD NZDCAD EURUSD
"""

from __future__ import annotations

import argparse
import io
import re
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FX_DIR = HERE.parent / "data_fx"
PAGE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair}/{y}/{m}"
HEADERS = {"User-Agent": "Mozilla/5.0"}
FIELDS = ("tk", "date", "datemonth", "platform", "timeframe", "fxpair")


def month_zip(pair: str, year: int, month: int) -> bytes | None:
    """The month's zip; for older years histdata only serves whole-year files, which are used instead."""
    raw = fetch_zip(pair, year, month)
    return raw if raw is not None else fetch_zip(pair, year, None)


def fetch_zip(pair: str, year: int, month: int | None) -> bytes | None:
    path = FX_DIR / "raw_histdata" / f"{pair}_{year}{'' if month is None else f'{month:02d}'}.zip"
    if path.is_file():
        return path.read_bytes()
    page = PAGE.format(pair=pair.lower(), y=year, m="" if month is None else month).rstrip("/")
    for attempt in range(5):
        try:
            html = urllib.request.urlopen(urllib.request.Request(page, headers=HEADERS), timeout=60).read()
            html = html.decode("utf-8", "replace")
            found = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
            if not all(k in found for k in FIELDS):
                return None
            data = urllib.parse.urlencode({k: found[k] for k in FIELDS}).encode()
            req = urllib.request.Request("https://www.histdata.com/get.php", data=data,
                                         headers={**HEADERS, "Referer": page})
            raw = urllib.request.urlopen(req, timeout=180).read()
            if raw[:2] != b"PK":
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            time.sleep(2)
            return raw
        except (OSError, TimeoutError):
            time.sleep(10 * 2 ** attempt)
    return None


def month_bars(raw: bytes, offset_hours: int) -> pd.DataFrame:
    """1-minute bid bars of the month, stamped in UTC."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = next(n for n in z.namelist() if n.endswith(".csv"))
    f = pd.read_csv(io.BytesIO(z.read(name)), sep=";", header=None, names=["ts", "o", "h", "l", "c", "v"])
    f.index = pd.to_datetime(f["ts"], format="%Y%m%d %H%M%S") + pd.Timedelta(hours=offset_hours)
    return f[["o", "h", "l", "c"]]


def to_5min(m1: pd.DataFrame, spread_by_hour: pd.Series) -> pd.DataFrame:
    bars = m1.resample("5min", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last"})
    bars["minutes"] = m1["c"].resample("5min", label="left", closed="left").count()
    bars = bars.dropna()
    sp = spread_by_hour.reindex(bars.index.hour).to_numpy()
    out = pd.DataFrame({"bid_open": bars["o"], "bid_high": bars["h"], "bid_low": bars["l"], "bid_close": bars["c"]})
    for k in ("open", "high", "low", "close"):
        out[f"ask_{k}"] = out[f"bid_{k}"] + sp
    out["minutes"] = bars["minutes"]
    return out


def missing_days(bars: pd.DataFrame) -> list[pd.Timestamp]:
    counts = bars.groupby(bars.index.normalize()).size()
    days = pd.date_range(bars.index[0].normalize(), bars.index[-1].normalize(), freq="D")
    days = days[(days.dayofweek < 5) & ~((days.month == 12) & (days.day == 25)) & ~((days.month == 1) & (days.day == 1))]
    return [d for d in days if counts.get(d, 0) < 100]


def check_offset(pair: str, fxcm: pd.DataFrame, months: list[tuple[int, int]]) -> int:
    """Hour offset histdata -> UTC that best matches FXCM closes on a month FXCM covers fully."""
    for y, m in months:
        raw = month_zip(pair, y, m)
        if raw is None:
            continue
        ref = fxcm.loc[f"{y}-{m:02d}", "bid_close"]
        scores = {}
        for off in (4, 5, 6):
            hd = month_bars(raw, off)["c"].resample("5min", label="left", closed="left").last().shift(1, freq="5min")
            hd.index = hd.index - pd.Timedelta(minutes=5)
            joined = pd.concat([ref, hd], axis=1, join="inner").dropna()
            scores[off] = float((joined.iloc[:, 0] - joined.iloc[:, 1]).abs().median() / 1e-4)
        best = min(scores, key=scores.get)
        print(f"{pair}: histdata offset check on {y}-{m:02d}: median |close diff| pips by offset {scores} -> {best}",
              flush=True)
        return best
    return 5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", default=["AUDCAD", "AUDNZD", "NZDCAD", "EURUSD"])
    args = ap.parse_args()
    for pair in args.pairs:
        path = FX_DIR / f"{pair.lower()}_5min.csv"
        bars = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
        if "source" in bars:
            bars = bars[bars["source"] == "fxcm"].drop(columns="source")
        spread = (bars["ask_close"] - bars["bid_close"]).groupby(bars.index.hour).median()
        days = missing_days(bars)
        months = sorted({(d.year, d.month) for d in days})
        full_months = [(y, m) for y, m in [(2026, 3), (2025, 3), (2024, 3)] if (y, m) not in months]
        offset = check_offset(pair, bars, full_months)
        added = []
        for y, m in months:
            raw = month_zip(pair, y, m)
            if raw is None:
                print(f"{pair}: histdata has no {y}-{m:02d}", flush=True)
                continue
            m1 = month_bars(raw, offset)
            m5 = to_5min(m1[(m1.index.year == y) & (m1.index.month == m)], spread)
            want = m5.index.normalize().isin([d for d in days if (d.year, d.month) == (y, m)])
            m5 = m5[want & ~m5.index.isin(bars.index)]
            added.append(m5.assign(source="histdata"))
        merged = pd.concat([bars.assign(source="fxcm"), *added]).sort_index()
        merged.index.name = "timestamp"
        merged.to_csv(path, float_format="%.5f")
        still = missing_days(merged)
        print(f"{pair}: {len(days)} missing weekdays in {len(months)} months, {sum(len(a) for a in added)} histdata "
              f"5m bars added; still missing {len(still)} weekdays {[str(d.date()) for d in still][:12]}", flush=True)


if __name__ == "__main__":
    main()
