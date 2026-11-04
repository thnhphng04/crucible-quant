"""Anchored mean-reversion grid with a bounded martingale on forex crosses, holding <= 5 days (INSIGHTS §9r).

Pre-registered on 2026-10-21 before any result of this script, from step 1 (short_horizon_study.py: only baskets
opened toward the 5-day mean beat the time-of-day-shuffle null):
- Open at a full hour when the previous close is >= 1 daily sigma (previous 30 days) from the 5-day mean, in the
  direction back to the mean; one basket per pair at a time; none 20:30-23:00 UTC or after Friday 12:00 UTC.
- Step 0.5 daily sigma; up to 5 lots of 1 / 1.5 / 2 / 2.5 / 3; a lot is added at a 5m close beyond the next level
  (market) at least 4 hours after the last fill; each lot takes profit 1 step from its fill (LIFO); everything
  closes at the 5-day mean, at the stop 5 steps from the entry, after 5 days, or at the last bar before the weekend.
  Risk at the stop = 1% of the pair's sleeve. Sleeves per pair, portfolio = mean of sleeve daily returns.
- Costs: FXCM bid/ask, commission 35 USD per million per side, swap -1%/year both sides.
- Variants (reported, never picked): equal lots (no martingale), no pause, step 0.25 sigma.
- Nulls (core): random entries with the same number of signals per pair, month and direction (20 runs); prices
  permuted across days within each month and time-of-day slot (10 runs).
- PASS (dev, 14 crosses): portfolio daily Sharpe >= 0.5, max equity DD <= 20%, positive in >= 2/3 of years and in
  >= 2/3 of pairs, Sharpe > p95 of both nulls. Then once on EURJPY GBPJPY AUDJPY CADJPY NZDJPY: Sharpe >= 0.5,
  >= 2/3 of years and >= 3/5 pairs positive.

    uv run python reference/waka/anchor_grid.py --market dev
    uv run python reference/waka/anchor_grid.py --market jpy   # only if dev passes
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_waka as rw  # noqa: E402
import short_horizon_study as sh  # noqa: E402

wk = rw.wk
OUT = rw.OUT / "anchor_grid"
JPY = ["EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "NZDJPY"]
N_RANDOM, N_SHUFFLE = 20, 10


@dataclass(frozen=True)
class Grid:
    step: float = 0.5
    entry_sigma: float = 1.0
    lots: tuple = (1.0, 1.5, 2.0, 2.5, 3.0)
    pause_hours: float = 4.0
    stop_steps: float = 5.0
    risk: float = 0.01
    anchor_exit: bool = True


CORE = Grid()
VARIANTS = {"core": CORE, "equal lots": replace(CORE, lots=(1.0,) * 5), "no pause": replace(CORE, pause_hours=0.0),
            "step 0.25": replace(CORE, step=0.25)}


@dataclass
class Pair:
    name: str
    m: rw.Market
    ma: np.ndarray
    sigma: np.ndarray  # daily sigma (fraction), previous 30 days
    eligible: np.ndarray  # full-hour bars where a basket may open
    hold_end: np.ndarray


def prepare(name: str, m: rw.Market) -> Pair:
    c = pd.Series(m.bc[0])
    ma = c.rolling(5 * 288).mean().shift(1).to_numpy()
    stamps = pd.to_datetime(m.ts)
    minute = np.asarray(stamps.hour * 60 + stamps.minute)
    friday_late = (np.asarray(stamps.dayofweek) == 4) & (np.asarray(stamps.hour) >= 12)
    eligible = ((np.asarray(stamps.minute) == 0) & (np.arange(len(m.ts)) >= max(m.start, 5 * 288))
                & ~((minute >= 20 * 60 + 30) & (minute < 23 * 60)) & ~friday_late & (m.vol[0] > 0)
                & np.isfinite(ma))
    hold_end = np.minimum(np.arange(len(m.ts)) + 5 * 288, sh.week_ends(m.ts) - 1).astype(np.int64)
    return Pair(name, m, np.nan_to_num(ma), m.vol[0], eligible, hold_end)


def signals(p: Pair, g: Grid) -> np.ndarray:
    prev = np.concatenate([[p.m.bc[0, 0]], p.m.bc[0, :-1]])
    dev = (prev / np.where(p.ma > 0, p.ma, np.nan) - 1) / np.where(p.sigma > 0, p.sigma, np.nan)
    sig = np.zeros(len(prev), np.int8)
    sig[p.eligible & (dev <= -g.entry_sigma)] = 1
    sig[p.eligible & (dev >= g.entry_sigma)] = -1
    return sig


def random_like(p: Pair, sig: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    stamps = pd.to_datetime(p.m.ts)
    month = np.asarray(stamps.year * 12 + stamps.month)
    out = np.zeros_like(sig)
    for mo in np.unique(month[p.eligible]):
        idx = np.flatnonzero(p.eligible & (month == mo))
        n_long = int(((sig == 1) & (month == mo)).sum())
        n_short = int(((sig == -1) & (month == mo)).sum())
        pick = rng.permutation(idx)[: min(len(idx), n_long + n_short)]
        out[pick[:n_long]] = 1
        out[pick[n_long:]] = -1
    return out


def run(p: Pair, g: Grid, sig: np.ndarray | None = None, prices: tuple | None = None) -> dict:
    m = p.m
    o, h, l, c = (m.bo[0], m.bh[0], m.bl[0], m.bc[0]) if prices is None else prices
    ma = p.ma
    if prices is not None:  # anchor and step follow the permuted path
        ma = np.nan_to_num(pd.Series(c).rolling(5 * 288).mean().shift(1).to_numpy())
    if sig is None:
        prev = np.concatenate([[c[0]], c[:-1]])
        dev = (prev / np.where(ma > 0, ma, np.nan) - 1) / np.where(p.sigma > 0, p.sigma, np.nan)
        sig = np.zeros(len(c), np.int8)
        sig[p.eligible & (dev <= -g.entry_sigma)] = 1
        sig[p.eligible & (dev >= g.entry_sigma)] = -1
    dist = g.step * p.sigma * o
    eq, mn, log = wk.anchor_grid_kernel(
        np.ascontiguousarray(o), np.ascontiguousarray(h), np.ascontiguousarray(l), np.ascontiguousarray(c),
        np.ascontiguousarray(m.spread[0]), np.ascontiguousarray(m.carry_long[0]),
        np.ascontiguousarray(m.carry_short[0]), sig, dist, p.hold_end, ma, m.day_idx, len(m.days),
        np.asarray(g.lots, float), int(g.pause_hours * 12), g.stop_steps, g.risk, rw.FX_COMM, g.anchor_exit)
    daily = pd.DataFrame({"equity": eq, "min_equity": mn}, index=m.days).dropna()
    daily = daily[daily.index >= m.days[min(len(m.days) - 1, 6)]]
    baskets = pd.DataFrame(log, columns=["open", "close", "dir", "lots_used", "pnl", "reason"])
    return {"daily": daily, "baskets": baskets}


def portfolio(results: dict[str, dict]) -> tuple[pd.Series, pd.Series]:
    eq = pd.DataFrame({k: v["daily"]["equity"] for k, v in results.items()})
    mn = pd.DataFrame({k: v["daily"]["min_equity"] for k, v in results.items()})
    r = eq.pct_change(fill_method=None)
    r_min = mn / eq.shift(1) - 1
    cal = pd.date_range(r.index[0], r.index[-1], freq="D")
    return r.mean(axis=1).reindex(cal).fillna(0.0), np.minimum(r_min.mean(axis=1), r.mean(axis=1)).reindex(cal).fillna(0.0)


def stats(r: pd.Series, r_min: pd.Series, results: dict[str, dict]) -> dict:
    eq = (1 + r).cumprod()
    low = eq.shift(1).fillna(1.0) * (1 + r_min)
    by_year = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    totals = {k: v["daily"]["equity"].iloc[-1] / v["daily"]["equity"].iloc[0] - 1 for k, v in results.items()}
    b = pd.concat([v["baskets"] for v in results.values()])
    years = len(r) / 365.25
    return {"sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0,
            "ann_return": float(eq.iloc[-1] ** (1 / years) - 1), "max_dd": float((low / eq.cummax() - 1).min()),
            "years_positive": int((by_year > 0).sum()), "years": len(by_year),
            "pairs_positive": int(sum(v > 0 for v in totals.values())), "pairs": len(totals),
            "baskets_per_pair_year": len(b) / len(results) / years,
            "basket_win_rate": float((b["pnl"] > 0).mean()), "mean_lots_used": float(b["lots_used"].mean()),
            "share_stop": float((b["reason"] == 2).mean()), "share_time": float((b["reason"] == 4).mean()),
            "share_anchor": float((b["reason"] == 3).mean())}, by_year, totals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["dev", "jpy"], default="dev")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    names = sh.DEV14 if args.market == "dev" else JPY
    pairs = {n: prepare(n, rw.load_fx([n], n.lower())) for n in names}
    rows, years = [], {}
    core = None
    for vname, g in VARIANTS.items():
        res = {n: run(p, g) for n, p in pairs.items()}
        st, by_year, totals = stats(*portfolio(res), res)
        rows.append({"variant": vname, **st})
        years[vname] = by_year
        if vname == "core":
            core = (st, res, totals)
            pd.DataFrame({"total_return": totals}).to_csv(OUT / f"pairs_{args.market}.csv")
            r, _ = portfolio(res)
            (1 + r).cumprod().to_csv(OUT / f"equity_{args.market}.csv")
        print(f"{args.market} {vname:11s} sharpe {st['sharpe']:+.2f} ann {st['ann_return']:+.4f} dd {st['max_dd']:+.3f} "
              f"years+ {st['years_positive']}/{st['years']} pairs+ {st['pairs_positive']}/{st['pairs']} "
              f"baskets/pair/yr {st['baskets_per_pair_year']:.0f} win {st['basket_win_rate']:.2f} "
              f"stop {st['share_stop']:.3f} time {st['share_time']:.2f}", flush=True)
    st = core[0]
    criteria = {"market": args.market, **{f"core_{k}": v for k, v in st.items()}}
    if args.market == "dev":
        rng = np.random.default_rng(41)
        rand, shuf = [], []
        for i in range(N_RANDOM):
            res = {n: run(p, CORE, random_like(p, signals(p, CORE), rng)) for n, p in pairs.items()}
            rand.append(stats(*portfolio(res), res)[0]["sharpe"])
        for i in range(N_SHUFFLE):
            res = {}
            for n, p in pairs.items():
                hh, ll, cc, oo = sh.tod_shuffle(p.m, 0, rng)
                res[n] = run(p, CORE, None, (oo, hh, ll, cc))
            shuf.append(stats(*portfolio(res), res)[0]["sharpe"])
        criteria.update({
            "random_p95": float(np.quantile(rand, 0.95)), "random_median": float(np.median(rand)),
            "shuffle_p95": float(np.quantile(shuf, 0.95)), "shuffle_median": float(np.median(shuf)),
            "1 sharpe >= 0.5": st["sharpe"] >= 0.5, "2 max DD <= 20%": st["max_dd"] >= -0.20,
            "3 years and pairs >= 2/3": st["years_positive"] >= 2 / 3 * st["years"]
            and st["pairs_positive"] >= 2 / 3 * st["pairs"],
            "4 beats both nulls": st["sharpe"] > np.quantile(rand, 0.95) and st["sharpe"] > np.quantile(shuf, 0.95)})
        criteria["PASS"] = all(v for k, v in criteria.items() if k[:2] in ("1 ", "2 ", "3 ", "4 "))
    else:
        criteria["PASS"] = (st["sharpe"] >= 0.5 and st["years_positive"] >= 2 / 3 * st["years"]
                            and st["pairs_positive"] >= 3)
    pd.DataFrame(rows).to_csv(OUT / f"metrics_{args.market}.csv", index=False)
    pd.DataFrame(years).to_csv(OUT / f"by_year_{args.market}.csv")
    pd.DataFrame([criteria]).to_csv(OUT / f"criteria_{args.market}.csv", index=False)
    print(pd.DataFrame(years).round(3).to_string())
    print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in criteria.items()}, flush=True)


if __name__ == "__main__":
    main()
