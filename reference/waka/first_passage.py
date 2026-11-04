"""Fixed-size first-passage bets on FX crosses: monetise the grid-scale mean reversion found in §9n (INSIGHTS §9o).

Pre-registered on 2026-10-21, before any result of this script was looked at:
- Bet: every pair keeps at most one long and one short position; a closed position is reopened at the next full
  hour (no new positions 20:30-23:00 UTC). One step = the smart distance (~1 daily sigma at entry, the §9n unit).
- Primary S1: fixed size, TP 1 step, SL 8 steps, risk at the SL = 1% of equity. All pairs share one account.
  Variants (reported, never picked): TP 1 / SL 4, TP 2 / SL 8, equal-lot grid of 9 trades with money TP 1 step and
  a stop 9 steps from the first entry (risk at the stop = 1%).
- Costs: FXCM bid/ask spread, 35 USD per million per side, swap -1%/year on both sides (FRED unavailable).
- Markets: dev = the 8 crosses already used (AUDCAD/AUDNZD/NZDCAD and EURGBP/EURCHF/AUDCHF/NZDCHF/CADCHF), to check
  the code; fresh = EURAUD, EURNZD, EURCAD, GBPAUD, GBPNZD, GBPCAD, GBPCHF, never looked at, run once; no change
  between the dev and fresh runs other than bug fixes.
- PASS (fresh, S1): daily Sharpe >= 0.5, max equity DD <= 30%, positive in >= 2/3 of calendar years, Sharpe > p95 of
  20 week-shuffled price runs, and the first-passage excess (P(+1 before -8) minus the random-walk baseline) > 0
  with calendar-week t >= 2 on the fresh pairs.

    uv run python reference/waka/first_passage.py --markets dev_fx dev_holdout     # check
    uv run python reference/waka/first_passage.py --markets fresh                  # once
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import regime_study as rs  # noqa: E402

rw, wk = rs.rw, rs.wk
OUT = rw.OUT / "first_passage"
RISK = 0.01
MARKETS = {"dev_fx": rs.FX_PAIRS, "dev_holdout": rs.HOLDOUT_PAIRS, "fresh": rs.FRESH_PAIRS}
VARIANTS = {"S1 TP 1 / SL 8": (1.0, 8.0, 1), "TP 1 / SL 4": (1.0, 4.0, 1), "TP 2 / SL 8": (2.0, 8.0, 1),
            "equal-lot grid 9, TP 1, SL 9": (1.0, 9.0, 9)}
PRIMARY = "S1 TP 1 / SL 8"
N_NULL = 20


def config(tp: float, sl: float, trades: int) -> rw.Waka:
    return replace(rw.Waka(smart=True), distance_pips=35.0, tp_pips=35.0 * tp, sl_pips=35.0 * sl, max_trades=trades,
                   m2=1.0, m35=1.0, m6=1.0)


def notional(m: rw.Market, w: rw.Waka, sl: float, trades: int) -> np.ndarray:
    """First-trade notional per unit of balance so that the loss at the stop is RISK."""
    dist = rw.kernel_inputs(m, w)["dist"]
    dfrac = np.where(dist > 0, dist / m.bo, np.nan)
    loss_steps = sum(sl - k for k in range(trades))  # equal lots at 0, 1, ..., trades-1 steps; stop at sl steps
    return np.nan_to_num(RISK / (loss_steps * dfrac), nan=0.0)


def hourly_signals(m: rw.Market) -> np.ndarray:
    stamps = pd.to_datetime(m.ts)
    minute = np.asarray(stamps.hour * 60 + stamps.minute)
    ok = (np.asarray(stamps.minute) == 0) & (np.arange(len(m.ts)) >= m.start)
    ok &= ~((minute >= 20 * 60 + 30) & (minute < 23 * 60))
    sig = np.zeros(m.bo.shape, np.int8)
    sig[:, ok] = 2
    return sig


def run_variant(m: rw.Market, tp: float, sl: float, trades: int) -> dict:
    w = config(tp, sl, trades)
    return rw.run(m, w, hourly_signals(m), notional(m, w, sl, trades))


def stats(res: dict) -> dict:
    daily = res["daily"]
    r = daily["equity"].pct_change().dropna()
    eq = daily["equity"]
    by_year = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    b = res["baskets"]
    years = (daily.index[-1] - daily.index[0]).days / 365.25
    return {"sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0,
            "ann_return": float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1),
            "max_dd": float((daily["min_equity"] / eq.cummax() - 1).min()),
            "years_positive": int((by_year > 0).sum()), "years": len(by_year),
            "positions_per_year": len(b) / years, "win_rate": float((b["pnl"] > 0).mean()),
            "share_tp": float((b["reason"] == "take profit").mean()),
            "median_hold_days": float(((b["closed"] - b["opened"]).dt.total_seconds() / 86400).median()),
            "fees": res["counters"]["fees"], "carry": res["counters"]["carry"]}, by_year


def first_passage_excess(market: str, pairs: list[str]) -> dict:
    """§9n label on this market's pairs (built with regime_study if missing)."""
    name = "fresh" if market == "fresh" else {"dev_fx": "fx", "dev_holdout": "holdout"}[market]
    path = rs.OUT / f"baskets_{name}.parquet"
    if not path.is_file():
        rs.OUT.mkdir(parents=True, exist_ok=True)
        rs.build(name)
    d = pd.read_parquet(path, columns=["ts", "fp", "fp_base", "symbol"])
    x = (d["fp"] - d["fp_base"]).where(d["fp"] >= 0)
    w = x.groupby(d["ts"].astype("int64") // rs.WEEK).mean().dropna()
    return {"fp_excess": float(x.mean()), "fp_excess_t_weeks": float(w.mean() / (w.std() / np.sqrt(len(w)))),
            **{f"fp_excess_{s}": float(v) for s, v in x.groupby(d["symbol"]).mean().items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=["dev_fx", "dev_holdout"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for market in args.markets:
        pairs = MARKETS[market]
        m = rw.load_fx(pairs, market)
        rows, years, curves = [], {}, {}
        for name, (tp, sl, trades) in VARIANTS.items():
            res = run_variant(m, tp, sl, trades)
            row, by_year = stats(res)
            rows.append({"variant": name, **row})
            years[name] = by_year
            curves[name] = res["daily"]["equity"]
            print(f"{market} {name:30s} sharpe {row['sharpe']:+.2f} ann {row['ann_return']:+.3f} dd {row['max_dd']:+.3f}"
                  f" win {row['win_rate']:.3f} pos/yr {row['positions_per_year']:.0f}", flush=True)
        rng = np.random.default_rng(31)
        nulls = []
        tp, sl, trades = VARIANTS[PRIMARY]
        for i in range(N_NULL):
            nulls.append(stats(run_variant(rw.shuffled_fx(m, rng), tp, sl, trades))[0]["sharpe"])
            print(f"  shuffled {i}: sharpe {nulls[-1]:+.2f}", flush=True)
        fp = first_passage_excess(market, pairs)
        primary = rows[0]
        crit = {
            "market": market, **{f"S1_{k}": v for k, v in primary.items() if k != "variant"},
            "null_p95": float(np.quantile(nulls, 0.95)), "null_median": float(np.median(nulls)), **fp,
            "1 sharpe >= 0.5": primary["sharpe"] >= 0.5,
            "2 max DD <= 30%": primary["max_dd"] >= -0.30,
            "3 >= 2/3 years positive": primary["years_positive"] >= 2 / 3 * primary["years"],
            "4 sharpe > shuffled p95": primary["sharpe"] > np.quantile(nulls, 0.95),
            "5 first-passage excess t >= 2": fp["fp_excess"] > 0 and fp["fp_excess_t_weeks"] >= 2,
        }
        crit["PASS"] = all(v for k, v in crit.items() if k[:2] in ("1 ", "2 ", "3 ", "4 ", "5 "))
        pd.DataFrame(rows).to_csv(OUT / f"metrics_{market}.csv", index=False)
        pd.DataFrame(years).to_csv(OUT / f"by_year_{market}.csv")
        pd.DataFrame(curves).to_csv(OUT / f"daily_equity_{market}.csv")
        pd.DataFrame({"shuffled_sharpe": nulls}).to_csv(OUT / f"null_{market}.csv", index=False)
        pd.DataFrame([crit]).to_csv(OUT / f"criteria_{market}.csv", index=False)
        print(pd.DataFrame(years).round(3).to_string())
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in crit.items()}, flush=True)


if __name__ == "__main__":
    main()
