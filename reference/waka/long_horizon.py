"""Forex holding up to 4 weeks: long-horizon anchored grid vs daily time-series momentum (INSIGHTS §9s).

Design, costs, nulls and criteria are frozen in reference/results/fx_long_horizon_preregistration.md.

    uv run python reference/waka/long_horizon.py --market dev
    uv run python reference/waka/long_horizon.py --market jpy --only grid|momentum   # only if dev passes
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
DATA = HERE.parent / "data_fx_long"
OUT = rw.OUT / "long_horizon"
JPY = ["EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "NZDJPY"]
N_NULL = 20
BARS_PER_DAY = 24


@dataclass(frozen=True)
class GridCfg:
    anchor_days: int = 120
    entry_sigma: float = 1.0
    step: float = 0.5
    lots: tuple = (1.0, 1.5, 2.0, 2.5, 3.0)
    pause_bars: int = 48
    stop_steps: float = 5.0
    hold_bars: int = 480
    risk: float = 0.01


@dataclass(frozen=True)
class MomCfg:
    lookback: int = 63
    vol_target: float = 0.10


GRID = {"core": GridCfg(), "equal lots": replace(GridCfg(), lots=(1.0,) * 5),
        "entry 1.5 sigma": replace(GridCfg(), entry_sigma=1.5), "anchor 250d": replace(GridCfg(), anchor_days=250),
        "Waka lots 1-1-2-4-8": replace(GridCfg(), lots=(1.0, 1.0, 2.0, 4.0, 8.0))}
MOM = {"core 63d": MomCfg(), "21d": MomCfg(21), "126d": MomCfg(126), "252d": MomCfg(252)}


def fxcm_spread(pair: str) -> float:
    """Median FXCM ask - bid as a fraction of price."""
    f = pd.read_csv(rw.FX_DIR / f"{pair.lower()}_5min.csv", usecols=["bid_close", "ask_close"])
    return float(np.median((f["ask_close"] - f["bid_close"]) / f["bid_close"]))


def load(pair: str) -> rw.Market:
    f = pd.read_csv(DATA / f"{pair.lower()}_futures_1h.csv", parse_dates=["timestamp"])
    ts = f["timestamp"].to_numpy("datetime64[ns]").astype(np.int64)
    one = lambda k: f[k].to_numpy(float)[None, :]  # noqa: E731
    carry_long, carry_short = rw.swap_carry([pair], ts, None)
    spread = np.full((1, len(ts)), fxcm_spread(pair)) * f["close"].to_numpy(float)[None, :]
    m = rw.Market(pair, "fx", [pair], ts, one("open"), one("high"), one("low"), one("close"), spread, carry_long,
                  carry_short)
    return rw.prepare(m)


def week_shuffle(m: rw.Market, rng: np.random.Generator) -> rw.Market:
    """Whole ISO weeks permuted within each calendar year; bar shapes relative to the previous close."""
    stamps = pd.to_datetime(m.ts)
    iso = stamps.isocalendar()
    week = np.asarray(iso["year"] * 100 + iso["week"])
    year = np.asarray(stamps.year)
    prev = np.concatenate([[m.bo[0, 0]], m.bc[0, :-1]])
    rel = np.stack([m.bo[0] / prev, m.bh[0] / prev, m.bl[0] / prev, m.bc[0] / prev])
    order = []
    for y in np.unique(year):
        weeks = pd.unique(week[year == y])
        for w in rng.permutation(weeks):
            order.append(np.flatnonzero((week == w) & (year == y)))
    order = np.concatenate(order)
    rel = rel[:, order]
    close = m.bo[0, 0] * np.exp(np.cumsum(np.log(rel[3])))
    prev_new = np.concatenate([[m.bo[0, 0]], close[:-1]])
    new = rw.Market(m.name, "fx", m.symbols, m.ts, (prev_new * rel[0])[None], (prev_new * rel[1])[None],
                    (prev_new * rel[2])[None], close[None], m.spread[:, order], m.carry_long, m.carry_short)
    return rw.prepare(new)


def daily(m: rw.Market) -> pd.DataFrame:
    stamps = pd.to_datetime(m.ts)
    close = pd.Series(m.bc[0], index=stamps).groupby(stamps.normalize()).last()
    r = np.log(close).diff()
    return pd.DataFrame({"close": close, "sigma_d": r.rolling(60).std()})


# ------------------------------------------------------------------------------------------- A. grid

def run_grid(m: rw.Market, g: GridCfg) -> dict:
    d = daily(m)
    d["anchor"] = d["close"].rolling(g.anchor_days).mean()
    d["sigma_m"] = d["sigma_d"] * np.sqrt(21)
    d["dev"] = (d["close"] / d["anchor"] - 1) / d["sigma_m"]
    known = d.shift(1)  # values known at the previous day's close
    stamps = pd.to_datetime(m.ts)
    day = stamps.normalize()
    k = known.reindex(day)
    first_bar = np.r_[True, np.asarray(day[1:] != day[:-1])]
    dev = k["dev"].to_numpy()
    sig = np.zeros(len(m.ts), np.int8)
    ok = first_bar & np.isfinite(dev)
    sig[ok & (dev <= -g.entry_sigma)] = 1
    sig[ok & (dev >= g.entry_sigma)] = -1
    sigma_m = np.nan_to_num(k["sigma_m"].to_numpy())
    dist = g.step * sigma_m * m.bo[0]
    ma = np.nan_to_num(k["anchor"].to_numpy())
    hold_end = np.minimum(np.arange(len(m.ts)) + g.hold_bars, len(m.ts) - 1).astype(np.int64)
    c = lambda a: np.ascontiguousarray(a)  # noqa: E731
    eq, mn, log = wk.anchor_grid_kernel(c(m.bo[0]), c(m.bh[0]), c(m.bl[0]), c(m.bc[0]), c(m.spread[0]),
                                        c(m.carry_long[0]), c(m.carry_short[0]), sig, dist, hold_end, ma, m.day_idx,
                                        len(m.days), np.asarray(g.lots, float), g.pause_bars, g.stop_steps, g.risk,
                                        rw.FX_COMM, True)
    out = pd.DataFrame({"equity": eq, "min_equity": mn}, index=m.days).dropna()
    first = d["anchor"].first_valid_index()
    out = out[out.index > first]
    return {"daily": out, "baskets": pd.DataFrame(log, columns=["open", "close", "dir", "lots_used", "pnl",
                                                                  "reason"])}


def grid_portfolio(results: dict) -> tuple[pd.Series, pd.Series]:
    eq = pd.DataFrame({k: v["daily"]["equity"] for k, v in results.items()})
    mn = pd.DataFrame({k: v["daily"]["min_equity"] for k, v in results.items()})
    r = eq.pct_change(fill_method=None)
    r_min = np.minimum(mn / eq.shift(1) - 1, r)
    return r.mean(axis=1).dropna(), r_min.mean(axis=1).dropna()


# --------------------------------------------------------------------------------------- B. momentum

def run_momentum(m: rw.Market, cfg: MomCfg) -> pd.Series:
    d = daily(m)
    r = d["close"].pct_change()
    signal = np.sign(np.log(d["close"] / d["close"].shift(cfg.lookback)))
    weight = signal * cfg.vol_target / (d["sigma_d"] * np.sqrt(252))
    monday = pd.Series(d.index.dayofweek == 0, index=d.index)
    held = weight.where(monday).ffill().shift(1)  # decided at a Monday close, held from Tuesday
    spread_frac = float(np.median(m.spread[0] / m.bc[0]))
    unit_cost = spread_frac / 2 + rw.FX_COMM
    cost = held.diff().abs() * unit_cost + held.abs() * 0.01 / 252
    first = d["sigma_d"].first_valid_index()
    out = (held * r - cost).dropna()
    return out[out.index > first + pd.Timedelta(days=cfg.lookback * 1.5)]


# ------------------------------------------------------------------------------------------- shared

def stats(r: pd.Series, r_min: pd.Series | None, per_pair_totals: dict[str, float]) -> dict:
    eq = (1 + r).cumprod()
    low = eq.shift(1).fillna(1.0) * (1 + (r_min if r_min is not None else r).reindex(r.index).fillna(0.0))
    by_year = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    years = (r.index[-1] - r.index[0]).days / 365.25
    return {"sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0,
            "ann_return": float(eq.iloc[-1] ** (1 / years) - 1), "max_dd": float((low / eq.cummax() - 1).min()),
            "years_positive": int((by_year > 0).sum()), "years": len(by_year),
            "pairs_positive": int(sum(v > 0 for v in per_pair_totals.values())), "pairs": len(per_pair_totals)}, by_year


def evaluate_grid(markets: dict, g: GridCfg) -> tuple[dict, pd.Series, dict]:
    res = {p: run_grid(m, g) for p, m in markets.items()}
    totals = {p: v["daily"]["equity"].iloc[-1] / v["daily"]["equity"].iloc[0] - 1 for p, v in res.items()}
    r, r_min = grid_portfolio(res)
    st, by_year = stats(r, r_min, totals)
    b = pd.concat([v["baskets"] for v in res.values()])
    st.update({"baskets_per_pair_year": len(b) / len(res) / ((r.index[-1] - r.index[0]).days / 365.25),
               "basket_win_rate": float((b["pnl"] > 0).mean()), "share_stop": float((b["reason"] == 2).mean()),
               "share_anchor": float((b["reason"] == 3).mean()), "share_time": float((b["reason"] == 4).mean())})
    return st, by_year, totals


def evaluate_momentum(markets: dict, cfg: MomCfg) -> tuple[dict, pd.Series, dict]:
    rets = {p: run_momentum(m, cfg) for p, m in markets.items()}
    totals = {p: float((1 + v).prod() - 1) for p, v in rets.items()}
    r = pd.DataFrame(rets).mean(axis=1).dropna()
    st, by_year = stats(r, None, totals)
    return st, by_year, totals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["dev", "jpy"], default="dev")
    ap.add_argument("--only", choices=["grid", "momentum"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    names = sh.DEV14 if args.market == "dev" else JPY
    markets = {p: load(p) for p in names if (DATA / f"{p.lower()}_futures_1h.csv").is_file()}
    print("pairs:", {p: f"{pd.to_datetime(m.ts[0]).date()}..{pd.to_datetime(m.ts[-1]).date()}" for p, m in markets.items()},
          flush=True)
    rows, years, criteria = [], {}, []
    families = {"grid": (GRID, evaluate_grid), "momentum": (MOM, evaluate_momentum)}
    rng = np.random.default_rng(53)
    for family, (variants, evaluate) in families.items():
        if args.only and family != args.only:
            continue
        core_st = None
        for vname, cfg in variants.items():
            st, by_year, totals = evaluate(markets, cfg)
            rows.append({"family": family, "variant": vname, **st})
            years[f"{family}: {vname}"] = by_year
            if core_st is None:
                core_st, core_cfg = st, cfg
                pd.DataFrame({"total_return": totals}).to_csv(OUT / f"pairs_{family}_{args.market}.csv")
            print(f"{args.market} {family:8s} {vname:16s} sharpe {st['sharpe']:+.2f} ann {st['ann_return']:+.4f} "
                  f"dd {st['max_dd']:+.3f} years+ {st['years_positive']}/{st['years']} "
                  f"pairs+ {st['pairs_positive']}/{st['pairs']}", flush=True)
        st = core_st
        crit = {"family": family, "market": args.market, **{f"core_{k}": v for k, v in st.items()}}
        if args.market == "dev":
            nulls = []
            for i in range(N_NULL):
                shuffled = {p: week_shuffle(m, rng) for p, m in markets.items()}
                nulls.append(evaluate(shuffled, core_cfg)[0]["sharpe"])
                print(f"  {family} null {i}: sharpe {nulls[-1]:+.2f}", flush=True)
            p95 = float(np.quantile(nulls, 0.95))
            crit.update({"null_median": float(np.median(nulls)), "null_p95": p95,
                         "1 sharpe >= 0.5": st["sharpe"] >= 0.5, "2 max DD <= 25%": st["max_dd"] >= -0.25,
                         "3 years and pairs >= 2/3": st["years_positive"] >= 2 / 3 * st["years"]
                         and st["pairs_positive"] >= 2 / 3 * st["pairs"],
                         "4 sharpe > null p95": st["sharpe"] > p95})
            crit["PASS"] = all(v for k, v in crit.items() if k[:2] in ("1 ", "2 ", "3 ", "4 "))
        else:
            crit["PASS"] = (st["sharpe"] >= 0.5 and st["years_positive"] >= 2 / 3 * st["years"]
                            and st["pairs_positive"] >= 3)
        criteria.append(crit)
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in crit.items()}, flush=True)
    pd.DataFrame(rows).to_csv(OUT / f"metrics_{args.market}.csv", index=False)
    pd.DataFrame(years).to_csv(OUT / f"by_year_{args.market}.csv")
    pd.DataFrame(criteria).to_csv(OUT / f"criteria_{args.market}.csv", index=False)
    print(pd.DataFrame(years).round(3).to_string())


if __name__ == "__main__":
    main()
