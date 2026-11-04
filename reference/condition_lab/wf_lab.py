"""Walk-forward 24/6 of the conditions that passed PREREGISTRATION.md, against the same grid without them.

Variants per baseline: none, cond1, cond2, cond1 & cond2 (the two dev-best passers of different families).
Grids: A = current_strategy.ipynb's WF_GRID (15min); B = a 1,440-set slice of the notebook grid (5min).
Per test window (6 months, 2023-01 -> 2026-07) and variant: the train window's 24 months rank every set
(A: R / monthly max drawdown, >= 10 trades a year; B: monthly Sharpe, >= 100 trades), then the test R of
  - per-coin top 10 (each coin its own picks), and
  - common top 10 (one ranking of the coins pooled, as current_strategy's "chung").
The monthly R of every (coin, set, variant) is cached in results/condition_lab/wf_<X>.npz.

    uv run python reference/condition_lab/wf_lab.py run --baseline A
    uv run python reference/condition_lab/wf_lab.py analyze --baseline A
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

import numba as nb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cond_lab as cl  # noqa: E402
from cond_lab import engine, vfe  # noqa: E402

VARIANTS = {"A": ["none", "fund7_pos", "rsi14_lt30", "fund7_pos&rsi14_lt30"],
            "B": ["none", "rsi14_lt30", "btc_vol_low", "rsi14_lt30&btc_vol_low"]}
GRIDS = {
    "A": {"boll_window": [80, 100, 150, 200, 300], "boll_window_dev": [1.8, 2.0, 2.2],
          "sl_multiplier": [0.5, 0.6, 0.8, 1.0], "vol_window": [96, 288], "vol_days": [30, 90],
          "vol_ratio_max": [0.8, 0.9, 1.0], "trail": [1.0, 1.5, 2.0], "tp": [0.0], "hold": [384]},
    "B": {"boll_window": [100, 150, 200, 250, 300, 400], "boll_window_dev": [1.8, 2.0, 2.2, 2.5],
          "sl_multiplier": [0.5, 0.7, 0.9, 1.1, 1.3], "band_ratio_max": [0.5, 0.75, 1.0, 1.5],
          "hold": [192, 288, 384], "tp": [1.1], "trail": [0.0]},
}
TRAIN, TEST, FIRST_TEST = 24, 6, "2023-01"
TOP_K = 10


@nb.njit(parallel=True, cache=True)
def _kernel(close, high, low, upper, lower, base, cond, period, n_periods, start, fee_bars, entry_fee_rate,
            holding_fee_rate, max_fee_r, b_idx, f_idx, k_idx, mults, tps, holds, trails):
    C = b_idx.size
    n = close.size
    R = np.zeros((C, n_periods), dtype=np.float32)
    N = np.zeros((C, n_periods), dtype=np.int16)
    for c in nb.prange(C):
        b, f, k, m, tp, hd, tr = b_idx[c], f_idx[c], k_idx[c], mults[c], tps[c], holds[c], trails[c]
        in_pos = False
        ei = 0
        entry = risk = stop = stop0 = best = target = 0.0
        for t in range(start, n):
            if not in_pos:
                if t < n - 1 and base[f, t] and cond[k, t]:
                    if close[t - 1] > lower[b, t] and close[t] < lower[b, t]:
                        rk = (upper[b, t] - close[t]) * m
                        if close[t] > 0 and rk > 0 and entry_fee_rate / (rk / close[t]) <= max_fee_r:
                            in_pos = True
                            ei, entry, risk = t, close[t], rk
                            stop = entry + rk
                            stop0 = stop
                            best = entry
                            target = entry - tp * rk
                continue
            holding = t - ei
            if holding >= hd:
                gross = -(close[t] - entry) / risk
            elif high[t] > stop:
                gross = -1.0 if stop == stop0 else -(stop - entry) / risk
            elif tp > 0 and low[t] < target:
                gross = tp
            elif t == n - 1:
                gross = -(close[t] - entry) / risk
            else:
                if tr > 0:
                    best = min(best, low[t])
                    stop = min(stop, best + tr * risk)
                continue
            slr = risk / entry
            net = gross - entry_fee_rate / slr - holding_fee_rate * (holding / fee_bars) / slr
            p = period[t]
            if p >= 0:
                R[c, p] += net
                N[c, p] += 1
            in_pos = False
    return R, N


def grid(baseline: str) -> pd.DataFrame:
    space = GRIDS[baseline]
    return pd.DataFrame(itertools.product(*space.values()), columns=list(space))


def run_coin(baseline: str, bars: pd.DataFrame, masks: dict[str, np.ndarray], g: pd.DataFrame, n_months: int):
    cfg = cl.BASELINES[baseline]
    tf = cfg["timeframe"]
    close = bars["close"].to_numpy(np.float64)
    band_keys = list(dict.fromkeys(zip(g.boll_window.astype(int), g.boll_window_dev.astype(float))))
    upper = np.empty((len(band_keys), len(close)))
    lower = np.empty_like(upper)
    for i, (w, d) in enumerate(band_keys):
        upper[i], lower[i] = engine._cpu_bands(close, w, d)
    rsi_ok = engine._cpu_rsi(close, cfg["rsi_window"]) < 100
    if baseline == "A":
        f_keys = list(dict.fromkeys(zip(g.vol_window.astype(int), g.vol_days.astype(int), g.vol_ratio_max)))
        vr = {(w, d): vfe.vol_ratio(close, w, d, tf) for w, d in dict.fromkeys((w, d) for w, d, _ in f_keys)}
        base = np.stack([rsi_ok & (vr[(w, d)] < x) for w, d, x in f_keys])
        f_of = [(int(r.vol_window), int(r.vol_days), r.vol_ratio_max) for r in g.itertuples()]
    else:
        f_keys = list(dict.fromkeys(zip(g.boll_window.astype(int), g.boll_window_dev.astype(float), g.band_ratio_max)))
        ratio = {(w, d): engine.band_width_ratio(bars, w, float(np.float32(d))).astype(np.float32)
                 for w, d in dict.fromkeys((w, d) for w, d, _ in f_keys)}
        base = np.stack([rsi_ok & (ratio[(w, d)] <= np.float32(x)) for w, d, x in f_keys])
        f_of = [(int(r.boll_window), float(r.boll_window_dev), r.band_ratio_max) for r in g.itertuples()]
    variants = VARIANTS[baseline]
    cond = np.stack([np.logical_and.reduce([masks[p] for p in v.split("&")]) for v in variants])
    bmap = {k: i for i, k in enumerate(band_keys)}
    fmap = {k: i for i, k in enumerate(f_keys)}
    b1 = np.array([bmap[(int(r.boll_window), float(r.boll_window_dev))] for r in g.itertuples()])
    f1 = np.array([fmap[k] for k in f_of])
    V = len(variants)
    rep = lambda a: np.repeat(a, V)  # noqa: E731 - combos ordered (set, variant)
    R, N = _kernel(close, bars["high"].to_numpy(np.float64), bars["low"].to_numpy(np.float64), upper, lower, base,
                   cond, vfe.month_index(bars, cl.FIRST_MONTH).astype(np.int64), n_months,
                   max(cfg["rsi_window"], 1), engine.resolve_fee_bars(tf), engine.ENTRY_FEE_RATE,
                   engine.HOLDING_FEE_RATE, engine.MAX_FEE_R, rep(b1).astype(np.int64), rep(f1).astype(np.int64),
                   np.tile(np.arange(V), len(g)).astype(np.int64), rep(g.sl_multiplier.to_numpy(np.float64)),
                   rep(g.tp.to_numpy(np.float64)), rep(g.hold.to_numpy(np.int64)), rep(g.trail.to_numpy(np.float64)))
    return R.reshape(len(g), V, n_months), N.reshape(len(g), V, n_months)


def run(baseline: str) -> None:
    cfg = cl.BASELINES[baseline]
    files = cl.coin_files()
    coins = [c for c in files if c != "btcusdt"]
    breadth = cl.daily_breadth(files)
    btc = cl.load_coin(files["btcusdt"], cfg["timeframe"])
    g = grid(baseline)
    n_months = (pd.Period("2026-10", "M") - pd.Period(cl.FIRST_MONTH, "M")).n + 1
    Rs, Ns = [], []
    t0 = time.perf_counter()
    for coin in coins:
        bars = cl.load_coin(files[coin], cfg["timeframe"])
        masks = cl.conditions(bars, btc, cfg["timeframe"], cl.load_funding(coin), breadth)
        R, N = run_coin(baseline, bars, masks, g, n_months)
        Rs.append(R)
        Ns.append(N)
        print(f"{coin}: {time.perf_counter() - t0:.0f}s", flush=True)
    np.savez_compressed(cl.OUT / f"wf_{baseline}.npz", R=np.stack(Rs), N=np.stack(Ns), coins=np.array(coins),
                        variants=np.array(VARIANTS[baseline]), first_month=cl.FIRST_MONTH)
    g.to_csv(cl.OUT / f"wf_{baseline}_grid.csv", index=False)


def scores(baseline: str, R: np.ndarray, N: np.ndarray) -> np.ndarray:
    """R, N: ... x months (train window). Higher is better; -inf when not eligible."""
    months = R.shape[-1]
    if baseline == "A":
        eq = np.concatenate([np.zeros(R.shape[:-1] + (1,)), np.cumsum(R, axis=-1)], axis=-1)
        dd = (np.maximum.accumulate(eq, axis=-1) - eq).max(axis=-1)
        s = R.sum(axis=-1) / np.maximum(dd, 1.0)
        ok = N.sum(axis=-1) >= 10 * months / 12
    else:
        sd = R.std(axis=-1)
        s = R.mean(axis=-1) / np.where(sd > 0, sd, np.nan)
        ok = N.sum(axis=-1) >= 100
    return np.where(ok & np.isfinite(s), s, -np.inf)


def analyze(baseline: str) -> None:
    data = np.load(cl.OUT / f"wf_{baseline}.npz")
    R, N = data["R"].astype(np.float64), data["N"].astype(np.int64)  # coins x sets x variants x months
    coins, variants = list(data["coins"]), list(data["variants"])
    import analyze as an  # noqa: PLC0415
    active = an.active_months(coins, cl.FIRST_MONTH, R.shape[-1])
    base = pd.Period(cl.FIRST_MONTH, "M")
    first = (pd.Period(FIRST_TEST, "M") - base).n
    rows = []
    for c0 in range(first, R.shape[-1], TEST):
        a, e = c0 - TRAIN, min(c0 + TEST, R.shape[-1])
        label = str(base + c0)
        members = [i for i in range(len(coins)) if active[i, a] and active[i, c0]]
        for v, name in enumerate(variants):
            tr_R, tr_N, te_R = R[members, :, v, a:c0], N[members, :, v, a:c0], R[members, :, v, c0:e]
            sc = scores(baseline, tr_R, tr_N)  # coins x sets
            per_coin = []
            for j in range(len(members)):
                order = np.argsort(-sc[j])[:TOP_K]
                order = order[np.isfinite(sc[j][order])]
                if len(order):
                    per_coin.append(te_R[j, order].sum(axis=-1).mean())
            pooled = scores(baseline, tr_R.sum(axis=0) / len(members), tr_N.sum(axis=0) // len(members))
            common = np.argsort(-pooled)[:TOP_K]
            rows.append({"window": label, "variant": name, "coins": len(members),
                         "per_coin_top10": np.mean(per_coin), "coins_pos": np.mean(np.array(per_coin) > 0),
                         "common_top10": te_R[:, common].sum(axis=-1).mean(),
                         "grid_mean": te_R.sum(axis=-1).mean(),
                         "trades_per_coin": N[members][:, :, v, c0:e].sum(axis=-1).mean()})
    t = pd.DataFrame(rows)
    t.to_csv(cl.OUT / f"wf_{baseline}_folds.csv", index=False)
    pd.set_option("display.width", 220)
    for metric in ("per_coin_top10", "common_top10", "grid_mean", "coins_pos", "trades_per_coin"):
        p = t.pivot(index="window", columns="variant", values=metric)[variants]
        print(f"\n=== baseline {baseline}: {metric} (test R per coin per 6-month window)")
        print(p.round(2).to_string())
        print("mean ", p.mean().round(3).to_dict())
        if metric in ("per_coin_top10", "common_top10"):
            print("windows better than none:", {v: f"{int((p[v] > p['none']).sum())}/{len(p)}" for v in variants[1:]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "analyze"])
    parser.add_argument("--baseline", choices=list(GRIDS), default="A")
    args = parser.parse_args()
    run(args.baseline) if args.command == "run" else analyze(args.baseline)


if __name__ == "__main__":
    main()
