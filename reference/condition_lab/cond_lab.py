"""Condition lab: extra entry conditions on top of two short Bollinger-breakout baselines (PREREGISTRATION.md).

Every condition is a per-bar boolean "allowed" mask, ANDed with the baseline's own filter (A: low realized vol,
B: narrow band) and the breakout. One numba kernel runs every (parameter set x condition) pair of a coin and
returns the net R and trade count per calendar month. With the "none" condition the trades are exactly
vol_filter_engine.run_grid's (A) and multi_position_engine.cpu_trace's with max_open 1 (B); `verify` checks it.

Run from the repository root:

    uv run python reference/condition_lab/cond_lab.py verify
    uv run python reference/condition_lab/cond_lab.py run --baseline A
    uv run python reference/condition_lab/cond_lab.py run --baseline B
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

REFERENCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REFERENCE / "multi_position"))
sys.path.insert(0, str(REFERENCE / "short_bollinger+RSI+volfilter"))
import multi_position_engine as engine  # noqa: E402
import vol_filter_engine as vfe  # noqa: E402

DATA = REFERENCE / "data_5m"
OUT = REFERENCE / "results" / "condition_lab"
FIRST_MONTH = "2021-01"
BASELINES = {
    "A": {"timeframe": "15min", "rsi_window": 5, "tp": 0.0, "trail": 1.5, "hold": 384,
          "space": {"boll_window": [150, 200, 300], "boll_window_dev": [2.0], "sl_multiplier": [0.5, 0.6, 0.8]}},
    "B": {"timeframe": "5min", "rsi_window": 14, "tp": 1.1, "trail": 0.0, "hold": 288,
          "space": {"boll_window": [200, 300, 400], "boll_window_dev": [2.0], "sl_multiplier": [0.5, 0.8, 1.1]}},
}
VOL_FILTER = (288, 90, 0.9)  # baseline A: vol_window, vol_days, vol_ratio_max
BAND_RATIO_MAX = 0.75  # baseline B


def coin_files() -> dict[str, Path]:
    return {p.name.split("_futures")[0]: p for p in sorted(DATA.glob("*_futures_5min.csv"))}


def split(coins: list[str]) -> dict[str, str]:
    """Fixed dev / confirm split: sorted names, alternating (BTC is not traded)."""
    tradable = sorted(c for c in coins if c != "btcusdt")
    return {c: ("dev" if i % 2 == 0 else "confirm") for i, c in enumerate(tradable)}


def param_sets(baseline: str) -> pd.DataFrame:
    space = BASELINES[baseline]["space"]
    return pd.DataFrame(itertools.product(*space.values()), columns=list(space))


# ---------------------------------------------------------------- features (causal: known at the close of bar t)

def _asof(bar_close: pd.Series, index: pd.DatetimeIndex, values: np.ndarray) -> np.ndarray:
    """Value of a series stamped at `index` (time it becomes known), as of each bar's close time."""
    right = pd.DataFrame({"known": index, "v": values}).dropna(subset=["known"]).sort_values("known")
    left = pd.DataFrame({"t": bar_close.to_numpy()})
    return pd.merge_asof(left, right, left_on="t", right_on="known", direction="backward")["v"].to_numpy()


def _htf(bars: pd.DataFrame, bar_close: pd.Series, rule: str) -> tuple[pd.Series, pd.Timedelta]:
    """Closes of completed higher-timeframe bars (stamped at their own close time)."""
    closes = bars.set_index("timestamp")["close"].resample(rule).last().dropna()
    return closes, pd.Timedelta(rule)


def daily_breadth(files: dict[str, Path]) -> pd.Series:
    """Share of coins (BTC included) with a daily close below their 50-day SMA, known at the end of the day."""
    below = {}
    for coin, path in files.items():
        d = pd.read_csv(path, usecols=["timestamp", "close"], parse_dates=["timestamp"]).set_index("timestamp")
        d = d["close"].resample("1D").last().dropna()
        sma = d.rolling(50, min_periods=50).mean()
        below[coin] = (d < sma).where(sma.notna())
    frame = pd.DataFrame(below)
    share = frame.mean(axis=1, skipna=True).where(frame.notna().sum(axis=1) >= 10)
    share.index = share.index + pd.Timedelta(days=1)  # known once the day has closed
    return share


