"""Waka Waka EA reproduction: BB/RSI fade baskets with a limited martingale grid on forex crosses, EURUSD and crypto.

Design fixed in the plan of 2026-10-21 (INSIGHTS §9m), kernel in engine.py. Markets:
- crosses: AUDCAD, AUDNZD, NZDCAD in one shared account (FXCM 1m bid/ask -> 5m, reference/data_fx/), 2018 ->
- eurusd: FXCM EURUSD, same rules with the volatility-scaled ("smart") distance
- eurusd_user: reference/EURUSD5.csv (bid only, 2024-09 -> 2026-01), illustration only
- crypto: the 47 coins of reference/data_5m, one sleeve per coin, smart distance, real funding

Run from the repository root:

    uv run python reference/waka/run_waka.py --nulls 20
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
sys.path.insert(0, str(REFERENCE / "grid_martingale"))
sys.path.insert(0, str(REFERENCE / "portfolio_trend"))
import run_grid_martingale as rg  # noqa: E402
from daily_trend import metrics  # noqa: E402

_spec = importlib.util.spec_from_file_location("waka_engine", HERE / "engine.py")  # grid_martingale has an engine.py
wk = sys.modules.setdefault("waka_engine", importlib.util.module_from_spec(_spec))  # numba's cache re-imports it
if not hasattr(wk, "waka_kernel"):
    _spec.loader.exec_module(wk)

FX_DIR = REFERENCE / "data_fx"
EURUSD_USER = REFERENCE / "EURUSD5.csv"
OUT = REFERENCE / "results" / "waka"
PIP = 1e-4
CROSSES = ["AUDCAD", "AUDNZD", "NZDCAD"]
FRED = {"AUD": "IR3TIB01AUM156N", "NZD": "IR3TIB01NZM156N", "CAD": "IR3TIB01CAM156N", "USD": "IR3TIB01USM156N",
        "EUR": "IR3TIB01EZM156N"}
SWAP_MARKUP = 0.01  # broker markup per year on each side
FX_COMM = 35e-6  # 35 USD per million per side
USER_SPREAD = 0.2 * PIP
CRYPTO_MAKER, CRYPTO_TAKER, CRYPTO_SLIP = rg.MAKER, rg.TAKER, rg.SLIP
EPISODES = {"2022-11 AUDCAD": ("2022-11-01", "2022-11-30"), "2023-06 AUDCAD": ("2023-06-12", "2023-06-23"),
            "2024-03..06 AUDNZD/NZDCAD": ("2024-03-01", "2024-06-30")}


@dataclass(frozen=True)
class Waka:
    bb_window: int = 20
    bb_dev: float = 2.0
    rsi_window: int = 14
    rsi_high: float = 70.0
    rsi_low: float = 30.0
    smart: bool = False  # False: fixed distance in pips; True: distance scaled by the previous-30-day volatility
    distance_pips: float = 35.0
    tp_pips: float = 10.0
    sl_pips: float = 1000.0
    max_trades: int = 9
    m2: float = 1.0
    m35: float = 2.0
    m6: float = 1.6
    deposit_load: float = 0.005  # margin of the first trade / balance at 1:100 (medium)
    max_dd: float = 1.0  # floating drawdown exit; 1 = off (default)
    pause_hours: float = 24.0
    be_level: int = 0
    rollover_filter: bool = True  # forex: no new baskets 20:30-23:00 UTC
    reverse: bool = False  # Waka's "Reverse Strategy": buy above the upper band, sell below the lower one
    path_mode: int = 0
    touch: bool = False


@dataclass
class Market:
    name: str
    kind: str  # "fx" or "crypto"
    symbols: list[str]
    ts: np.ndarray  # int64 ns, bar open (UTC)
    bo: np.ndarray  # [S, T] bid
    bh: np.ndarray
    bl: np.ndarray
    bc: np.ndarray
    spread: np.ndarray
    carry_long: np.ndarray
    carry_short: np.ndarray
    days: pd.DatetimeIndex = field(default=None)
    day_idx: np.ndarray = field(default=None)
    vol: np.ndarray = field(default=None)  # [S, T] previous-30-day std of daily log returns
    start: int = 0
    _signals: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ data

def prepare(m: Market) -> Market:
    stamps = pd.to_datetime(m.ts)
    day = stamps.normalize()
    m.days = pd.DatetimeIndex(day.unique())
    m.day_idx = m.days.get_indexer(day).astype(np.int64)
    m.vol = np.zeros_like(m.bc)
    for s in range(len(m.symbols)):
        daily = pd.Series(m.bc[s], index=stamps).groupby(day).last()
        vol = np.log(daily).diff().rolling(30).std().shift(1)
        m.vol[s] = np.nan_to_num(vol.to_numpy()[m.day_idx], nan=0.0)
    first = m.days[min(31, len(m.days) - 1)]
    m.start = int(np.searchsorted(m.ts, first.value))
    m._signals = {}
    return m


def load_rates() -> pd.DataFrame | None:
    """Monthly 3-month interbank rates (%, OECD via FRED), forward-filled; None when unavailable."""
    path = FX_DIR / "rates.csv"
    marker = FX_DIR / "rates_unavailable.txt"  # delete it to retry FRED
    if marker.is_file():
        return None
    if not path.is_file():
        try:
            series = {}
            for cur, sid in FRED.items():
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
                raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                             timeout=60).read()
                frame = pd.read_csv(pd.io.common.BytesIO(raw), na_values=".")
                series[cur] = frame.set_index(frame.columns[0]).iloc[:, 0].astype(float)
            pd.DataFrame(series).to_csv(path)
        except Exception as exc:  # noqa: BLE001
            print("FRED rates unavailable, using -1%/year swap on both sides:", exc)
            marker.write_text(f"FRED download failed: {exc}\n")
            return None
    rates = pd.read_csv(path, index_col=0, parse_dates=True)
    return rates.resample("MS").last().ffill()


def swap_carry(symbols: list[str], ts: np.ndarray, rates: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray]:
    """Daily swap as a fraction of notional on the first bar at or after 21:00 UTC of each weekday, x3 on Wednesday.
    Long cost = (r_quote - r_base + markup) / 360, short cost = (r_base - r_quote + markup) / 360."""
    stamps = pd.to_datetime(ts)
    long_c = np.zeros((len(symbols), len(ts)))
    short_c = np.zeros_like(long_c)
    frame = pd.DataFrame({"ts": stamps, "i": np.arange(len(ts))})
    frame = frame[(stamps.hour >= 21) & (stamps.dayofweek < 5)]
    rows = frame.groupby(frame["ts"].dt.normalize())["i"].first()
    mult = np.where(rows.index.dayofweek == 2, 3.0, 1.0)
    for s, sym in enumerate(symbols):
        if rates is None:
            diff = np.zeros(len(rows))
        else:
            month = rates.reindex(rows.index.to_period("M").to_timestamp(), method="ffill")
            diff = ((month[sym[:3]] - month[sym[3:6]]) / 100).fillna(0.0).to_numpy()  # r_base - r_quote
        long_c[s, rows.to_numpy()] = (-diff + SWAP_MARKUP) / 360 * mult
        short_c[s, rows.to_numpy()] = (diff + SWAP_MARKUP) / 360 * mult
    return long_c, short_c


def load_fx(pairs: list[str], name: str) -> Market:
    frames = []
    for pair in pairs:
        f = pd.read_csv(FX_DIR / f"{pair.lower()}_5min.csv", parse_dates=["timestamp"], index_col="timestamp")
        frames.append(f.add_prefix(pair + "_"))
    joined = pd.concat(frames, axis=1, join="inner").sort_index()
    ts = joined.index.to_numpy("datetime64[ns]").astype(np.int64)
    get = lambda col: np.stack([joined[f"{p}_{col}"].to_numpy(float) for p in pairs])  # noqa: E731
    spread = np.clip(((get("ask_open") - get("bid_open")) + (get("ask_close") - get("bid_close"))) / 2, 0, None)
    carry_long, carry_short = swap_carry(pairs, ts, load_rates())
    m = Market(name, "fx", pairs, ts, get("bid_open"), get("bid_high"), get("bid_low"), get("bid_close"), spread,
               carry_long, carry_short)
    return prepare(m)


def load_eurusd_user() -> Market:
    f = pd.read_csv(EURUSD_USER, sep="\t", header=None, names=["timestamp", "o", "h", "l", "c", "v"],
                    parse_dates=["timestamp"])
    ts = f["timestamp"].to_numpy("datetime64[ns]").astype(np.int64)
    arr = lambda k: f[k].to_numpy(float)[None, :]  # noqa: E731
    carry_long, carry_short = swap_carry(["EURUSD"], ts, load_rates())
    m = Market("eurusd_user", "fx", ["EURUSD"], ts, arr("o"), arr("h"), arr("l"), arr("c"),
               np.full((1, len(ts)), USER_SPREAD), carry_long, carry_short)
    return prepare(m)


def crypto_market(coin: rg.Coin) -> Market:
    one = lambda a: a[None, :]  # noqa: E731
    m = Market(coin.symbol, "crypto", [coin.symbol], coin.ts, one(coin.o), one(coin.h), one(coin.l), one(coin.c),
               np.zeros((1, len(coin.ts))), one(coin.funding), one(-coin.funding))
    return prepare(m)


def crypto_symbols() -> list[str]:
    return sorted(p.name.split("_futures")[0] for p in rg.DATA_DIR.glob("*_futures_5min.csv"))


def shuffled_fx(m: Market, rng: np.random.Generator) -> Market:
    """Permute whole bars (shape relative to the previous close, spread carried along) within each calendar week,
    with the same order for every symbol of the account."""
    week = m.ts // (7 * 86_400 * 10**9)
    order = np.lexsort((rng.random(len(week)), week))
    arrays = []
    for s in range(len(m.symbols)):
        prev = np.concatenate([[m.bo[s, 0]], m.bc[s, :-1]])
        rel = np.stack([m.bo[s] / prev, m.bh[s] / prev, m.bl[s] / prev, m.bc[s] / prev])[:, order]
        close = m.bo[s, 0] * np.exp(np.cumsum(np.log(rel[3])))
        prev_new = np.concatenate([[m.bo[s, 0]], close[:-1]])
        arrays.append((prev_new * rel[0], prev_new * rel[1], prev_new * rel[2], close))
    stack = lambda k: np.stack([a[k] for a in arrays])  # noqa: E731
    new = Market(m.name, m.kind, m.symbols, m.ts, stack(0), stack(1), stack(2), stack(3), m.spread[:, order],
                 m.carry_long, m.carry_short)
    return prepare(new)


# --------------------------------------------------------------------------------------------- signals

def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down)


def signals(m: Market, w: Waka) -> np.ndarray:
    """+1 buy / -1 sell at the open of the 5m bar that starts when the signalling M15 bar has closed."""
    key = (w.bb_window, w.bb_dev, w.rsi_window, w.rsi_high, w.rsi_low, w.rollover_filter, w.reverse)
    if key in m._signals:
        return m._signals[key]
    stamps = pd.to_datetime(m.ts)
    out = np.zeros(m.bc.shape, np.int8)
    for s in range(len(m.symbols)):
        c15 = pd.Series(m.bc[s], index=stamps).resample("15min", label="left", closed="left").last().dropna()
        mid = c15.rolling(w.bb_window).mean()
        sd = c15.rolling(w.bb_window).std(ddof=0)
        r = rsi(c15, w.rsi_window)
        sig = np.where((c15 < mid - w.bb_dev * sd) & (r <= w.rsi_low), 1,
                       np.where((c15 > mid + w.bb_dev * sd) & (r >= w.rsi_high), -1, 0))
        act = (c15.index + pd.Timedelta(minutes=15)).to_numpy("datetime64[ns]").astype(np.int64)
        pos = np.searchsorted(m.ts, act)
        ok = (pos < len(m.ts)) & (m.ts[np.minimum(pos, len(m.ts) - 1)] == act) & (sig != 0)
        out[s, pos[ok]] = sig[ok]
    out[:, : m.start] = 0
    if m.kind == "fx" and w.rollover_filter:
        minute = stamps.hour * 60 + stamps.minute
        out[:, (minute >= 20 * 60 + 30) & (minute < 23 * 60)] = 0
    if w.reverse:
        out = -out
    m._signals[key] = out
    return out


def random_signals(m: Market, real: np.ndarray, rng: np.random.Generator, w: Waka) -> np.ndarray:
    """Same number of buy and sell signal bars per symbol and calendar month, at random eligible M15 boundaries."""
    stamps = pd.to_datetime(m.ts)
    month = np.asarray(stamps.year * 12 + stamps.month)
    eligible = np.asarray(stamps.minute % 15 == 0) & (np.arange(len(m.ts)) >= m.start)
    if m.kind == "fx" and w.rollover_filter:
        minute = np.asarray(stamps.hour * 60 + stamps.minute)
        eligible &= ~((minute >= 20 * 60 + 30) & (minute < 23 * 60))
    out = np.zeros_like(real)
    for s in range(real.shape[0]):
        for mo in np.unique(month[eligible]):
            idx = np.flatnonzero(eligible & (month == mo))
            n_buy = int(((real[s] == 1) & (month == mo)).sum())
            n_sell = int(((real[s] == -1) & (month == mo)).sum())
            pick = rng.permutation(idx)[: min(len(idx), n_buy + n_sell)]
            out[s, pick[:n_buy]] = 1
            out[s, pick[n_buy:]] = -1
    return out


# ------------------------------------------------------------------------------------------------- run

def smart_calibration() -> dict:
    """35 pips relative to AUDCAD's median daily move (price units) and to its median price, 2018-2020."""
    path = FX_DIR / "smart_calibration.json"
    if path.is_file():
        return json.loads(path.read_text())
    m = load_fx(["AUDCAD"], "audcad")
    stamps = pd.to_datetime(m.ts)
    window = (stamps >= "2018-01-01") & (stamps < "2021-01-01") & (m.vol[0] > 0)
    cal = {"move": float(np.median(m.vol[0, window] * m.bc[0, window])), "price": float(np.median(m.bc[0, window]))}
    cal["k"] = 35 * PIP / cal["move"]  # distance = k x vol x price
    cal["ref_dfrac"] = 35 * PIP / cal["price"]  # AUDCAD's distance as a fraction of price
    path.write_text(json.dumps(cal, indent=1))
    return cal


