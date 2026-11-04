"""Grid and martingale (DCA) backtests over the 47 coins of reference/data_5m, with a weekly-shuffle null.

Variants, criteria and fill rules are fixed in the plan of 2026-10-21 (INSIGHTS §9l); kernels in engine.py.
Each coin is an equal sleeve starting on its 31st day; the portfolio return is the mean of the active sleeves'
daily returns. Run from the repository root:

    uv run python reference/grid_martingale/run_grid_martingale.py --shuffles 20
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REFERENCE / "portfolio_trend"))
import engine  # noqa: E402
from daily_trend import metrics, newey_west_ols  # noqa: E402,F401

DATA_DIR = REFERENCE / "data_5m"
CACHE = HERE / "cache"
MAKER, TAKER, SLIP = 0.0002, 0.0005, 0.0001


@dataclass(frozen=True)
class Strategy:
    kind: str  # "grid" or "dca"
    exit_mode: int = 0  # grid: 0 = A1 reset at range exit, 1 = A2 hold
    n_levels: int = 20
    min_step: float = float(np.log(1.004))
    direction: int = 1  # dca: +1 long, -1 short
    n_so: int = 8
    first_dev: float = 0.02
    step_scale: float = 1.2
    volume_scale: float = 1.5
    take_profit: float = 0.02
    stop_beyond: float = 0.0  # dca: 0 = B1 hold, 0.10 = B2 stop
    path_mode: int = 0
    touch: bool = False
    one_side: bool = False


CORE = {
    "A1 grid, reset at range exit": Strategy("grid", exit_mode=0),
    "A2 grid, hold inventory": Strategy("grid", exit_mode=1),
    "B1 DCA long, no stop": Strategy("dca", direction=1),
    "B1 DCA short, no stop": Strategy("dca", direction=-1),
}
EXTRA = {
    "B2 DCA long, stop -10%": Strategy("dca", direction=1, stop_beyond=0.10),
    "B2 DCA short, stop +10%": Strategy("dca", direction=-1, stop_beyond=0.10),
}
SENSITIVITY = {"touch fills": {"touch": True}, "reversed path": {"path_mode": 1}, "one side per bar": {"one_side": True}}
SENSITIVE = ["A1 grid, reset at range exit", "B1 DCA long, no stop", "B1 DCA short, no stop"]


# ------------------------------------------------------------------------------------------------ data

@dataclass
class Coin:
    symbol: str
    ts: np.ndarray  # int64 ns, bar open
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    funding: np.ndarray  # rate applied at this bar's open
    days: pd.DatetimeIndex = field(default=None)
    day_idx: np.ndarray = field(default=None)
    vol: np.ndarray = field(default=None)  # previous-30-day std of daily log returns, per bar
    start: int = 0


def load_coin(symbol: str) -> Coin:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{symbol}.npz"
    if path.is_file():
        z = np.load(path)
        coin = Coin(symbol, z["ts"], z["o"], z["h"], z["l"], z["c"], z["funding"])
    else:
        bars = pd.read_csv(DATA_DIR / f"{symbol}_futures_5min.csv", usecols=["timestamp", "open", "high", "low",
                                                                               "close"], parse_dates=["timestamp"])
        ts = bars["timestamp"].to_numpy("datetime64[ns]").astype(np.int64)
        funding = np.zeros(len(ts))
        fpath = DATA_DIR / "funding" / f"{symbol}.csv"
        if fpath.is_file():
            rates = pd.read_csv(fpath, parse_dates=["timestamp"])
            ft = rates["timestamp"].dt.floor("5min").to_numpy("datetime64[ns]").astype(np.int64)
            pos = np.searchsorted(ts, ft)
            ok = (pos < len(ts)) & (ts[np.minimum(pos, len(ts) - 1)] == ft)
            np.add.at(funding, pos[ok], rates["last_funding_rate"].to_numpy()[ok])
        coin = Coin(symbol, ts, *(bars[k].to_numpy(float) for k in ("open", "high", "low", "close")), funding)
        np.savez(path, ts=coin.ts, o=coin.o, h=coin.h, l=coin.l, c=coin.c, funding=coin.funding)
    return prepare(coin)


def prepare(coin: Coin) -> Coin:
    stamps = pd.to_datetime(coin.ts)
    day = stamps.normalize()
    coin.days = pd.DatetimeIndex(day.unique())
    coin.day_idx = coin.days.get_indexer(day).astype(np.int64)
    daily_close = pd.Series(coin.c, index=stamps).groupby(day).last()
    vol = np.log(daily_close).diff().rolling(30).std().shift(1)  # only days before the bar's day
    coin.vol = vol.to_numpy()[coin.day_idx]
    coin.vol = np.nan_to_num(coin.vol, nan=0.0)
    first = coin.days[min(30, len(coin.days) - 1)]
    coin.start = int(np.searchsorted(coin.ts, first.value))
    return coin


def shuffled(coin: Coin, rng: np.random.Generator) -> Coin:
    """Permute whole bars (shape relative to the previous close) within each calendar week."""
    prev = np.concatenate([[coin.o[0]], coin.c[:-1]])
    rel = np.stack([coin.o / prev, coin.h / prev, coin.l / prev, coin.c / prev])
    week = coin.ts // (7 * 86_400 * 10**9)
    order = np.lexsort((rng.random(len(week)), week))  # random order inside each week, weeks in sequence
    rel = rel[:, order]
    close = coin.o[0] * np.exp(np.cumsum(np.log(rel[3])))
    prev_new = np.concatenate([[coin.o[0]], close[:-1]])
    new = Coin(coin.symbol, coin.ts, prev_new * rel[0], prev_new * rel[1], prev_new * rel[2], close, coin.funding)
    return prepare(new)


# ------------------------------------------------------------------------------------------------- run

def run_coin(coin: Coin, s: Strategy) -> tuple[pd.Series, np.ndarray]:
    args = (coin.o, coin.h, coin.l, coin.c)
    if s.kind == "grid":
        eq, counters = engine.grid_kernel(*args, coin.vol, coin.funding, coin.day_idx, len(coin.days), coin.start,
                                          s.n_levels, s.min_step, MAKER, TAKER, SLIP, s.exit_mode, s.path_mode,
                                          s.touch, s.one_side)
    else:
        eq, counters = engine.dca_kernel(*args, coin.funding, coin.day_idx, len(coin.days), coin.start, s.direction,
                                         s.n_so, s.first_dev, s.step_scale, s.volume_scale, s.take_profit,
                                         s.stop_beyond, MAKER, TAKER, SLIP, s.path_mode, s.touch, s.one_side)
    return pd.Series(eq, index=coin.days).dropna(), counters


def portfolio(equities: dict[str, pd.Series]) -> pd.Series:
    """Mean of the active sleeves' daily returns (a sleeve is active from its first equity day to its last)."""
    frame = pd.DataFrame(equities)
    returns = frame.pct_change(fill_method=None)
    returns = returns.where(frame.shift(1) > 0)  # a ruined sleeve stops contributing after its last loss
    return returns.mean(axis=1).dropna()


