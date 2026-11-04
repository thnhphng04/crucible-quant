"""Daily time-series momentum portfolio on the Binance USD-M perps of reference/data_5m.

Design (fixed before the first run, no parameter fitting; plan of 2026-10-21, INSIGHTS §9k):

- Daily close = last 5min close of each UTC day. Orders for signal day t fill at the 5min close at 01:00 UTC of
  day t+1 (one hour after the close the signal saw); P&L runs from fill to fill.
- Universe: coins with >= MIN_DAYS of daily closes; on the first day of each month the TOP_N by the median daily
  quote volume of the last 30 days. A coin whose data ends (delisting) is closed at its last price at once.
- Signal: mean over L in LOOKBACKS of sign(close_t / close_{t-L} - 1), in {-1, -1/3, 1/3, 1}.
- Volatility: EWMA (half-life VOL_HALFLIFE days) standard deviation of daily log returns, annualized x sqrt(365).
- Target weight: w = signal x (ASSET_VOL / vol) / N (N = universe size); gross sum |w| capped at GROSS_CAP.
- Rebalance every Monday fill (signal from the Sunday close); units are held in between (weights drift).
- Costs: COST per side on traded notional. Funding: actual rates; a long pays a positive rate, a short receives.

Run from the repository root:

    uv run python reference/portfolio_trend/daily_trend.py --out reference/results/portfolio_trend_daily
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
DATA_DIR = REFERENCE / "data_5m"
CACHE = HERE / "cache"


@dataclass(frozen=True)
class Config:
    lookbacks: tuple[int, ...] = (20, 60, 120)
    top_n: int = 30
    min_days: int = 150
    volume_days: int = 30
    vol_halflife: float = 30.0
    asset_vol: float = 0.40
    gross_cap: float = 2.0
    rebalance: str = "weekly"  # "weekly" (Monday fills) or "daily"
    cost: float = 0.0006  # per side, on traded notional
    funding: bool = True
    sides: str = "both"  # "both", "long", "short"


# ----------------------------------------------------------------------------------------------- data

@dataclass
class Panel:
    close: pd.DataFrame  # daily close, index = signal day t
    fill: pd.DataFrame  # fill price at 01:00 UTC of day t+1 (forward-filled after the coin's last bar)
    alive: pd.DataFrame  # True while the fill time is not after the coin's last bar
    quote_volume: pd.DataFrame  # daily quote volume of day t
    funding: pd.DataFrame  # sum of funding rates paid in (fill_{t-1}, fill_t]


def _coin_daily(path: Path) -> dict[str, pd.Series]:
    bars = pd.read_csv(path, usecols=["timestamp", "close", "quote_volume"], parse_dates=["timestamp"])
    bars = bars.set_index("timestamp").sort_index()
    close = bars["close"]
    daily_close = close.resample("1D").last().dropna()
    days = daily_close.index
    fill_times = days + pd.Timedelta(days=1, minutes=55)  # the 5min bar opening 00:55 closes at 01:00
    fill = pd.Series(close.reindex(fill_times, method="ffill").to_numpy(), index=days)
    last_bar = close.index[-1]
    alive = pd.Series(fill_times <= last_bar, index=days)
    volume = bars["quote_volume"].resample("1D").sum().reindex(days)
    return {"close": daily_close, "fill": fill, "alive": alive, "quote_volume": volume}


def _coin_funding(path: Path, days: pd.DatetimeIndex) -> pd.Series:
    if not path.is_file():
        return pd.Series(0.0, index=days)
    rates = pd.read_csv(path, parse_dates=["timestamp"])
    # a payment at time f belongs to the holding period (fill_{t-1}, fill_t] = (t 01:00, t+1 01:00]
    day = (rates["timestamp"] - pd.Timedelta(hours=1) - pd.Timedelta(nanoseconds=1)).dt.floor("D")
    return rates.groupby(day)["last_funding_rate"].sum().reindex(days, fill_value=0.0)


def load_panel(data_dir: Path = DATA_DIR, refresh: bool = False) -> Panel:
    CACHE.mkdir(exist_ok=True)
    names = ["close", "fill", "alive", "quote_volume", "funding"]
    # CSV cache: small, and readable by both the uv and the anaconda pyarrow/pandas versions
    if not refresh and all((CACHE / f"{n}.csv").is_file() for n in names):
        frames = {n: pd.read_csv(CACHE / f"{n}.csv", index_col=0, parse_dates=True) for n in names}
        frames["alive"] = frames["alive"].astype(bool)
        return Panel(*(frames[n] for n in names))
    parts: dict[str, dict[str, pd.Series]] = {n: {} for n in names}
    for path in sorted(data_dir.glob("*_futures_5min.csv")):
        symbol = path.name.split("_futures")[0]
        daily = _coin_daily(path)
        for key, series in daily.items():
            parts[key][symbol] = series
        parts["funding"][symbol] = _coin_funding(data_dir / "funding" / f"{symbol}.csv", daily["close"].index)
        print(f"loaded {symbol}: {len(daily['close'])} days", flush=True)
    index = pd.date_range(min(s.index[0] for s in parts["close"].values()),
                          max(s.index[-1] for s in parts["close"].values()), freq="1D")
    frames = {n: pd.DataFrame(parts[n]).reindex(index) for n in names}
    frames["fill"] = frames["fill"].ffill()
    frames["alive"] = frames["alive"].astype("boolean").fillna(False).astype(bool)
    frames["funding"] = frames["funding"].fillna(0.0)
    for name, frame in frames.items():
        frame.to_csv(CACHE / f"{name}.csv")
    return Panel(*(frames[n] for n in names))


# -------------------------------------------------------------------------------------------- signals

def universe(panel: Panel, cfg: Config) -> pd.DataFrame:
    """Boolean day x coin: in the month's top-N by trailing median quote volume and still alive."""
    history = panel.close.notna().cumsum()
    volume = panel.quote_volume.rolling(cfg.volume_days, min_periods=cfg.volume_days // 2).median()
    eligible = (history >= cfg.min_days) & panel.alive & panel.close.notna()
    out = pd.DataFrame(False, index=panel.close.index, columns=panel.close.columns)
    month_starts = panel.close.index[panel.close.index.is_month_start]
    for day in month_starts:
        ranked = volume.loc[day][eligible.loc[day]].sort_values(ascending=False)
        out.loc[day, ranked.index[: cfg.top_n]] = True
    out = out.astype(float)
    out.loc[~out.index.isin(month_starts)] = np.nan  # membership is decided on month starts and held
    return out.ffill().fillna(0.0).astype(bool) & panel.alive


def signals(close: pd.DataFrame, lookbacks: tuple[int, ...]) -> pd.DataFrame:
    parts = [np.sign(close / close.shift(lb) - 1.0) for lb in lookbacks]
    return sum(parts) / len(parts)


def ewma_vol(close: pd.DataFrame, halflife: float) -> pd.DataFrame:
    returns = np.log(close).diff()
    return returns.ewm(halflife=halflife, min_periods=60).std() * np.sqrt(365)


def target_weights(panel: Panel, cfg: Config, signal: pd.DataFrame | None = None) -> pd.DataFrame:
    members = universe(panel, cfg)
    if signal is None:
        signal = signals(panel.close, cfg.lookbacks)
    if cfg.sides == "long":
        signal = signal.clip(lower=0.0)
    elif cfg.sides == "short":
        signal = signal.clip(upper=0.0)
    vol = ewma_vol(panel.close, cfg.vol_halflife)
    count = members.sum(axis=1).replace(0, np.nan)
    weights = (signal * (cfg.asset_vol / vol)).div(count, axis=0).where(members).fillna(0.0)
    gross = weights.abs().sum(axis=1)
    scale = (cfg.gross_cap / gross).clip(upper=1.0).fillna(1.0)
    return weights.mul(scale, axis=0)


# ------------------------------------------------------------------------------------------ simulation

def rebalance_days(index: pd.DatetimeIndex, cfg: Config) -> np.ndarray:
    if cfg.rebalance == "daily":
        return np.ones(len(index), dtype=bool)
    return np.asarray(index.dayofweek == 6)  # Sunday signal -> Monday 01:00 fill


def simulate(panel: Panel, weights: np.ndarray, cfg: Config) -> dict[str, np.ndarray]:
    """weights: (T, K) or (T, S, K) target weights at each signal day. Returns per-day arrays (leading axis T)."""
    fill = panel.fill.to_numpy()
    alive = panel.alive.to_numpy()
    funding = panel.funding.to_numpy() if cfg.funding else np.zeros_like(fill)
    ret = np.zeros_like(fill)
    ret[1:] = np.nan_to_num(fill[1:] / fill[:-1] - 1.0)
    batch = weights.ndim == 3
    shape = weights.shape[1:]
    holdings = np.zeros(shape)
    equity = np.ones(shape[:-1]) if batch else np.array(1.0)
    rebalance = rebalance_days(panel.close.index, cfg)
    T = len(fill)
    out = {k: np.zeros((T,) + shape[:-1]) for k in
           ("equity", "gross_pnl", "long_pnl", "short_pnl", "funding_pnl", "cost", "turnover", "net_exposure",
            "gross_exposure")}
    coin_pnl = None if batch else np.zeros((T, fill.shape[1]))
    for t in range(T):
        if t:
            pnl = holdings * ret[t]
            fund = -holdings * funding[t]
            out["gross_pnl"][t] = pnl.sum(-1)
            out["long_pnl"][t] = np.where(holdings > 0, pnl, 0.0).sum(-1)
            out["short_pnl"][t] = np.where(holdings < 0, pnl, 0.0).sum(-1)
            out["funding_pnl"][t] = fund.sum(-1)
            if coin_pnl is not None:
                coin_pnl[t] = pnl + fund
            holdings = holdings * (1.0 + ret[t])
            equity = equity + pnl.sum(-1) + fund.sum(-1)
        target = holdings.copy()
        if rebalance[t]:
            target = weights[t] * equity[..., None] if batch else weights[t] * equity
        target = np.where(alive[t], target, 0.0)  # delisted coins are closed at their last price
        trade = np.abs(target - holdings).sum(-1)
        cost = cfg.cost * trade
        if coin_pnl is not None:
            coin_pnl[t] -= cfg.cost * np.abs(target - holdings)
        equity = equity - cost
        holdings = target
        out["cost"][t] = cost
        out["turnover"][t] = trade / equity
        out["net_exposure"][t] = holdings.sum(-1) / equity
        out["gross_exposure"][t] = np.abs(holdings).sum(-1) / equity
        out["equity"][t] = equity
    if coin_pnl is not None:
        out["coin_pnl"] = coin_pnl
    return out


def daily_frame(panel: Panel, sim: dict[str, np.ndarray]) -> pd.DataFrame:
    frame = pd.DataFrame({k: v for k, v in sim.items() if k != "coin_pnl"}, index=panel.close.index)
    previous = frame["equity"].shift(1).fillna(1.0)
    for key in ("gross_pnl", "long_pnl", "short_pnl", "funding_pnl", "cost"):
        frame[key] = frame[key] / previous
    frame["return"] = frame["equity"] / previous - 1.0
    return frame


# --------------------------------------------------------------------------------------------- metrics

def newey_west_ols(y: np.ndarray, x: np.ndarray, lags: int = 10) -> tuple[np.ndarray, np.ndarray]:
    """OLS of y on [1, x] with Newey-West (Bartlett) standard errors; returns (coef, t-stats)."""
    X = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    xu = X * resid[:, None]
    S = xu.T @ xu
    for lag in range(1, lags + 1):
        gamma = xu[lag:].T @ xu[:-lag]
        S += (1 - lag / (lags + 1)) * (gamma + gamma.T)
    inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(inv @ S @ inv))
    return coef, coef / se