def kernel_inputs(m: Market, w: Waka) -> dict:
    lots = wk.lot_sequence(w.max_trades, w.m2, w.m35, w.m6)
    base = w.deposit_load * 100  # first trade notional / balance at 1:100
    if w.smart:
        cal = smart_calibration()
        dist = w.distance_pips / 35 * cal["k"] * m.vol * m.bo
        frac = np.where(dist > 0, dist / m.bo, np.inf)
        notional = base * (w.distance_pips * PIP / cal["price"]) / frac  # same % loss per distance as AUDCAD
    else:
        dist = np.full(m.bo.shape, w.distance_pips * PIP)
        notional = np.full(m.bo.shape, base)
    if m.kind == "fx":
        costs = dict(comm_mkt=FX_COMM, comm_lim=FX_COMM, slip=0.0, leverage=100.0)
    else:
        costs = dict(comm_mkt=CRYPTO_TAKER, comm_lim=CRYPTO_MAKER, slip=CRYPTO_SLIP, leverage=20.0)
    return dict(dist=dist, notional=notional, lots=lots, **costs)


def run(m: Market, w: Waka, signal: np.ndarray | None = None, notional: np.ndarray | None = None) -> dict:
    """notional: first-trade notional per unit of balance [S, T], overriding the deposit-load sizing."""
    sig = signals(m, w) if signal is None else signal
    k = kernel_inputs(m, w)
    if notional is not None:
        k["notional"] = notional
    a = m.start
    cut = lambda x: np.ascontiguousarray(x[:, a:])  # noqa: E731
    day_idx = m.day_idx[a:] - m.day_idx[a]
    n_days = int(day_idx[-1] + 1)
    eq, bal, mn, log, counters = wk.waka_kernel(
        cut(m.bo), cut(m.bh), cut(m.bl), cut(m.bc), cut(m.spread), np.ones(m.bo[:, a:].shape, np.bool_), cut(sig),
        cut(k["dist"]), cut(k["notional"]), cut(m.carry_long), cut(m.carry_short), day_idx, n_days, k["lots"],
        w.tp_pips / w.distance_pips, w.sl_pips / w.distance_pips, k["comm_mkt"], k["comm_lim"], k["slip"],
        w.be_level, w.max_dd, int(w.pause_hours * 12), k["leverage"], 0.5, w.path_mode, w.touch)
    days = m.days[m.day_idx[a]:]
    calendar = pd.date_range(days[0], days[-1], freq="D")
    daily = pd.DataFrame({"equity": eq, "balance": bal, "min_equity": mn}, index=days).dropna(subset=["equity"])
    daily = daily.reindex(calendar).ffill()  # weekends: no change
    baskets = pd.DataFrame(log, columns=["sym", "dir", "open", "close", "trades", "pnl", "reason", "worst", "fees",
                                         "carry", "entry", "exit"])
    if len(baskets):
        stamps = pd.to_datetime(m.ts[a:])
        baskets["symbol"] = [m.symbols[int(s)] for s in baskets["sym"]]
        baskets["opened"] = stamps[baskets["open"].astype(int)]
        baskets["closed"] = stamps[baskets["close"].astype(int)]
        baskets["reason"] = baskets["reason"].astype(int).map(wk.REASONS)
    return {"daily": daily, "baskets": baskets,
            "counters": dict(zip(["fees", "carry", "baskets", "dd_exits", "stop_outs", "ruined"], counters))}


