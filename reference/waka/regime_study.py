"""Entry conditions for the Waka grid / martingale: which ex-ante regimes make baskets safer (INSIGHTS §9n).

Pre-registered on 2026-10-21, before any outcome was looked at:
- Markets. crypto: the 47 coins of reference/data_5m, train < 2024-01-01, test >= 2024-01-01. fx: AUDCAD, AUDNZD,
  NZDCAD, train < 2023-01-01, test >= 2023-01-01. holdout: EURGBP, EURCHF, AUDCHF, NZDCHF, CADCHF, whole history,
  opened once with the frozen fx filter.
- Basket sample: one basket per direction at every full hour once 60 days of history exist; Waka defaults (lots
  1/1/2/4/8/12.8/..., 9 trades, money TP = 10/35 of the distance, no stop); distance = smart (35 pips x previous-30-day
  daily vol / AUDCAD's, ~1 daily sigma); first trade = 1 unit; closed at market after 30 days. Costs as run_waka.
- Labels: pnl in distance units of the first trade (net), full ladder (9 trades), worst floating pnl, first passage
  (+1 distance in favour before -8 against). Baseline per basket: a driftless log random walk gives
  -log(1 - 8f) / (log(1 + f) - log(1 - 8f)), f = distance / price (8/9 only for small f; ~0.92 for crypto).
- Features (data up to the bar before the entry): rv_4h, rv_24h (realised vol / daily sigma), vol_trend (24h vs 7d
  realised vol), er_24h, er_7d (efficiency ratio), mom_24h and dist_ma_7d (in sigma, signed by the basket's
  direction), width_7d (7-day high-low range / sigma), jump_24h (largest 5m move / 5m sigma), vr_60d (variance ratio
  of 1-day vs 1-hour returns over 60 days); crypto also funding (last rate in bp, signed: + = the basket pays) and
  btc_rv_24h. hour and weekday are reported, never selected.
- Selection, train only, per market, directions pooled: deciles of each feature -> mean basket pnl; a feature is kept
  if |Spearman(decile, mean pnl)| >= 0.7 and the top-vs-bottom tercile difference of calendar-week mean pnl has
  |t| >= 3; at most 3 features, ranked by |t|, skipping one whose |corr| with a kept feature > 0.7.
- Filter: every kept feature on its better side of the train median (AND).
- Test. Basket level: filtered minus unfiltered mean pnl, t over calendar weeks >= 2. Account level (waka_kernel, the
  run_waka medium sizing, smart distance): entries at every full hour where the filter holds (per direction) over
  the test period; PASS needs Sharpe >= 0.5, max equity DD <= 30%, Sharpe > p95 of 20 random-entry runs with the same
  monthly counts per direction, and Sharpe > all-hours entries. All four plus the basket test.

    uv run python reference/waka/regime_study.py build --markets crypto fx holdout
    uv run python reference/waka/regime_study.py select --markets crypto fx
    uv run python reference/waka/regime_study.py test --markets crypto fx
    uv run python reference/waka/regime_study.py holdout
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_waka as rw  # noqa: E402

wk = rw.wk
OUT = rw.OUT / "regime"
SPLIT = {"crypto": "2024-01-01", "fx": "2023-01-01"}
FX_PAIRS = rw.CROSSES
HOLDOUT_PAIRS = ["EURGBP", "EURCHF", "AUDCHF", "NZDCHF", "CADCHF"]
FRESH_PAIRS = ["EURAUD", "EURNZD", "EURCAD", "GBPAUD", "GBPNZD", "GBPCAD", "GBPCHF"]  # first_passage.py (§9o)
PAIRS = {"fx": FX_PAIRS, "holdout": HOLDOUT_PAIRS, "fresh": FRESH_PAIRS}
MAX_BARS = 30 * 288
FP_DOWN = 8.0
WEEK = 7 * 86_400 * 10**9
SELECTABLE = ["rv_4h", "rv_24h", "vol_trend", "er_24h", "er_7d", "mom_24h", "dist_ma_7d", "width_7d", "jump_24h",
              "vr_60d", "funding", "btc_rv_24h"]
SIGNED = {"mom_24h", "dist_ma_7d", "funding"}
CORE = rw.Waka(smart=True)
N_RANDOM = 20


# ---------------------------------------------------------------------------------------------- features

def raw_features(m: rw.Market, s: int) -> pd.DataFrame:
    """Per-bar features from data up to the previous bar (shifted by one), unsigned."""
    c = pd.Series(m.bc[s])
    h = pd.Series(m.bh[s])
    lo = pd.Series(m.bl[s])
    sig = pd.Series(m.vol[s]).replace(0.0, np.nan)
    r = np.log(c).diff()
    r2 = r**2
    absdiff = c.diff().abs()
    rv = lambda n: np.sqrt(r2.rolling(n).sum())  # noqa: E731
    f = pd.DataFrame({
        "rv_4h": rv(48) / (sig * np.sqrt(4 / 24)),
        "rv_24h": rv(288) / sig,
        "vol_trend": rv(288) / (rv(2016) / np.sqrt(7)),
        "er_24h": (c - c.shift(288)).abs() / absdiff.rolling(288).sum(),
        "er_7d": (c - c.shift(2016)).abs() / absdiff.rolling(2016).sum(),
        "mom_24h": np.log(c / c.shift(288)) / sig,
        "dist_ma_7d": (c / c.rolling(2016).mean() - 1) / sig,
        "width_7d": (h.rolling(2016).max() / lo.rolling(2016).min() - 1) / (sig * np.sqrt(7)),
        "jump_24h": r.abs().rolling(288).max() / (sig / np.sqrt(288)),
        "vr_60d": np.log(c / c.shift(288)).rolling(17280).var() / (24 * np.log(c / c.shift(12)).rolling(17280).var()),
    })
    if m.kind == "crypto":
        rate = pd.Series(np.where(m.carry_long[s] != 0, m.carry_long[s], np.nan)).ffill()
        f["funding"] = rate * 1e4
    return f.shift(1)


def entry_bars(m: rw.Market) -> np.ndarray:
    stamps = pd.to_datetime(m.ts)
    ok = (np.asarray(stamps.minute) == 0) & (np.arange(len(m.ts)) >= max(m.start, 17280 + 2016))
    return np.flatnonzero(ok)


def costs(kind: str) -> tuple[float, float, float]:
    if kind == "fx":
        return rw.FX_COMM, rw.FX_COMM, 0.0
    return rw.CRYPTO_TAKER, rw.CRYPTO_MAKER, rw.CRYPTO_SLIP


def symbol_baskets(m: rw.Market, s: int, btc: pd.Series | None) -> pd.DataFrame:
    idx = entry_bars(m)
    dist = rw.kernel_inputs(m, CORE)["dist"][s]
    idx = idx[dist[idx] > 0]
    starts = np.repeat(idx, 2)
    dirs = np.tile(np.array([1, -1], np.int8), len(idx))
    comm_mkt, comm_lim, slip = costs(m.kind)
    c = lambda a: np.ascontiguousarray(a[s])  # noqa: E731
    out = wk.basket_outcomes(c(m.bo), c(m.bh), c(m.bl), c(m.bc), c(m.spread), c(m.carry_long), c(m.carry_short),
                             starts.astype(np.int64), dirs, dist[starts], wk.lot_sequence(9, 1.0, 2.0, 1.6),
                             10 / 35, comm_mkt, comm_lim, slip, MAX_BARS, FP_DOWN)
    feats = raw_features(m, s).iloc[starts].reset_index(drop=True)
    for col in SIGNED & set(feats.columns):
        feats[col] = feats[col] * dirs
    stamps = pd.to_datetime(m.ts[starts])
    dfrac = dist[starts] / m.bo[s, starts]
    up, down = np.log1p(dfrac), -np.log(np.clip(1 - FP_DOWN * dfrac, 1e-9, None))
    frame = pd.DataFrame({"symbol": m.symbols[s], "bar": starts, "ts": stamps, "dir": dirs, "dfrac": dfrac,
                          "fp_base": down / (up + down),
                          "pnl": out[:, wk.O_PNL], "trades": out[:, wk.O_TRADES], "worst": out[:, wk.O_WORST],
                          "bars": out[:, wk.O_BARS], "tp": out[:, wk.O_TP], "mae": out[:, wk.O_MAE],
                          "fp": out[:, wk.O_FP], "hour": stamps.hour, "weekday": stamps.dayofweek})
    frame = pd.concat([frame, feats], axis=1)
    if btc is not None:
        frame["btc_rv_24h"] = btc.reindex(stamps).to_numpy()
    return frame


def btc_rv() -> pd.Series:
    m = rw.crypto_market(rw.rg.load_coin("btcusdt"))
    f = raw_features(m, 0)
    return pd.Series(f["rv_24h"].to_numpy(), index=pd.to_datetime(m.ts))


def build(market: str) -> None:
    frames = []
    if market == "crypto":
        btc = btc_rv()
        for sym in rw.crypto_symbols():
            m = rw.crypto_market(rw.rg.load_coin(sym))
            frames.append(symbol_baskets(m, 0, btc))
            print(f"  {sym}: {len(frames[-1])} baskets", flush=True)
    else:
        m = rw.load_fx(PAIRS[market], market)
        for s in range(len(m.symbols)):
            frames.append(symbol_baskets(m, s, None))
            print(f"  {m.symbols[s]}: {len(frames[-1])} baskets", flush=True)
    data = pd.concat(frames, ignore_index=True)
    floats = data.select_dtypes("float64").columns
    data[floats] = data[floats].astype("float32")
    data.to_parquet(OUT / f"baskets_{market}.parquet")
    print(f"{market}: {len(data)} baskets saved", flush=True)


# --------------------------------------------------------------------------------------------- selection

def week_t(values: pd.Series, weeks: pd.Series) -> float:
    w = values.groupby(weeks).mean().dropna()
    return float(w.mean() / (w.std() / np.sqrt(len(w)))) if len(w) > 2 and w.std() > 0 else np.nan


def feature_table(data: pd.DataFrame, feature: str) -> tuple[pd.DataFrame, dict]:
    x = data[feature]
    ok = x.notna()
    d = data[ok]
    dec = pd.qcut(d[feature], 10, labels=False, duplicates="drop")
    fp = d["fp"].where(d["fp"] >= 0)
    table = pd.DataFrame({"mean_pnl": d["pnl"].groupby(dec).mean(), "full_ladder": (d["trades"] >= 9).groupby(dec).mean(),
                          "fp_up_first": fp.groupby(dec).mean(),
                          "fp_minus_base": (fp - d["fp_base"].where(fp.notna())).groupby(dec).mean(),
                          "worst_p01": d["worst"].groupby(dec).quantile(0.01),
                          "lo": d[feature].groupby(dec).min(), "hi": d[feature].groupby(dec).max(),
                          "n": d["pnl"].groupby(dec).size()})
    rho = pd.Series(table.index).corr(table["mean_pnl"].reset_index(drop=True), method="spearman")
    q1, q2 = d[feature].quantile([1 / 3, 2 / 3])
    weeks = d["ts"].astype("int64") // WEEK
    top = d["pnl"].where(d[feature] >= q2).groupby(weeks).mean()
    bottom = d["pnl"].where(d[feature] <= q1).groupby(weeks).mean()
    diff = (top - bottom).dropna()
    t = float(diff.mean() / (diff.std() / np.sqrt(len(diff)))) if len(diff) > 2 else np.nan
    return table, {"feature": feature, "spearman_deciles": rho, "top_minus_bottom": float(diff.mean()), "t_weeks": t,
                   "median": float(d[feature].median())}


def select(market: str) -> None:
    data = pd.read_parquet(OUT / f"baskets_{market}.parquet")
    train = data[data["ts"] < SPLIT[market]]
    weeks = train["ts"].astype("int64") // WEEK
    fp = train["fp"].where(train["fp"] >= 0)
    print(f"{market} train: {len(train)} baskets, mean pnl {train['pnl'].mean():+.3f} (week t "
          f"{week_t(train['pnl'], weeks):+.2f}), full ladder {np.mean(train['trades'] >= 9):.3f}, "
          f"P(+1 before -8) {fp.mean():.4f} vs random-walk baseline {train['fp_base'][fp.notna()].mean():.4f}",
          flush=True)
    rows, tables = [], []
    for feature in [f for f in SELECTABLE if f in train.columns] + ["hour", "weekday"]:
        table, row = feature_table(train, feature)
        tables.append(table.assign(feature=feature))
        rows.append(row)
    stats = pd.DataFrame(rows).set_index("feature")
    pd.concat(tables).to_csv(OUT / f"deciles_train_{market}.csv")
    stats.to_csv(OUT / f"feature_stats_train_{market}.csv")
    print(stats.round(3).to_string(), flush=True)
    candidates = stats.loc[[f for f in SELECTABLE if f in stats.index]]
    candidates = candidates[(candidates["spearman_deciles"].abs() >= 0.7) & (candidates["t_weeks"].abs() >= 3)]
    candidates = candidates.reindex(candidates["t_weeks"].abs().sort_values(ascending=False).index)
    kept = []
    for feature, row in candidates.iterrows():
        if len(kept) == 3:
            break
        if any(abs(train[feature].corr(train[k["feature"]])) > 0.7 for k in kept):
            continue
        kept.append({"feature": feature, "median": row["median"], "keep": "above" if row["t_weeks"] > 0 else "below",
                     "t_weeks": row["t_weeks"], "spearman_deciles": row["spearman_deciles"]})
    frozen = {"market": market, "split": SPLIT[market], "features": kept}
    (OUT / f"filter_{market}.json").write_text(json.dumps(frozen, indent=1))
    print(f"{market}: frozen filter {json.dumps(kept)}", flush=True)


# ------------------------------------------------------------------------------------------------ testing

def apply_filter(data: pd.DataFrame, frozen: dict) -> pd.Series:
    mask = pd.Series(True, index=data.index)
    for f in frozen["features"]:
        x = data[f["feature"]]
        mask &= (x > f["median"]) if f["keep"] == "above" else (x <= f["median"])
    return mask


def basket_level(data: pd.DataFrame, mask: pd.Series) -> dict:
    weeks = data["ts"].astype("int64") // WEEK
    fp = data["fp"].where(data["fp"] >= 0)
    per_week = data["pnl"].where(mask).groupby(weeks).mean() - data["pnl"].groupby(weeks).mean()
    per_week = per_week.dropna()
    return {"baskets": len(data), "share_filtered": float(mask.mean()),
            "mean_pnl_all": float(data["pnl"].mean()), "mean_pnl_filtered": float(data.loc[mask, "pnl"].mean()),
            "diff_t_weeks": float(per_week.mean() / (per_week.std() / np.sqrt(len(per_week)))),
            "full_ladder_all": float(np.mean(data["trades"] >= 9)),
            "full_ladder_filtered": float(np.mean(data.loc[mask, "trades"] >= 9)),
            "fp_all": float(fp.mean()), "fp_filtered": float(fp[mask].mean()),
            "fp_base_all": float(data["fp_base"][fp.notna()].mean()),
            "fp_base_filtered": float(data["fp_base"][fp.notna() & mask].mean()),
            "worst_p01_all": float(data["worst"].quantile(0.01)),
            "worst_p01_filtered": float(data.loc[mask, "worst"].quantile(0.01))}


def signals_from(data: pd.DataFrame, mask: pd.Series, symbols: list[str], T: int) -> np.ndarray:
    sig = np.zeros((len(symbols), T), np.int8)
    rows = data[mask]
    for s, sym in enumerate(symbols):
        r = rows[rows["symbol"] == sym]
        longs = r.loc[r["dir"] > 0, "bar"].to_numpy()
        shorts = r.loc[r["dir"] < 0, "bar"].to_numpy()
        sig[s, longs] = 1
        both = np.intersect1d(longs, shorts)
        sig[s, np.setdiff1d(shorts, both)] = -1
        sig[s, both] = 2
    return sig


def random_like(data: pd.DataFrame, mask: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Same number of filtered entries per symbol, month and direction, at random entry hours."""
    month = data["ts"].dt.to_period("M")
    keys = [data["symbol"], month, data["dir"]]
    counts = mask.groupby(keys).transform("sum")
    sizes = mask.groupby(keys).transform("size")
    u = pd.Series(rng.random(len(data)), index=data.index)
    rank = u.groupby(keys).rank(method="first")
    return rank <= counts.where(sizes > 0, 0)


