"""Walk-forward parameter optimisation of the Waka Waka reproduction (plan of 2026-10-21, section 4b; INSIGHTS §9m).

Every parameter set runs once over the whole history; each window's train and test statistics are cut from that run's
daily equity, so selection only ever sees train days. Score in train = annualised return / |max equity drawdown|
(intraday low equity), sets with < 30 baskets or a stop-out / ruin in train excluded. Schedules: 3y/1y (calendar
years) and 18m/6m (half years). Forex markets keep per-set daily returns in memory (no grid-wide files: the disk is
nearly full); crypto sets are run coin by coin and averaged into equal-sleeve portfolios.

    uv run python reference/waka/optimize_waka.py --markets crosses eurusd crypto --workers 12
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import time

for _var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):  # one BLAS thread per worker process
    os.environ.setdefault(_var, "1")
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_waka as rw  # noqa: E402

OUT = rw.OUT / "opt"
MIN_TRAIN_BASKETS = 30
MULTIPLIERS = {"1/2/1.6": (1.0, 2.0, 1.6), "1/1/1": (1.0, 1.0, 1.0), "1/1.5/1.3": (1.0, 1.5, 1.3),
               "2/2/2": (2.0, 2.0, 2.0)}
GRID_DIMS = dict(distance_pips=[20.0, 35.0, 50.0, 80.0], tp_pips=[5.0, 10.0, 20.0], max_trades=[5, 7, 9],
                 multipliers=list(MULTIPLIERS), max_dd=[1.0, 0.30])
FX_SIGNALS = [dict(bb_window=b, bb_dev=d, rsi_window=r, rsi_high=h, rsi_low=lo)
              for b, d, r, (h, lo) in itertools.product([20, 40, 80], [1.5, 2.0, 2.5], [7, 14],
                                                        [(65.0, 35.0), (70.0, 30.0), (80.0, 20.0)])]
CRYPTO_SIGNALS = [dict(bb_window=20, bb_dev=2.0, rsi_window=14, rsi_high=70.0, rsi_low=30.0),
                  dict(bb_window=40, bb_dev=2.5, rsi_window=14, rsi_high=80.0, rsi_low=20.0)]


def parameter_sets(core: rw.Waka, signal_dims: list[dict]) -> list[rw.Waka]:
    sets = []
    for sig in signal_dims:
        for dist, tp, mt, mult, dd in itertools.product(*GRID_DIMS.values()):
            m2, m35, m6 = MULTIPLIERS[mult]
            sets.append(replace(core, **sig, distance_pips=dist, tp_pips=tp, max_trades=mt, m2=m2, m35=m35, m6=m6,
                                max_dd=dd))
    return sets


def describe(w: rw.Waka) -> dict:
    row = {k: v for k, v in asdict(w).items() if k in ("bb_window", "bb_dev", "rsi_window", "rsi_high", "rsi_low",
                                                         "distance_pips", "tp_pips", "max_trades", "max_dd")}
    row["multipliers"] = next(k for k, v in MULTIPLIERS.items() if v == (w.m2, w.m35, w.m6))
    return row


def windows(first_day: pd.Timestamp, last_day: pd.Timestamp, schedule: str) -> list[tuple]:
    """(label, train_start, test_start, test_end) with the train fully inside the data (45 days of slack)."""
    train_m, test_m = {"3y1y": (36, 12), "18m6m": (18, 6)}[schedule]
    out = []
    test_start = pd.Timestamp(first_day.year, 1, 1)
    while test_start <= last_day:
        train_start = test_start - pd.DateOffset(months=train_m)
        test_end = test_start + pd.DateOffset(months=test_m)
        if train_start >= first_day - pd.Timedelta(days=45):
            label = str(test_start.year) if test_m == 12 else f"{test_start.year}-H{1 + (test_start.month > 6)}"
            out.append((label, train_start, test_start, min(test_end, last_day + pd.Timedelta(days=1))))
        test_start = test_end if test_start.month in (1, 7) else test_start + pd.DateOffset(months=test_m)
    return out


# --------------------------------------------------------------------------------------- per-set series

def daily_arrays(res: dict, calendar: pd.DatetimeIndex) -> np.ndarray:
    """[3, days]: daily return, intraday-low return vs the previous close, baskets opened that day."""
    daily = res["daily"].reindex(calendar)
    eq = daily["equity"]
    prev = eq.shift(1)
    out = np.full((3, len(calendar)), np.nan, np.float32)
    out[0] = (eq / prev - 1).to_numpy()
    out[1] = (daily["min_equity"] / prev - 1).to_numpy()
    opened = res["baskets"]["opened"].dt.normalize().value_counts() if len(res["baskets"]) else pd.Series(dtype=int)
    stop = res["baskets"].loc[res["baskets"]["reason"] == "stop-out", "closed"].dt.normalize() \
        if len(res["baskets"]) else pd.Series(dtype="datetime64[ns]")
    count = opened.reindex(calendar).fillna(0).to_numpy()
    count[calendar.isin(stop)] = -1e6  # marks a stop-out day; any window containing it is excluded
    out[2] = count
    return out


_STATE: dict = {}


def _init(market: str) -> None:
    if market == "crosses":
        _STATE["m"] = rw.load_fx(rw.CROSSES, market)
    elif market == "eurusd":
        _STATE["m"] = rw.load_fx(["EURUSD"], market)


def _fx_task(args: tuple) -> tuple[list[int], np.ndarray]:
    ids, sets, calendar = args
    m = _STATE["m"]
    return ids, np.stack([daily_arrays(rw.run(m, w), calendar) for w in sets])


def _crypto_task(args: tuple) -> tuple[str, np.ndarray]:
    symbol, sets, calendar = args
    m = rw.crypto_market(rw.rg.load_coin(symbol))
    return symbol, np.stack([daily_arrays(rw.run(m, w), calendar) for w in sets])


def run_grid(market: str, sets: list[rw.Waka], calendar: pd.DatetimeIndex, workers: int) -> np.ndarray:
    """[n_sets, 3, days] daily returns, intraday-low returns and basket counts (portfolio for crypto)."""
    t0 = time.time()
    if market != "crypto":
        out = np.full((len(sets), 3, len(calendar)), np.nan, np.float32)
        chunks = [list(range(i, min(i + 96, len(sets)))) for i in range(0, len(sets), 96)]  # same signal per chunk
        with ProcessPoolExecutor(workers, initializer=_init, initargs=(market,)) as pool:
            for n, (ids, arr) in enumerate(pool.map(_fx_task, [(c, [sets[i] for i in c], calendar) for c in chunks])):
                out[ids] = arr
                if n % 10 == 0:
                    print(f"  {market}: {ids[-1] + 1}/{len(sets)} sets, {time.time() - t0:.0f}s", flush=True)
        return out
    total = np.zeros((len(sets), 3, len(calendar)))
    active = np.zeros((len(sets), len(calendar)))
    symbols = rw.crypto_symbols()
    with ProcessPoolExecutor(workers) as pool:
        for n, (sym, arr) in enumerate(pool.map(_crypto_task, [(s, sets, calendar) for s in symbols])):
            ok = ~np.isnan(arr[:, 0])
            total[:, :2] += np.nan_to_num(arr[:, :2])
            total[:, 2] += np.nan_to_num(arr[:, 2])
            active += ok
            print(f"  crypto: {n + 1}/{len(symbols)} coins ({sym}), {time.time() - t0:.0f}s", flush=True)
    port = np.full((len(sets), 3, len(calendar)), np.nan, np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        port[:, 0] = np.where(active > 0, total[:, 0] / active, np.nan)
        port[:, 1] = np.where(active > 0, total[:, 1] / active, np.nan)
    port[:, 2] = total[:, 2]
    return port


# -------------------------------------------------------------------------------------------- statistics

def window_stats(arr: np.ndarray, mask: np.ndarray) -> dict[str, np.ndarray]:
    """Per set over the days in mask: total return, annualised return, max drawdown on intraday lows, baskets."""
    r = np.nan_to_num(arr[:, 0, mask].astype(float))
    rmin = np.minimum(np.nan_to_num(arr[:, 1, mask].astype(float)), r)
    eq = np.cumprod(1 + r, axis=1)
    prev = np.concatenate([np.ones((len(eq), 1)), eq[:, :-1]], axis=1)
    low = prev * (1 + rmin)
    peak = np.maximum.accumulate(np.concatenate([np.ones((len(eq), 1)), eq], axis=1), axis=1)[:, :-1]
    dd = np.minimum((low / peak - 1).min(axis=1), 0.0)
    total = eq[:, -1] - 1
    years = mask.sum() / 365.25
    ann = np.sign(1 + total) * np.abs(1 + total) ** (1 / years) - 1
    counts = arr[:, 2, mask]
    return {"ret": total, "ann": ann, "dd": dd, "baskets": np.where((counts < 0).any(axis=1), -1, counts.sum(axis=1))}


def score(st: dict[str, np.ndarray]) -> np.ndarray:
    s = st["ann"] / np.maximum(-st["dd"], 0.01)
    return np.where((st["baskets"] >= MIN_TRAIN_BASKETS) & (st["ret"] > -1), s, -np.inf)


def stitched(arr: np.ndarray, picks: list[np.ndarray], masks: list[np.ndarray]) -> pd.DataFrame:
    """Equal-weight daily returns of the picked sets inside each test window, concatenated."""
    rows = []
    for ids, mask in zip(picks, masks):
        r = np.nanmean(arr[ids, 0][:, mask].astype(float), axis=0)
        rmin = np.nanmean(np.minimum(arr[ids, 1][:, mask], arr[ids, 0][:, mask]).astype(float), axis=0)
        rows.append(pd.DataFrame({"r": r, "rmin": rmin}, index=np.flatnonzero(mask)))
    return pd.concat(rows)


def curve_stats(frame: pd.DataFrame) -> dict:
    r = frame["r"].fillna(0).to_numpy()
    eq = np.cumprod(1 + r)
    prev = np.concatenate([[1.0], eq[:-1]])
    low = prev * (1 + np.minimum(frame["rmin"].fillna(0).to_numpy(), r))
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[:-1]
    dd = min((low / peak - 1).min(), 0.0)
    years = len(r) / 365.25
    ann = eq[-1] ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0.0
    return {"total": eq[-1] - 1, "ann": ann, "max_dd": dd, "ann_over_dd": ann / max(-dd, 0.01), "sharpe": sharpe}


def walk_forward(market: str, schedule: str, arr: np.ndarray, sets: list[rw.Waka], calendar: pd.DatetimeIndex,
                 core_id: int) -> dict:
    valid = ~np.all(np.isnan(arr[:, 0]), axis=0)
    first, last = calendar[valid][0], calendar[valid][-1]
    rows, per_set, picks1, picks10, masks = [], [], [], [], []
    for label, a, b, c in windows(first, last, schedule):
        train = (calendar >= a) & (calendar < b) & valid
        test = (calendar >= b) & (calendar < c) & valid
        tr, te = window_stats(arr, train), window_stats(arr, test)
        sc = score(tr)
        order = np.argsort(-sc)
        ok = np.isfinite(sc)
        top10 = order[:10]
        top25 = order[: max(1, int(ok.sum() * 0.25))]
        rank_ok = ok & np.isfinite(te["ret"])
        rho = pd.Series(sc[rank_ok]).rank().corr(pd.Series(te["ret"][rank_ok]).rank()) if rank_ok.sum() > 2 else np.nan
        rows.append({"window": label, "train_start": a.date(), "test_start": b.date(), "test_end": c.date(),
                     "eligible_sets": int(ok.sum()), "best1_test_ret": te["ret"][order[0]],
                     "best1_test_dd": te["dd"][order[0]], "top10_test_ret": te["ret"][top10].mean(),
                     "top10_test_dd": te["dd"][top10].mean(), "top25_test_ret_median": np.median(te["ret"][top25]),
                     "grid_test_ret_median": np.nanmedian(te["ret"]), "grid_share_profitable": np.mean(te["ret"] > 0),
                     "core_test_ret": te["ret"][core_id], "core_test_dd": te["dd"][core_id],
                     "spearman_train_test": rho, **{f"best1_{k}": v for k, v in describe(sets[order[0]]).items()}})
        per_set.append(pd.DataFrame({"window": label, "set": np.arange(len(sets)), "train_score": sc,
                                     "train_ret": tr["ret"], "train_dd": tr["dd"], "train_baskets": tr["baskets"],
                                     "test_ret": te["ret"], "test_dd": te["dd"]}))
        picks1.append(order[:1])
        picks10.append(top10)
        masks.append(test)
    wf = pd.DataFrame(rows)
    oos = {"best1": stitched(arr, picks1, masks), "top10": stitched(arr, picks10, masks),
           "core": stitched(arr, [np.array([core_id])] * len(masks), masks)}
    stats = {k: curve_stats(v) for k, v in oos.items()}
    curves = pd.DataFrame({k: (1 + v["r"].fillna(0)).cumprod().to_numpy() for k, v in oos.items()},
                          index=calendar[oos["core"].index])
    crit = {
        "market": market, "schedule": schedule, "windows": len(wf),
        "1 top10 test > 0 in >= 2/3 windows": (wf["top10_test_ret"] > 0).mean() >= 2 / 3,
        "2 top10 OOS sharpe >= 0.5 and DD <= 30%": stats["top10"]["sharpe"] >= 0.5 and stats["top10"]["max_dd"] >= -0.30,
        "3 top10 OOS ann/DD > core": stats["top10"]["ann_over_dd"] > stats["core"]["ann_over_dd"],
        **{f"{k}_{m}": v for k, s in stats.items() for m, v in s.items()},
    }
    return {"wf": wf, "per_set": pd.concat(per_set), "curves": curves, "criteria": crit}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=["crosses", "eurusd", "crypto"])
    ap.add_argument("--schedules", nargs="+", default=["3y1y", "18m6m"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="first N sets only (smoke test)")
    ap.add_argument("--reverse", action="store_true", help="trend-following entries (results/waka_reverse/opt/)")
    args = ap.parse_args()
    global OUT
    if args.reverse:
        rw.use_reverse()
        OUT = rw.OUT / "opt"
    OUT.mkdir(parents=True, exist_ok=True)
    criteria = []
    for market in args.markets:
        core = rw.CORES[market]
        sets = parameter_sets(core, CRYPTO_SIGNALS if market == "crypto" else FX_SIGNALS)
        core_id = sets.index(core)  # the documented defaults are one of the grid's sets
        if args.limit:
            sets = sets[: max(args.limit, core_id + 1)]
        if market == "crypto":
            first = min(pd.Timestamp(rw.rg.load_coin(s).days[0]) for s in rw.crypto_symbols()[:1])
            calendar = pd.date_range("2021-01-01", pd.Timestamp.now().normalize(), freq="D")
        else:
            m = rw.load_fx(rw.CROSSES if market == "crosses" else ["EURUSD"], market)
            calendar = pd.date_range(m.days[0], m.days[-1], freq="D")
        print(f"{market}: {len(sets)} sets, core = set {core_id}, {len(calendar)} days", flush=True)
        arr = run_grid(market, sets, calendar, args.workers)
        pd.DataFrame([describe(w) for w in sets]).to_csv(OUT / f"sets_{market}.csv", index_label="set")
        for schedule in args.schedules:
            res = walk_forward(market, schedule, arr, sets, calendar, core_id)
            tag = f"{market}_{schedule}"
            res["wf"].to_csv(OUT / f"wf_{tag}.csv", index=False)
            res["per_set"].to_csv(OUT / f"windows_{tag}.csv.gz", index=False, float_format="%.5g")
            res["curves"].to_csv(OUT / f"oos_{tag}.csv")
            criteria.append(res["criteria"])
            print(res["wf"][["window", "eligible_sets", "best1_test_ret", "top10_test_ret", "top25_test_ret_median",
                             "grid_test_ret_median", "core_test_ret", "spearman_train_test"]].round(3).to_string())
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in res["criteria"].items()}, flush=True)
        del arr
    path = OUT / "criteria_opt.csv"
    new = pd.DataFrame(criteria)
    if path.is_file():  # keep rows of markets not run this time
        old = pd.read_csv(path)
        old = old[~old.set_index(["market", "schedule"]).index.isin(new.set_index(["market", "schedule"]).index)]
        new = pd.concat([old, new], ignore_index=True)
    new.to_csv(path, index=False)


if __name__ == "__main__":
    main()