def conditions(bars: pd.DataFrame, btc: pd.DataFrame, timeframe: str, funding: pd.DataFrame | None,
               breadth: pd.Series) -> dict[str, np.ndarray]:
    tf = pd.Timedelta(timeframe)
    bpd = int(round(pd.Timedelta(days=1) / tf))
    ts = bars["timestamp"]
    bar_close = ts + tf
    close = bars["close"].astype(float)
    high, low, volume = bars["high"].astype(float), bars["low"].astype(float), bars["volume"].astype(float)
    b_close = btc.set_index("timestamp")["close"].astype(float)
    b_al = b_close.reindex(ts).ffill().to_numpy()  # BTC close of the same bar (same timestamps)
    b_series = pd.Series(b_al)
    c = close.to_numpy()

    def ret(series: pd.Series, days: int) -> pd.Series:
        return series / series.shift(days * bpd) - 1

    cond: dict[str, np.ndarray] = {"none": np.ones(len(bars), dtype=bool)}
    for d in (20, 50, 100, 200):
        cond[f"btc_sma{d}d"] = b_series < b_series.rolling(d * bpd).mean()
    for d in (1, 7, 30):
        cond[f"btc_ret{d}d_neg"] = ret(b_series, d) < 0
    cond["btc_vol_low"] = vfe.vol_ratio(b_al, VOL_FILTER[0], VOL_FILTER[1], timeframe) < 1.0
    for d in (7, 30):
        cond[f"rel{d}d_weak"] = (ret(close, d) - ret(b_series, d)) < 0
    for d in (50, 200):
        cond[f"coin_sma{d}d"] = close < close.rolling(d * bpd).mean()
    br = _asof(bar_close, breadth.index, breadth.to_numpy())
    cond["breadth_ge50"] = br >= 0.5
    cond["breadth_ge65"] = br >= 0.65
    if funding is not None:
        f = funding.set_index("timestamp")["last_funding_rate"].astype(float).sort_index()
        last = _asof(bar_close, f.index, f.to_numpy())
        med30 = _asof(bar_close, f.index, f.rolling("30D").median().to_numpy())
        mean7 = _asof(bar_close, f.index, f.rolling("7D").mean().to_numpy())
        cond["fund_pos"] = last > 0
        cond["fund_above_neutral"] = last > 0.0001
        cond["fund_gt_med30"] = last > med30
        cond["fund7_pos"] = mean7 > 0
    else:
        for name in ("fund_pos", "fund_above_neutral", "fund_gt_med30", "fund7_pos"):
            cond[name] = np.zeros(len(bars), dtype=bool)
    rsi14, rsi5 = engine._cpu_rsi(c, 14), engine._cpu_rsi(c, 5)
    for x in (30, 40, 50):
        cond[f"rsi14_lt{x}"] = rsi14 < x
    for x in (20, 30):
        cond[f"rsi5_lt{x}"] = rsi5 < x
    for x in (25, 30, 35):
        cond[f"rsi14_gt{x}"] = rsi14 > x
    for x in (10, 20):
        cond[f"rsi5_gt{x}"] = rsi5 > x
    vratio = volume / volume.rolling(bpd).median().shift(1)
    cond["brk_vol_ge1.5"] = vratio >= 1.5
    cond["brk_vol_ge2"] = vratio >= 2.0
    cond["brk_vol_lt1"] = vratio < 1.0
    rng = (high - low).where(high > low)
    clv = (close - low) / rng
    cond["clv_le0.25"] = clv <= 0.25
    cond["clv_ge0.5"] = clv >= 0.5
    r = close.pct_change()
    sig = r.rolling(bpd).std().shift(1)
    cond["bar_big_2sig"] = r.abs() >= 2 * sig
    cond["bar_small_1sig"] = r.abs() < sig
    for rule, name in (("1h", "htf1h"), ("4h", "htf4h"), ("1D", "htf1d")):
        if pd.Timedelta(rule) <= tf:
            continue
        closes, width = _htf(bars, bar_close, rule)
        known = closes.index + width
        sma = closes.rolling(20).mean()
        cond[f"{name}_below_sma20"] = _asof(bar_close, known, (closes < sma).where(sma.notna()).to_numpy()) == 1
        if rule == "4h":
            cond["htf4h_rsi14_lt50"] = _asof(bar_close, known, engine._cpu_rsi(closes.to_numpy(), 14)) < 50
    hour = bar_close.dt.hour
    cond["sess_asia"] = (hour < 8).to_numpy()
    cond["sess_eu"] = ((hour >= 8) & (hour < 16)).to_numpy()
    cond["sess_us"] = (hour >= 16).to_numpy()
    weekend = bar_close.dt.weekday >= 5
    cond["weekday"] = (~weekend).to_numpy()
    cond["weekend"] = weekend.to_numpy()
    hh = high.rolling(30 * bpd).max()
    ll = low.rolling(30 * bpd).min()
    cond["near_30d_high"] = close >= 0.9 * hh
    cond["far_below_30d_high"] = close <= 0.8 * hh
    pos = (close - ll) / (hh - ll).where(hh > ll)
    cond["upper_half_30d"] = pos >= 0.5
    cond["lower_half_30d"] = pos < 0.5
    return {k: np.asarray(v, dtype=bool) for k, v in cond.items()}


