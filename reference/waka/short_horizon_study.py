"""Short-horizon mean reversion on forex crosses (step 1 of the short-hold grid design, INSIGHTS §9r).

For every full hour and both directions: does the bid move 1 step in the direction before k steps against it,
within 5 trading days and before the weekend? Step = s x previous-30-day daily sigma x price at the entry,
s in {0.25, 0.5, 1}, k in {4, 8}; unresolved counts as "not up first". Compared with the same entries on a null
that permutes bars of the same time-of-day slot across the days of each calendar month (keeps the intraday
volatility profile and monthly drift, destroys serial dependence), same steps, same cut-off; t over calendar weeks.
Split by entry session (UTC: Asia 22-07, London 07-12, New York 12-22) and by the distance from the 5-day mean in
the trade's direction (toward the mean <= -1 sigma, away >= +1 sigma, else neutral).

    uv run python reference/waka/short_horizon_study.py --pairs AUDCAD ... --nulls 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_waka as rw  # noqa: E402

wk = rw.wk
OUT = rw.OUT / "short_horizon"
WEEK = 7 * 86_400 * 10**9
HOLD = 5 * 288
STEPS = (0.25, 0.5, 1.0)
KS = (4, 8)
DEV14 = ["AUDCAD", "AUDNZD", "NZDCAD", "EURUSD", "EURGBP", "EURCHF", "AUDCHF", "NZDCHF", "CADCHF", "EURAUD",
         "EURNZD", "GBPNZD", "GBPCAD", "GBPCHF"]


def week_ends(ts: np.ndarray) -> np.ndarray:
    """Exclusive end index of each bar's trading week (a gap of more than 12 hours starts a new week)."""
    gap = np.diff(ts) > 12 * 3600 * 10**9
    starts = np.concatenate([[0], np.flatnonzero(gap) + 1])
    ends = np.concatenate([starts[1:], [len(ts)]])
    return ends[np.searchsorted(starts, np.arange(len(ts)), side="right") - 1]


def tod_shuffle(m: rw.Market, s: int, rng: np.random.Generator) -> tuple[np.ndarray, ...]:
    """Bars permuted across days within (calendar month, time-of-day slot); shapes relative to the previous close."""
    stamps = pd.to_datetime(m.ts)
    month = np.asarray(stamps.year * 12 + stamps.month)
    slot = np.asarray(stamps.hour * 12 + stamps.minute // 5)
    prev = np.concatenate([[m.bo[s, 0]], m.bc[s, :-1]])
    rel = np.stack([m.bo[s] / prev, m.bh[s] / prev, m.bl[s] / prev, m.bc[s] / prev])
    in_time = np.lexsort((np.arange(len(month)), slot, month))
    shuffled = np.lexsort((rng.random(len(month)), slot, month))
    new_rel = np.empty_like(rel)
    new_rel[:, in_time] = rel[:, shuffled]
    close = m.bo[s, 0] * np.exp(np.cumsum(np.log(new_rel[3])))
    prev_new = np.concatenate([[m.bo[s, 0]], close[:-1]])
    return prev_new * new_rel[1], prev_new * new_rel[2], close, prev_new * new_rel[0]  # h, l, c, o


def labels(h, l, c, o, starts, ends, dirs, sigma_frac, s, k) -> np.ndarray:
    step = s * sigma_frac * o[starts]
    return wk.first_passage(np.ascontiguousarray(h), np.ascontiguousarray(l), np.ascontiguousarray(c),
                            np.ascontiguousarray(o), starts, ends, dirs, step, 1.0, float(k))


def pair_frame(pair: str, n_null: int, rng: np.random.Generator) -> pd.DataFrame:
    m = rw.load_fx([pair], pair.lower())
    stamps = pd.to_datetime(m.ts)
    ok = (np.asarray(stamps.minute) == 0) & (np.arange(len(m.ts)) >= m.start) & (m.vol[0] > 0)
    idx = np.flatnonzero(ok)
    starts = np.repeat(idx, 2).astype(np.int64)
    dirs = np.tile(np.array([1, -1], np.int8), len(idx))
    ends = np.minimum(starts + HOLD, week_ends(m.ts)[starts]).astype(np.int64)
    sigma_frac = m.vol[0][starts]
    c = pd.Series(m.bc[0])
    dev = ((c / c.rolling(5 * 288).mean() - 1) / pd.Series(m.vol[0]).replace(0, np.nan)).shift(1).to_numpy()
    hour = np.asarray(stamps.hour)[starts]
    frame = pd.DataFrame({"pair": pair, "ts": m.ts[starts], "dir": dirs,
                          "session": np.where((hour >= 22) | (hour < 7), "asia", np.where(hour < 12, "london", "ny")),
                          "dev5": dev[starts] * dirs})
    h, l, cl, o = m.bh[0], m.bl[0], m.bc[0], m.bo[0]
    nulls = [tod_shuffle(m, 0, rng) for _ in range(n_null)]
    for s in STEPS:
        for k in KS:
            col = f"s{s}_k{k}"
            frame[col] = labels(h, l, cl, o, starts, ends, dirs, sigma_frac, s, k)
            frame[col + "_null"] = np.mean([labels(*nu, starts, ends, dirs, sigma_frac, s, k) == 1 for nu in nulls],
                                           axis=0)
    print(f"  {pair}: {len(frame)} entries", flush=True)
    return frame


def compare(d: pd.DataFrame, col: str) -> dict:
    real = (d[col] == 1).astype(float)
    null = d[col + "_null"]
    weeks = d["ts"] // WEEK
    diff = (real - null).groupby(weeks).mean().dropna()
    t = diff.mean() / (diff.std() / np.sqrt(len(diff))) if len(diff) > 2 else np.nan
    return {"n": len(d), "p_real": real.mean(), "p_null": null.mean(), "diff": real.mean() - null.mean(), "t_weeks": t,
            "unresolved": (d[col] < 0).mean()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", default=DEV14)
    ap.add_argument("--nulls", type=int, default=5)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(17)
    data = pd.concat([pair_frame(p, args.nulls, rng) for p in args.pairs], ignore_index=True)
    data["dev_bucket"] = pd.cut(data["dev5"], [-np.inf, -1, 1, np.inf], labels=["toward mean", "neutral", "away"])
    rows = []
    for s in STEPS:
        for k in KS:
            col = f"s{s}_k{k}"
            rows.append({"step": s, "k": k, "group": "all", **compare(data, col)})
            for key in ("session", "dev_bucket"):
                for value, part in data.groupby(key, observed=True):
                    rows.append({"step": s, "k": k, "group": f"{key}={value}", **compare(part, col)})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "first_passage_by_scale.csv", index=False)
    by_pair = pd.DataFrame({p: compare(g, "s0.5_k4") for p, g in data.groupby("pair")}).T
    by_pair.to_csv(OUT / "by_pair_s0.5_k4.csv")
    pd.set_option("display.width", 200)
    print(table.round(4).to_string(index=False))
    print(by_pair.round(4).to_string())


if __name__ == "__main__":
    main()