def account_stats(daily: pd.DataFrame, baskets: pd.DataFrame) -> dict:
    eq = daily["equity"]
    peak = eq.cummax()
    row = {
        "total_return": eq.iloc[-1] / eq.iloc[0] - 1,
        "equity_max_dd": float((daily["min_equity"] / peak - 1).min()),
        "balance_max_dd": float((daily["balance"] / daily["balance"].cummax() - 1).min()),
    }
    if len(baskets):
        hold = (baskets["closed"] - baskets["opened"]).dt.total_seconds() / 86400
        years = (daily.index[-1] - daily.index[0]).days / 365.25
        row.update({
            "baskets_per_year": len(baskets) / years,
            "basket_win_rate": float((baskets["pnl"] > 0).mean()),
            "mean_trades": float(baskets["trades"].mean()),
            "share_full_ladder": float((baskets["trades"] == baskets["trades"].max()).mean()),
            "median_hold_days": float(hold.median()),
            "max_hold_days": float(hold.max()),
            "avg_win": float(baskets.loc[baskets["pnl"] > 0, "pnl"].mean()),
            "avg_loss": float(baskets.loc[baskets["pnl"] <= 0, "pnl"].mean()),
            "worst_float": float(baskets["worst"].min()),
        })
    return row


def benchmark(m: Market) -> pd.Series:
    """Equal-weight long of the account's symbols (calendar days, weekends flat)."""
    stamps = pd.to_datetime(m.ts)
    closes = pd.DataFrame({s: pd.Series(m.bc[i], index=stamps).resample("1D").last()
                           for i, s in enumerate(m.symbols)}).ffill()
    return closes.pct_change(fill_method=None).mean(axis=1).dropna()