def metrics(returns: pd.Series, market: pd.Series | None = None) -> dict[str, float]:
    r = returns.dropna()
    ann = np.sqrt(365)
    sharpe = r.mean() / r.std() * ann
    downside = r[r < 0].std()
    scaled = r * (0.20 / (r.std() * ann))  # the same strategy run at 20% annual volatility
    equity = (1 + scaled).cumprod()
    years = len(r) / 365
    row = {
        "sharpe": sharpe,
        "sortino": r.mean() / downside * ann if downside > 0 else np.nan,
        "cagr_at_20vol": equity.iloc[-1] ** (1 / years) - 1,
        "max_dd_at_20vol": (equity / equity.cummax() - 1).min(),
        "raw_ann_return": r.mean() * 365,
        "raw_ann_vol": r.std() * ann,
    }
    row["calmar_at_20vol"] = row["cagr_at_20vol"] / -row["max_dd_at_20vol"]
    if market is not None:
        joined = pd.concat([r, market], axis=1, join="inner").dropna()
        coef, tstat = newey_west_ols(joined.iloc[:, 0].to_numpy(), joined.iloc[:, 1].to_numpy())
        row.update({"alpha_ann": coef[0] * 365, "alpha_t_nw": tstat[0], "beta": coef[1], "beta_t_nw": tstat[1],
                    "corr_market": joined.iloc[:, 0].corr(joined.iloc[:, 1])})
    return row


