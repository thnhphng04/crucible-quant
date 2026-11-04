"""Why do some coins fail the slow-Bollinger strategies? Per-coin traits on 15min bars of reference/data.

For every coin:
- liquidity and volatility: median daily quote volume, daily realized volatility;
- Bollinger (window --boll, dev 2.0) width in % of price at downside breaks, and the entry fee in R a
  sl_multiplier 1 trade pays there (engine: 0.001 / sl_ratio, sl_ratio = risk / entry);
- variance ratios VR(q) of 15min log returns (q = 4, 16, 96 bars): > 1 trending, < 1 mean reverting;
- event study at downside breaks (close[t-1] > lower > close[t]) and upside breaks: the mean forward move over
  h bars in units of band width (upper - lower), i.e. gross R of a stop-at-band-width trade with no take profit,
  before fees. Short trend wins when the move after a downside break is negative; fade wins when the move after an
  upside break is negative (short the break) / after a downside break is positive (long the break);
- share of downside breaks that close back above the lower band within 4 bars (fake breaks).

Then joins the walk-forward per-coin result (--result, coin_groups.csv) and prints rank correlations across coins.
Run from the repository root:

    uv run python reference/results/coin_traits.py --result reference/results/wf_multi_asset_short_rsi_trend_nbgrid_9m3m
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

BAD = {"btcusdt", "ethusdt", "bnbusdt", "ltcusdt", "linkusdt"}
NEUTRAL = {"solusdt", "trxusdt", "tonusdt"}
GOOD = {"dogeusdt", "maticusdt", "xrpusdt", "xlmusdt", "opusdt", "adausdt", "avaxusdt", "suiusdt", "1000shibusdt"}
EXTRA = ["beta_btc", "corr_btc", "idio_vol_pct", "skew_4h", "crash_day_share", "down_up_semivol", "bh_sharpe",
         "funding_8h_bp"]
HORIZONS = [4, 16, 48, 96, 288]


def load(path: Path) -> pd.DataFrame:
    bars = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "quote_volume" in bars:
        agg["quote_volume"] = "sum"
    return bars.resample("15min").agg(agg).dropna()


def variance_ratio(log_returns: np.ndarray, q: int) -> float:
    r = log_returns - log_returns.mean()
    summed = np.convolve(r, np.ones(q), mode="valid")
    return float(summed.var() / (q * r.var()))


def traits(bars: pd.DataFrame, window: int) -> dict:
    close = bars["close"]
    mid = close.rolling(window).mean()
    std = close.rolling(window).std(ddof=0)
    upper, lower = mid + 2.0 * std, mid - 2.0 * std
    width = upper - lower
    prev = close.shift(1)
    down = (prev > lower) & (close < lower)
    up = (prev < upper) & (close > upper)
    daily = bars.resample("1D").agg({"close": "last", "volume": "sum"}).dropna()
    log_ret = np.log(close).diff().dropna().to_numpy()
    row = {
        "quote_vol_musd": float((bars["volume"] * close).resample("1D").sum().median() / 1e6),
        "daily_vol_pct": float(np.log(daily["close"]).diff().std() * 100),
        "band_width_pct": float((width / close)[down].median() * 100),
        "fee_R_sl1": float((0.001 / ((upper - close) / close))[down].median()),
        "breaks_per_month": float(down.sum() / (len(bars) / (96 * 30.4))),
    }
    for q in (4, 16, 96):
        row[f"VR{q}"] = variance_ratio(log_ret, q)
    for h in HORIZONS:
        move = (close.shift(-h) - close) / width
        row[f"down_{h}"] = float(move[down].mean())
        row[f"up_{h}"] = float(move[up].mean())
        # minus the coin's average move from any bar, so the period's bull/bear drift does not count as an edge
        row[f"down_adj_{h}"] = row[f"down_{h}"] - float(move.mean())
    back_inside = pd.concat([(close.shift(-k) > lower.shift(-k)) for k in range(1, 5)], axis=1).any(axis=1)
    row["fake_break_4"] = float(back_inside[down].mean())
    return row


def extra_traits(bars: pd.DataFrame, btc_daily: pd.Series, funding_path: Path) -> dict:
    """Return-distribution, BTC-relation, funding and liquidity traits (--extra)."""
    daily = np.log(bars["close"].resample("1D").last().dropna()).diff().dropna()
    joined = pd.concat([daily, btc_daily], axis=1, join="inner").dropna()
    coin, btc = joined.iloc[:, 0], joined.iloc[:, 1]
    beta = float(coin.cov(btc) / btc.var())
    four_hour = np.log(bars["close"].resample("4h").last().dropna()).diff().dropna()
    row = {
        "beta_btc": beta,
        "corr_btc": float(coin.corr(btc)),
        "idio_vol_pct": float((coin - beta * btc).std() * 100),
        "skew_4h": float(four_hour.skew()),
        "crash_day_share": float((daily < np.log(0.95)).mean()),
        "down_up_semivol": float(np.sqrt((daily[daily < 0] ** 2).mean() / (daily[daily > 0] ** 2).mean())),
        "bh_sharpe": float(daily.mean() / daily.std() * np.sqrt(365)),
    }
    if "quote_volume" in bars:
        row["quote_vol_musd"] = float(bars["quote_volume"].resample("1D").sum().median() / 1e6)
    if funding_path.is_file():
        rates = pd.read_csv(funding_path, parse_dates=["timestamp"]).set_index("timestamp")
        rates = rates.loc[bars.index[0]: bars.index[-1]]
        per_8h = rates["last_funding_rate"] * 8 / rates["funding_interval_hours"].fillna(8)
        row["funding_8h_bp"] = float(per_8h.mean() * 1e4)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("reference/data"))
    parser.add_argument("--boll", type=int, default=100)
    parser.add_argument("--result", type=Path, help="walk-forward folder holding coin_groups.csv")
    parser.add_argument("--start", help="Only bars from this date")
    parser.add_argument("--end", help="Only bars up to this date")
    parser.add_argument("--symbols", nargs="*", help="Only these symbols (file name before '_futures')")
    parser.add_argument("--extra", action="store_true",
                        help="Also beta/corr to BTC, idiosyncratic vol, 4h skew, crash days, semivol ratio, "
                             "buy-and-hold Sharpe, funding (data-dir/funding/<sym>.csv)")
    args = parser.parse_args()

    paths = {p.name.split("_futures")[0]: p for p in sorted(args.data_dir.glob("*_futures_*.csv"))}
    if args.symbols:
        paths = {s: p for s, p in paths.items() if s in args.symbols}
    btc_daily = None
    if args.extra:
        btc = load(paths.get("btcusdt", args.data_dir / "btcusdt_futures_5min.csv")).loc[args.start: args.end]
        btc_daily = np.log(btc["close"].resample("1D").last().dropna()).diff().dropna()
    rows = {}
    for symbol, path in paths.items():
        bars = load(path).loc[args.start: args.end]
        rows[symbol] = traits(bars, args.boll)
        if args.extra:
            rows[symbol].update(extra_traits(bars, btc_daily, args.data_dir / "funding" / f"{symbol}.csv"))
    table = pd.DataFrame(rows).T
    table.insert(0, "group", ["bad" if s in BAD else "neutral" if s in NEUTRAL else "good" if s in GOOD else "new"
                              for s in table.index])
    if args.result:
        result = pd.read_csv(args.result / "coin_groups.csv", index_col=0)
        table = table.join(result[["top_k", "top_25pct", "grid_median"]])
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    show = ["group", "quote_vol_musd", "daily_vol_pct", "band_width_pct", "fee_R_sl1", "breaks_per_month",
            "VR4", "VR16", "VR96", "fake_break_4"]
    print(f"Bollinger {args.boll} bars, dev 2.0, 15min")
    print(table.sort_values("group")[show + [c for c in ("top_k", "top_25pct") if c in table]].astype(
        {c: float for c in show[1:]}).round(3).to_string())
    extra = [c for c in EXTRA if c in table]
    if extra:
        print("\nextra traits:")
        print(table.sort_values("group")[["group"] + extra].astype({c: float for c in extra}).round(3).to_string())
    print("\nforward move after a downside break, in band widths (negative = short trend wins, gross):")
    print(table.sort_values("group")[["group"] + [f"down_{h}" for h in HORIZONS]].round(3).to_string())
    print("\nsame, minus the average move from any bar (drift removed):")
    print(table.sort_values("group")[["group"] + [f"down_adj_{h}" for h in HORIZONS]].round(3).to_string())
    print("\nforward move after an upside break (positive = long trend wins, negative = short fade wins):")
    print(table.sort_values("group")[["group"] + [f"up_{h}" for h in HORIZONS]].round(3).to_string())
    numeric = table.drop(columns="group").astype(float)
    print("\ngroup means:")
    print(numeric.groupby(table["group"]).mean().round(3).T.to_string())
    if "top_25pct" in table:
        print("\nSpearman across coins with the walk-forward top 25% / top 10:")
        corr = numeric.corr("spearman")[["top_25pct", "top_k"]].drop(["top_25pct", "top_k", "grid_median"])
        print(corr.round(2).to_string())


if __name__ == "__main__":
    main()