def evaluate_fx(m: Market, res: dict) -> dict:
    r = res["daily"]["equity"].pct_change().dropna()
    row = metrics(r, benchmark(m))
    row.update(account_stats(res["daily"], res["baskets"]))
    by_year = r.groupby(r.index.year).apply(lambda x: x.mean() / x.std() if x.std() > 0 else 0.0)
    row.update({"years_positive": int((by_year > 0).sum()), "years": len(by_year)})
    row.update(res["counters"])
    return row


def run_crypto(coins: dict[str, Market], w: Waka, signals_by: dict | None = None) -> dict:
    equities, stats, baskets = {}, {}, []
    for sym, m in coins.items():
        res = run(m, w, None if signals_by is None else signals_by[sym])
        eq = res["daily"]["equity"]
        equities[sym] = eq
        stats[sym] = {**account_stats(res["daily"], res["baskets"]), **res["counters"]}
        if len(res["baskets"]):
            baskets.append(res["baskets"])
    return {"equities": equities, "stats": pd.DataFrame(stats).T, "portfolio": rg.portfolio(equities),
            "baskets": pd.concat(baskets) if baskets else pd.DataFrame()}


def evaluate_crypto(res: dict, market: pd.Series) -> dict:
    sleeves = rg.sleeve_stats(res["equities"])
    row = rg.evaluate("x", res["portfolio"], market, sleeves)
    b = res["baskets"]
    row.update({"basket_win_rate": float((b["pnl"] > 0).mean()), "mean_trades": float(b["trades"].mean()),
                "share_full_ladder": float((b["trades"] == b["trades"].max()).mean()),
                "sleeves_dd_exit_or_stopout": int((res["stats"][["dd_exits", "stop_outs"]].sum(axis=1) > 0).sum())})
    return row