def by_year(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.groupby(frame.index.year)
    return pd.DataFrame({
        "days": grouped["return"].count(),
        "return": grouped["return"].apply(lambda r: (1 + r).prod() - 1),
        "sharpe": grouped["return"].apply(lambda r: r.mean() / r.std() * np.sqrt(365)),
        "gross_pnl": grouped["gross_pnl"].sum(),
        "long_pnl": grouped["long_pnl"].sum(),
        "short_pnl": grouped["short_pnl"].sum(),
        "funding_pnl": grouped["funding_pnl"].sum(),
        "cost": -grouped["cost"].sum(),
        "avg_net_exposure": grouped["net_exposure"].mean(),
        "avg_gross_exposure": grouped["gross_exposure"].mean(),
        "turnover_per_year": grouped["turnover"].sum(),
    })


def random_sign_benchmark(panel: Panel, weights: pd.DataFrame, cfg: Config, n: int = 1000, seed: int = 7,
                          batch: int = 100) -> np.ndarray:
    """Sharpe of n runs with the same |w| and a random sign per coin, redrawn at every rebalance."""
    rng = np.random.default_rng(seed)
    magnitude = weights.abs().to_numpy()
    rebalance = rebalance_days(weights.index, cfg)
    block = np.cumsum(rebalance)  # signs are constant between rebalances
    start = int(np.argmax(magnitude.sum(axis=1) > 0))
    sharpes = []
    for first in range(0, n, batch):
        size = min(batch, n - first)
        signs = rng.choice([-1.0, 1.0], size=(block.max() + 1, size, magnitude.shape[1]))
        w = magnitude[:, None, :] * signs[block]
        sim = simulate(panel, w, cfg)
        equity = sim["equity"][start:]
        r = np.diff(equity, axis=0) / equity[:-1]
        sharpes.append(r.mean(0) / r.std(0) * np.sqrt(365))
    return np.concatenate(sharpes)


# ------------------------------------------------------------------------------------------------ main

VARIANTS: dict[str, dict] = {
    "main (ensemble 20/60/120, long+short)": {},
    "long only": {"sides": "long"},
    "short only": {"sides": "short"},
    "L=20 only": {"lookbacks": (20,)},
    "L=60 only": {"lookbacks": (60,)},
    "L=120 only": {"lookbacks": (120,)},
    "cost x2": {"cost": 0.0012},
    "daily rebalance": {"rebalance": "daily"},
}


def run(panel: Panel, cfg: Config, signal: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    weights = target_weights(panel, cfg, signal)
    sim = simulate(panel, weights.to_numpy(), cfg)
    frame = daily_frame(panel, sim)
    start = weights.abs().sum(axis=1).gt(0).idxmax()
    return frame.loc[start:], weights, sim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=REFERENCE / "results" / "portfolio_trend_daily")
    parser.add_argument("--refresh", action="store_true", help="Rebuild the daily cache from the 5min CSVs")
    parser.add_argument("--random-runs", type=int, default=1000)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)

    panel = load_panel(refresh=args.refresh)
    base = Config()
    market_frame, _, _ = run(panel, base, signal=pd.DataFrame(1.0, index=panel.close.index,
                                                              columns=panel.close.columns))
    market = market_frame["return"]
    btc = panel.fill["btcusdt"].pct_change()

    rows, frames = {}, {}
    for name, change in VARIANTS.items():
        frame, weights, sim = run(panel, replace(base, **change))
        frames[name] = frame
        rows[name] = metrics(frame["return"], market)
        if name.startswith("main"):
            main_frame, main_weights, main_sim = frame, weights, sim
    rows["benchmark: long all coins (vol-weighted)"] = metrics(market, market)
    rows["benchmark: BTC buy and hold"] = metrics(btc.loc[main_frame.index[0]:], market)
    table = pd.DataFrame(rows).T
    print("\n=== metrics (net of costs and funding; daily returns, 365-day annualization)")
    print(table.round(3).to_string())

    years = by_year(main_frame)
    print("\n=== main by year")
    print(years.round(3).to_string())
    split = main_frame.assign(era=np.where(main_frame.index.year <= 2022, "2021-22", "2023-26"))
    print("\n=== main by era:", {e: round(metrics(g["return"])["sharpe"], 2) for e, g in split.groupby("era")})

    coin_pnl = pd.DataFrame(main_sim["coin_pnl"], index=panel.close.index, columns=panel.close.columns)
    coin_pnl = coin_pnl.loc[main_frame.index[0]:].div(main_frame["equity"].shift(1).fillna(1.0), axis=0)
    universe_file = DATA_DIR / "universe.csv"
    new = set(pd.read_csv(universe_file)["symbol"].str.lower()) if universe_file.is_file() else set()
    by_set = {"new 30 coins": coin_pnl[[c for c in coin_pnl if c in new]].sum(axis=1),
              "old 17 coins": coin_pnl[[c for c in coin_pnl if c not in new]].sum(axis=1)}
    print("\n=== main P&L contribution by coin set (sum of daily return contributions, before cost of other set)")
    print({k: round(float(v.sum()), 3) for k, v in by_set.items()})
    per_coin = coin_pnl.sum().sort_values()
    print("worst coins:", per_coin.head(5).round(3).to_dict())
    print("best coins:", per_coin.tail(5).round(3).to_dict())

    print(f"\nrandom-sign benchmark: {args.random_runs} runs ...", flush=True)
    random_sharpes = random_sign_benchmark(panel, main_weights, base, n=args.random_runs)
    main_sharpe = table.loc[next(iter(VARIANTS)), "sharpe"]
    percentile = float((random_sharpes < main_sharpe).mean() * 100)
    print(f"random Sharpe median {np.median(random_sharpes):.2f}, 95th pct {np.percentile(random_sharpes, 95):.2f}; "
          f"main {main_sharpe:.2f} -> percentile {percentile:.1f}")

    main_row = table.loc[next(iter(VARIANTS))]
    single = table.loc[["L=20 only", "L=60 only", "L=120 only"], "sharpe"]
    checks = {
        "1. Sharpe >= 0.5 and > 0 in >= 4/6 years": bool(main_row["sharpe"] >= 0.5 and (years["sharpe"] > 0).sum() >= 4),
        "2. alpha vs long-all > 0 with NW t > 2": bool(main_row["alpha_ann"] > 0 and main_row["alpha_t_nw"] > 2),
        "3. Sharpe >= 95th pct of random signs": bool(percentile >= 95),
        "4. >= 2/3 single-L Sharpe > 0 and cost x2 Sharpe > 0":
            bool((single > 0).sum() >= 2 and table.loc["cost x2", "sharpe"] > 0),
    }
    print("\n=== pre-registered criteria")
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("OVERALL:", "PASS" if all(checks.values()) else "FAIL")

    table.to_csv(args.out / "metrics.csv")
    years.to_csv(args.out / "by_year.csv")
    main_frame.to_csv(args.out / "daily.csv")
    pd.DataFrame({k: v["return"] for k, v in frames.items()}).to_csv(args.out / "variant_returns.csv")
    pd.Series(random_sharpes, name="sharpe").to_csv(args.out / "random_benchmark.csv", index=False)
    main_weights.loc[main_weights.index.dayofweek == 6].tail(20).to_csv(args.out / "weights_sample.csv")
    per_coin.to_csv(args.out / "per_coin_pnl.csv")
    pd.Series(checks).to_csv(args.out / "criteria.csv")


if __name__ == "__main__":
    main()