def base_masks(baseline: str, bars: pd.DataFrame, band_keys: list[tuple[int, float]]) -> np.ndarray:
    """The baseline's own filter per band key (RSI < 100 kept so the NaN warm-up bars match the originals)."""
    cfg = BASELINES[baseline]
    close = bars["close"].to_numpy(np.float64)
    rsi_ok = engine._cpu_rsi(close, cfg["rsi_window"]) < 100
    if baseline == "A":
        vr = vfe.vol_ratio(close, VOL_FILTER[0], VOL_FILTER[1], cfg["timeframe"])
        return np.stack([rsi_ok & (vr < VOL_FILTER[2])] * len(band_keys))
    rows = []
    for w, d in band_keys:
        ratio = engine.band_width_ratio(bars, w, float(np.float32(d)))
        rows.append(rsi_ok & (ratio.astype(np.float32) <= np.float32(BAND_RATIO_MAX)))
    return np.stack(rows)


# ---------------------------------------------------------------- kernel (short side, one position at a time)

@nb.njit(parallel=True, cache=True)
def _kernel(close, high, low, upper, lower, base, cond, period, n_periods, start, fee_bars, entry_fee_rate,
            holding_fee_rate, max_fee_r, b_idx, k_idx, mults, tp, hold, trail):
    C = b_idx.size
    n = close.size
    R = np.zeros((C, n_periods))
    N = np.zeros((C, n_periods), dtype=np.int32)
    for c in nb.prange(C):
        b, k, m = b_idx[c], k_idx[c], mults[c]
        in_pos = False
        ei = 0
        entry = risk = stop = stop0 = best = target = 0.0
        for t in range(start, n):
            if not in_pos:
                if t < n - 1 and base[b, t] and cond[k, t]:
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
            if holding >= hold:
                gross = -(close[t] - entry) / risk
            elif high[t] > stop:
                gross = -1.0 if stop == stop0 else -(stop - entry) / risk
            elif tp > 0 and low[t] < target:
                gross = tp
            elif t == n - 1:
                gross = -(close[t] - entry) / risk
            else:
                if trail > 0:
                    best = min(best, low[t])
                    stop = min(stop, best + trail * risk)
                continue
            slr = risk / entry
            net = gross - entry_fee_rate / slr - holding_fee_rate * (holding / fee_bars) / slr
            p = period[t]
            if p >= 0:
                R[c, p] += net
                N[c, p] += 1
            in_pos = False
    return R, N


def run_coin(baseline: str, bars: pd.DataFrame, masks: dict[str, np.ndarray], names: list[str]):
    cfg = BASELINES[baseline]
    sets = param_sets(baseline)
    close = bars["close"].to_numpy(np.float64)
    band_keys = list(dict.fromkeys(zip(sets.boll_window.astype(int), sets.boll_window_dev.astype(float))))
    upper = np.empty((len(band_keys), len(close)))
    lower = np.empty_like(upper)
    for i, (w, d) in enumerate(band_keys):
        upper[i], lower[i] = engine._cpu_bands(close, w, d)
    base = base_masks(baseline, bars, band_keys)
    cond = np.stack([masks[name] for name in names])
    bmap = {k: i for i, k in enumerate(band_keys)}
    combos = [(bmap[(int(s.boll_window), float(s.boll_window_dev))], k, float(s.sl_multiplier))
              for s in sets.itertuples() for k in range(len(names))]
    b_idx, k_idx, mults = (np.array(x) for x in zip(*combos))
    period = vfe.month_index(bars, FIRST_MONTH)
    n_months = (pd.Timestamp("2026-10-01").to_period("M") - pd.Timestamp(FIRST_MONTH).to_period("M")).n + 1
    R, N = _kernel(close, bars["high"].to_numpy(np.float64), bars["low"].to_numpy(np.float64), upper, lower, base,
                   cond, period.astype(np.int64), n_months, max(cfg["rsi_window"], 1),
                   engine.resolve_fee_bars(cfg["timeframe"]), engine.ENTRY_FEE_RATE, engine.HOLDING_FEE_RATE,
                   engine.MAX_FEE_R, b_idx.astype(np.int64), k_idx.astype(np.int64), mults.astype(np.float64),
                   float(cfg["tp"]), int(cfg["hold"]), float(cfg["trail"]))
    shape = (len(sets), len(names), n_months)
    return R.reshape(shape), N.reshape(shape)