def episodes(daily: pd.DataFrame) -> pd.DataFrame:
    """Worst equity drawdown inside each documented live drawdown window and its rank among calendar months."""
    dd = daily["min_equity"] / daily["equity"].cummax() - 1
    monthly = dd.groupby(dd.index.to_period("M")).min().sort_values()
    rows = {}
    for name, (a, b) in EPISODES.items():
        worst = dd.loc[a:b].min()
        rows[name] = {"worst_dd_in_window": worst, "month": str(dd.loc[a:b].idxmin().to_period("M")),
                      "rank_among_months": int((monthly < worst).sum() + 1), "months": len(monthly)}
    return pd.DataFrame(rows).T


# ------------------------------------------------------------------------------------------- variants

def variants(core: Waka, kind: str) -> dict[str, Waka]:
    v = {
        "core (medium 0.5%)": core,
        "low 0.25%": replace(core, deposit_load=0.0025),
        "high 1.5%": replace(core, deposit_load=0.015),
        "max floating DD 30%": replace(core, max_dd=0.30),
        "break even after level 7": replace(core, be_level=7),
        "no martingale (1-1-1)": replace(core, m2=1.0, m35=1.0, m6=1.0),
        "single trade (TP 10, SL 280 pips)": replace(core, max_trades=1, sl_pips=280.0),
        "BB 40": replace(core, bb_window=40),
        "RSI 65/35": replace(core, rsi_high=65.0, rsi_low=35.0),
        "RSI 75/25": replace(core, rsi_high=75.0, rsi_low=25.0),
    }
    if kind == "fx":
        v["smart distance" if not core.smart else "fixed 35 pips"] = replace(core, smart=not core.smart)
    return v