def market_returns(coins: dict[str, Coin]) -> pd.Series:
    closes = {s: pd.Series(c.c, index=pd.to_datetime(c.ts)).resample("1D").last() for s, c in coins.items()}
    frame = pd.DataFrame(closes)
    starts = {s: c.days[min(30, len(c.days) - 1)] for s, c in coins.items()}
    returns = frame.pct_change(fill_method=None)
    for symbol, first in starts.items():
        returns.loc[returns.index <= first, symbol] = np.nan
    return returns.mean(axis=1).dropna()


def sleeve_stats(equities: dict[str, pd.Series]) -> pd.DataFrame:
    rows = {}
    for symbol, eq in equities.items():
        dd = (eq / eq.cummax() - 1).min()
        rows[symbol] = {"final": eq.iloc[-1], "max_dd": dd, "ruined": bool((eq <= 0).any())}
    return pd.DataFrame(rows).T


def evaluate(name: str, port: pd.Series, market: pd.Series, sleeves: pd.DataFrame) -> dict:
    row = metrics(port, market)
    equity = (1 + port).cumprod()
    row.update({
        "total_return": equity.iloc[-1] - 1,
        "max_dd": (equity / equity.cummax() - 1).min(),
        "years_positive": int((port.groupby(port.index.year).apply(lambda r: r.mean() / r.std()) > 0).sum()),
        "years": port.index.year.nunique(),
        "sleeves_dd_gt_50": float((sleeves["max_dd"] < -0.5).mean()),
        "sleeves_ruined": int(sleeves["ruined"].sum()),
        "median_sleeve_final": float(sleeves["final"].median()),
    })
    return row