def account(market: str, data: pd.DataFrame, mask: pd.Series, period_start: str) -> pd.Series:
    """Daily account returns of entries where mask holds, from period_start (crypto: mean of coin sleeves)."""
    if market == "crypto":
        equities = {}
        for sym in rw.crypto_symbols():
            m = rw.crypto_market(rw.rg.load_coin(sym))
            m.start = max(m.start, int(np.searchsorted(m.ts, pd.Timestamp(period_start).value)))
            if m.start >= len(m.ts) - 288:
                continue
            sig = signals_from(data[data["symbol"] == sym], mask[data["symbol"] == sym], [sym], len(m.ts))
            equities[sym] = rw.run(m, CORE, sig)["daily"]["equity"]
        return rw.rg.portfolio(equities)
    m = rw.load_fx(PAIRS[market], market)
    m.start = max(m.start, int(np.searchsorted(m.ts, pd.Timestamp(period_start).value)))
    sig = signals_from(data, mask, m.symbols, len(m.ts))
    return rw.run(m, CORE, sig)["daily"]["equity"].pct_change().dropna()


def account_stats(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    return {"sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0,
            "total": float(eq.iloc[-1] - 1), "max_dd": float((eq / eq.cummax() - 1).min())}


def evaluate(market: str, data: pd.DataFrame, frozen: dict, period_start: str, tag: str) -> dict:
    period = data[data["ts"] >= period_start]
    if not frozen["features"]:
        print(f"{tag}: no feature passed selection -> nothing to test", flush=True)
        return {"tag": tag, "features": 0}
    mask = apply_filter(period, frozen)
    bl = basket_level(period, mask)
    print(f"{tag} basket level: {json.dumps({k: round(v, 4) for k, v in bl.items()})}", flush=True)
    filtered = account_stats(account(market, period, mask, period_start))
    everything = account_stats(account(market, period, pd.Series(True, index=period.index), period_start))
    rng = np.random.default_rng(23)
    randoms = []
    for i in range(N_RANDOM):
        randoms.append(account_stats(account(market, period, random_like(period, mask, rng), period_start))["sharpe"])
        print(f"  random {i}: sharpe {randoms[-1]:+.2f}", flush=True)
    p95 = float(np.quantile(randoms, 0.95))
    row = {"tag": tag, **{f"basket_{k}": v for k, v in bl.items()},
           **{f"filtered_{k}": v for k, v in filtered.items()}, **{f"all_hours_{k}": v for k, v in everything.items()},
           "random_sharpe_median": float(np.median(randoms)), "random_sharpe_p95": p95}
    row.update({
        "B basket filtered > all, t >= 2": bl["diff_t_weeks"] >= 2,
        "A1 sharpe >= 0.5 and DD <= 30%": filtered["sharpe"] >= 0.5 and filtered["max_dd"] >= -0.30,
        "A2 sharpe > random p95": filtered["sharpe"] > p95,
        "A3 sharpe > all hours": filtered["sharpe"] > everything["sharpe"],
    })
    row["PASS"] = all(row[k] for k in row if k[:2] in ("B ", "A1", "A2", "A3"))
    print(f"{tag}: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, default=str)}",
          flush=True)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "select", "test", "holdout"])
    ap.add_argument("--markets", nargs="+", default=["crypto", "fx"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.step == "build":
        for market in args.markets:
            build(market)
    elif args.step == "select":
        for market in args.markets:
            select(market)
    elif args.step == "test":
        rows = []
        for market in args.markets:
            frozen = json.loads((OUT / f"filter_{market}.json").read_text())
            data = pd.read_parquet(OUT / f"baskets_{market}.parquet")
            rows.append(evaluate(market, data, frozen, SPLIT[market], f"{market} test"))
        pd.DataFrame(rows).to_csv(OUT / "test_results.csv", index=False)
    else:
        frozen = json.loads((OUT / "filter_fx.json").read_text())
        data = pd.read_parquet(OUT / "baskets_holdout.parquet")
        row = evaluate("holdout", data, frozen, str(data["ts"].min().date()), "holdout (fx filter)")
        pd.DataFrame([row]).to_csv(OUT / "holdout_results.csv", index=False)


if __name__ == "__main__":
    main()