CORES = {"crosses": Waka(), "eurusd": Waka(smart=True), "eurusd_user": Waka(smart=True), "crypto": Waka(smart=True)}


def use_reverse() -> None:
    """Switch every core to the trend-following entry and write results to results/waka_reverse/."""
    global OUT
    OUT = REFERENCE / "results" / "waka_reverse"
    for name, core in CORES.items():
        CORES[name] = replace(core, reverse=True)


def check_eurusd() -> None:
    user = pd.read_csv(EURUSD_USER, sep="\t", header=None, names=["timestamp", "o", "h", "l", "c", "v"],
                       parse_dates=["timestamp"], index_col="timestamp")
    fx = pd.read_csv(FX_DIR / "eurusd_5min.csv", parse_dates=["timestamp"], index_col="timestamp")
    joined = user.join(fx, how="inner")
    diff = (joined["c"] - joined["bid_close"]).abs() / PIP
    print(f"EURUSD5.csv vs FXCM: {len(joined)} common bars of {len(user)}; |close diff| median {diff.median():.2f} "
          f"pips, share < 1 pip {np.mean(diff < 1):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nulls", type=int, default=20)
    ap.add_argument("--markets", nargs="+", default=["crosses", "eurusd", "eurusd_user", "crypto"])
    ap.add_argument("--reverse", action="store_true", help="trend-following entries (results/waka_reverse/)")
    args = ap.parse_args()
    if args.reverse:
        use_reverse()
    OUT.mkdir(parents=True, exist_ok=True)
    check_eurusd()
    print("smart calibration:", smart_calibration())
    rows, nulls, by_year, criteria, eps, daily_out, basket_out = [], [], [], [], [], {}, []
    for name in args.markets:
        core = CORES[name]
        rng = np.random.default_rng(11)
        if name == "crypto":
            raw = {s: rg.load_coin(s) for s in crypto_symbols()}
            coins = {s: crypto_market(c) for s, c in raw.items()}
            market = rg.market_returns(raw)
            for vname, w in variants(core, "crypto").items():
                res = run_crypto(coins, w)
                rows.append({"market": name, "variant": vname, **evaluate_crypto(res, market)})
                print(f"{name:12s} {vname:36s} sharpe {rows[-1]['sharpe']:+.2f} total {rows[-1]['total_return']:+.2f} "
                      f"dd {rows[-1]['max_dd']:+.2f}", flush=True)
                if w is core:
                    port, core_res = res["portfolio"], res
            daily_out[name] = (1 + port).cumprod()
            basket_out.append(core_res["baskets"].assign(market=name))
            by_year.append(port.groupby(port.index.year).apply(lambda r: (1 + r).prod() - 1).rename(name))
            for i in range(args.nulls):
                rand = {s: random_signals(m, signals(m, core), rng, core) for s, m in coins.items()}
                r_rand = run_crypto(coins, core, rand)["portfolio"]
                shuf = {s: crypto_market(rg.shuffled(c, rng)) for s, c in raw.items()}
                r_shuf = run_crypto(shuf, core)["portfolio"]
                nulls.append({"market": name, "run": i, "random_entry": metrics(r_rand)["sharpe"],
                              "shuffled": metrics(r_shuf)["sharpe"]})
                print(f"  null {i}: random {nulls[-1]['random_entry']:+.2f} shuffled {nulls[-1]['shuffled']:+.2f}",
                      flush=True)
            core_row = rows[[r["variant"] for r in rows if r["market"] == name].index("core (medium 0.5%)") +
                            len([r for r in rows if r["market"] != name])]
        else:
            m = load_eurusd_user() if name == "eurusd_user" else load_fx(CROSSES if name == "crosses" else ["EURUSD"],
                                                                          name)
            for vname, w in variants(core, "fx").items():
                res = run(m, w)
                rows.append({"market": name, "variant": vname, **evaluate_fx(m, res)})
                print(f"{name:12s} {vname:36s} sharpe {rows[-1]['sharpe']:+.2f} total {rows[-1]['total_return']:+.2f} "
                      f"eq dd {rows[-1]['equity_max_dd']:+.2f} bal dd {rows[-1]['balance_max_dd']:+.2f} "
                      f"win {rows[-1].get('basket_win_rate', np.nan):.2f}", flush=True)
                if w is core:
                    core_res = res
            daily_out[name] = core_res["daily"]["equity"]
            basket_out.append(core_res["baskets"].assign(market=name))
            eq = core_res["daily"]["equity"]
            r = eq.pct_change().dropna()
            by_year.append(r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1).rename(name))
            if name == "crosses":
                eps.append(episodes(core_res["daily"]).assign(market=name))
            if name != "eurusd_user":
                for i in range(args.nulls):
                    rand = random_signals(m, signals(m, core), rng, core)
                    r_rand = run(m, core, rand)["daily"]["equity"].pct_change().dropna()
                    r_shuf = run(shuffled_fx(m, rng), core)["daily"]["equity"].pct_change().dropna()
                    nulls.append({"market": name, "run": i, "random_entry": metrics(r_rand)["sharpe"],
                                  "shuffled": metrics(r_shuf)["sharpe"]})
                    print(f"  null {i}: random {nulls[-1]['random_entry']:+.2f} "
                          f"shuffled {nulls[-1]['shuffled']:+.2f}", flush=True)
            core_row = next(r for r in rows if r["market"] == name and r["variant"] == "core (medium 0.5%)")
        if name == "eurusd_user":
            continue
        nl = pd.DataFrame([n for n in nulls if n["market"] == name])
        dd = core_row["max_dd"] if name == "crypto" else core_row["equity_max_dd"]
        criteria.append({
            "market": name,
            "1 sharpe >= 0.5 and >= 2/3 years positive": core_row["sharpe"] >= 0.5 and
            core_row["years_positive"] >= 2 / 3 * core_row["years"],
            "2 max equity DD <= 30%": dd >= -0.30,
            "3 beats p95 of random entries and shuffles": core_row["sharpe"] > nl["random_entry"].quantile(0.95) and
            core_row["sharpe"] > nl["shuffled"].quantile(0.95),
            "4 alpha t > 2": core_row["alpha_t_nw"] > 2,
            "sharpe": core_row["sharpe"], "null_random_p95": nl["random_entry"].quantile(0.95),
            "null_shuffle_p95": nl["shuffled"].quantile(0.95),
        })
    pd.DataFrame(rows).to_csv(OUT / "metrics.csv", index=False)
    pd.DataFrame(nulls).to_csv(OUT / "null.csv", index=False)
    pd.concat(by_year, axis=1).to_csv(OUT / "by_year.csv")
    pd.DataFrame(daily_out).to_csv(OUT / "daily_equity.csv")
    pd.concat(basket_out).to_csv(OUT / "baskets.csv", index=False)
    crit = pd.DataFrame(criteria)
    crit.to_csv(OUT / "criteria.csv", index=False)
    if eps:
        pd.concat(eps).to_csv(OUT / "episodes.csv")
        print(pd.concat(eps).to_string())
    print(pd.concat(by_year, axis=1).round(3).to_string())
    print(crit.to_string())


if __name__ == "__main__":
    main()