def run_all(coins: dict[str, Coin], strategies: dict[str, Strategy]) -> dict:
    results = {}
    for name, s in strategies.items():
        equities, counters = {}, {}
        for symbol, coin in coins.items():
            eq, cnt = run_coin(coin, s)
            if len(eq):
                equities[symbol], counters[symbol] = eq, cnt
        results[name] = {"equities": equities, "counters": pd.DataFrame(counters).T, "portfolio": portfolio(equities)}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=REFERENCE / "results" / "grid_martingale")
    parser.add_argument("--shuffles", type=int, default=20)
    parser.add_argument("--symbols", nargs="*")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)

    symbols = args.symbols or sorted(p.name.split("_futures")[0] for p in DATA_DIR.glob("*_futures_5min.csv"))
    coins = {s: load_coin(s) for s in symbols}
    print(f"{len(coins)} coins loaded", flush=True)
    market = market_returns(coins)

    strategies = dict(CORE) | dict(EXTRA)
    for base in SENSITIVE:
        for label, change in SENSITIVITY.items():
            strategies[f"{base} | {label}"] = replace(CORE[base], **change)
    results = run_all(coins, strategies)

    rows, years = {}, {}
    for name, res in results.items():
        sleeves = sleeve_stats(res["equities"])
        rows[name] = evaluate(name, res["portfolio"], market, sleeves)
        years[name] = res["portfolio"].groupby(res["portfolio"].index.year).apply(lambda r: (1 + r).prod() - 1)
    rows["benchmark: long all coins"] = metrics(market, market)
    table = pd.DataFrame(rows).T
    cols = ["sharpe", "total_return", "max_dd", "years_positive", "years", "alpha_ann", "alpha_t_nw", "beta",
            "corr_market", "sleeves_dd_gt_50", "sleeves_ruined", "median_sleeve_final"]
    print("\n=== portfolio metrics (net of fees and funding, mark-to-market)")
    print(table[cols].round(3).to_string())
    print("\n=== yearly portfolio return")
    print(pd.DataFrame(years).T.round(3).to_string())

    print("\n=== activity (sums over coins)")
    for name in list(CORE) + list(EXTRA):
        cnt = results[name]["counters"].sum()
        if results[name]["counters"].shape[1] == 5:
            print(f"{name}: fills {cnt[0]:,.0f}, range resets {cnt[1]:,.0f}, fees {cnt[2]:.2f}, funding paid "
                  f"{cnt[3]:.2f} (in sleeve-equity units), ruined {cnt[4]:.0f}")
        else:
            print(f"{name}: cycles {cnt[0]:,.0f}, take profits {cnt[1]:,.0f}, stops {cnt[2]:,.0f}, all {CORE.get(name, EXTRA.get(name)).n_so}"
                  f" SOs used {cnt[7]:,.0f}, mean SOs/closed cycle {cnt[6] / max(cnt[1] + cnt[2], 1):.2f}, "
                  f"fees {cnt[3]:.2f}, funding paid {cnt[4]:.2f}, ruined {cnt[5]:.0f}")
    for name in CORE:
        worst = sleeve_stats(results[name]["equities"]).sort_values("final").head(5)
        print(f"worst sleeves {name}: " + ", ".join(f"{s} {r.final:.2f} (dd {r.max_dd:.0%})"
                                                    for s, r in worst.iterrows()))

    null_rows = []
    if args.shuffles:
        print(f"\nweekly-shuffle null: {args.shuffles} shuffles x {len(coins)} coins ...", flush=True)
        rng = np.random.default_rng(11)
        for k in range(args.shuffles):
            fake = {s: shuffled(c, rng) for s, c in coins.items()}
            fake_market = market_returns(fake)
            res = run_all(fake, CORE)
            for name, r in res.items():
                m = metrics(r["portfolio"], fake_market)
                null_rows.append({"shuffle": k, "strategy": name, "sharpe": m["sharpe"],
                                  "total_return": (1 + r["portfolio"]).prod() - 1})
        null = pd.DataFrame(null_rows)
        null.to_csv(args.out / "shuffle_null.csv", index=False)
        print(null.groupby("strategy")[["sharpe", "total_return"]].describe(percentiles=[0.5, 0.95]).round(3)
              .to_string())

    print("\n=== pre-registered criteria (core strategies)")
    verdicts = {}
    for name, s in CORE.items():
        r = table.loc[name]
        c1 = r["sharpe"] >= 0.5 and r["years_positive"] >= 4
        c2 = r["max_dd"] >= -0.30 and r["sleeves_dd_gt_50"] <= 0.10
        if null_rows:
            dist = null[null["strategy"] == name]["sharpe"]
            c3 = bool(r["sharpe"] > np.percentile(dist, 95))
        else:
            c3 = False
        reversed_name = f"{name} | reversed path"
        rev_ok = table.loc[reversed_name, "sharpe"] > 0 if reversed_name in table.index else True
        c4 = r["alpha_ann"] > 0 and r["alpha_t_nw"] > 2 and rev_ok
        verdicts[name] = {"1 sharpe/years": c1, "2 drawdown": c2, "3 > shuffle 95th": c3, "4 alpha + reversed": c4,
                          "overall": c1 and c2 and c3 and c4}
    verdict = pd.DataFrame(verdicts).T
    print(verdict.replace({True: "PASS", False: "FAIL"}).to_string())

    table.to_csv(args.out / "metrics.csv")
    pd.DataFrame(years).T.to_csv(args.out / "by_year.csv")
    pd.DataFrame({k: v["portfolio"] for k, v in results.items()}).to_csv(args.out / "daily_returns.csv")
    pd.concat({k: sleeve_stats(results[k]["equities"]) for k in CORE}).to_csv(args.out / "per_coin.csv")
    verdict.to_csv(args.out / "criteria.csv")


if __name__ == "__main__":
    main()