def load_coin(path: Path, timeframe: str) -> pd.DataFrame:
    return engine.load_bars(path, timeframe, "2021-01-01", "2026-10-19")


def load_funding(coin: str) -> pd.DataFrame | None:
    path = DATA / "funding" / f"{coin}.csv"
    if not path.is_file():
        return None
    return pd.read_csv(path, parse_dates=["timestamp"], date_format="mixed")


# ---------------------------------------------------------------- commands

def verify() -> None:
    files = coin_files()
    for coin in ("avaxusdt", "galausdt"):
        bars = load_coin(files[coin], "15min")
        masks = {"none": np.ones(len(bars), dtype=bool)}
        R, N = run_coin("A", bars, masks, ["none"])
        sets = param_sets("A")
        grid = sets.assign(rsi_window=5, rsi_threshold=100, tp_sl_ratio=0.0, max_holding_bars=384,
                           vol_window=VOL_FILTER[0], vol_days=VOL_FILTER[1], vol_ratio_max=VOL_FILTER[2], trail_r=1.5)
        R_ref, N_ref, _ = vfe.run_grid(vfe.SIDE_SHORT, bars, grid, "15min", vfe.month_index(bars, FIRST_MONTH))
        m = min(R.shape[2], R_ref.shape[1])
        print(f"A {coin}: max |dR| {np.abs(R[:, 0, :m] - R_ref[:, :m]).max():.2e}, "
              f"trade-count diffs {int(np.abs(N[:, 0, :m] - N_ref[:, :m]).sum())}, trades {int(N.sum())}")
    for coin in ("avaxusdt",):
        bars = load_coin(files[coin], "5min")
        R, N = run_coin("B", bars, {"none": np.ones(len(bars), dtype=bool)}, ["none"])
        month = vfe.month_index(bars, FIRST_MONTH)
        worst, diffs = 0.0, 0
        for i, s in enumerate(param_sets("B").itertuples()):
            params = {"boll_window": s.boll_window, "boll_window_dev": s.boll_window_dev, "rsi_window": 14,
                      "rsi_threshold": 100, "sl_multiplier": s.sl_multiplier, "tp_sl_ratio": 1.1,
                      "max_holding_bars": 288, "band_ratio_max": BAND_RATIO_MAX}
            tr = engine.cpu_trace(engine.SIDE_SHORT, bars, params, "5min", max_open=1)
            ref = np.zeros(R.shape[2])
            cnt = np.zeros(R.shape[2], dtype=int)
            np.add.at(ref, month[tr.exit_index.to_numpy(int)], tr.net_r.to_numpy())
            np.add.at(cnt, month[tr.exit_index.to_numpy(int)], 1)
            worst = max(worst, np.abs(R[i, 0] - ref).max())
            diffs += int(np.abs(N[i, 0] - cnt).sum())
        print(f"B {coin}: max |dR| vs cpu_trace {worst:.2e}, trade-count diffs {diffs}, trades {int(N.sum())}")


def run(baseline: str) -> None:
    cfg = BASELINES[baseline]
    files = coin_files()
    groups = split(list(files))
    breadth = daily_breadth(files)
    btc = load_coin(files["btcusdt"], cfg["timeframe"])
    OUT.mkdir(parents=True, exist_ok=True)
    results, names = {}, None
    t0 = time.perf_counter()
    for coin in groups:
        bars = load_coin(files[coin], cfg["timeframe"])
        masks = conditions(bars, btc, cfg["timeframe"], load_funding(coin), breadth)
        names = names or list(masks)
        R, N = run_coin(baseline, bars, masks, names)
        results[coin] = (R, N)
        print(f"{coin} ({groups[coin]}): {len(bars):,} bars, base trades {int(N[:, 0].sum() / len(N))}/set, "
              f"{time.perf_counter() - t0:.0f}s", flush=True)
    coins = list(results)
    np.savez_compressed(OUT / f"baseline_{baseline}.npz",
                        R=np.stack([results[c][0] for c in coins]), N=np.stack([results[c][1] for c in coins]),
                        coins=np.array(coins), groups=np.array([groups[c] for c in coins]),
                        conditions=np.array(names), first_month=FIRST_MONTH)
    param_sets(baseline).to_csv(OUT / f"baseline_{baseline}_sets.csv", index=False)
    print("saved", OUT / f"baseline_{baseline}.npz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["verify", "run"])
    parser.add_argument("--baseline", choices=list(BASELINES), default="A")
    args = parser.parse_args()
    verify() if args.command == "verify" else run(args.baseline)


if __name__ == "__main__":
    main()
