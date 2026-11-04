"""Apply the pre-registered rules of PREREGISTRATION.md to results/condition_lab/baseline_<X>.npz.

Dev coins first (D1-D3); the confirm coins are evaluated only for the conditions that pass on dev (C1). With
--explore the confirm numbers of every condition are printed as well, labelled exploratory.

    uv run python reference/condition_lab/analyze.py --baseline A [--explore]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cond_lab as cl  # noqa: E402

FIRST, LAST = "2021-08", "2026-09"  # months whose closed trades count


def active_months(coins: list[str], first_month: str, n_months: int) -> np.ndarray:
    """coins x months: the coin has data in the month (first month after the listing month to its last month)."""
    files = cl.coin_files()
    base = pd.Period(first_month, "M")
    out = np.zeros((len(coins), n_months), dtype=bool)
    for i, coin in enumerate(coins):
        with open(files[coin], encoding="utf-8") as fh:
            fh.readline()
            first = pd.Timestamp(fh.readline().split(",")[0])
            fh.seek(0, 2)
            fh.seek(max(fh.tell() - 4096, 0))
            last = pd.Timestamp(fh.read().strip().splitlines()[-1].split(",")[0])
        a = (first.to_period("M") - base).n + 1
        b = (last.to_period("M") - base).n
        out[i, max(a, 0): min(b, n_months - 1) + 1] = True
    return out


def portfolio(R: np.ndarray, active: np.ndarray) -> np.ndarray:
    """sets x conditions x months: mean R over the coins active in the month."""
    weights = active / np.maximum(active.sum(axis=0), 1)
    return np.einsum("csm,cm->sm", R.reshape(R.shape[0], -1, R.shape[-1]), weights).reshape(R.shape[1:])


def evaluate(R: np.ndarray, N: np.ndarray, active: np.ndarray, months: pd.PeriodIndex) -> dict[str, np.ndarray]:
    P = portfolio(R, active)  # sets x conds x months
    sharpe = P.mean(axis=2) / np.where(P.std(axis=2) > 0, P.std(axis=2), np.nan) * np.sqrt(12)
    eq = np.cumsum(P, axis=2)
    max_dd = (np.maximum.accumulate(np.maximum(eq, 0), axis=2) - eq).max(axis=2)
    years = months.year.to_numpy()
    uy = np.unique(years)
    rpt = np.stack([R[..., years == y].sum(axis=(0, 3)) / np.maximum(N[..., years == y].sum(axis=(0, 3)), 1)
                    for y in uy], axis=-1)  # sets x conds x years
    trades = N.sum(axis=(0, 3))  # sets x conds
    coin_years = active.sum() / 12
    return {"sharpe": sharpe, "r_yr_coin": R.sum(axis=(0, 3)) / coin_years, "max_dd": max_dd, "rpt": rpt,
            "trades": trades, "trades_yr_coin": trades / coin_years, "years": uy}


def table(ev: dict, names: list[str]) -> pd.DataFrame:
    d_sh = ev["sharpe"] - ev["sharpe"][:, [0]]
    d_rpt = ev["rpt"] - ev["rpt"][:, [0], :]
    keep = ev["trades"] / ev["trades"][:, [0]]
    return pd.DataFrame({
        "dSharpe_med": np.median(d_sh, axis=0),
        "sets_better": (d_sh > 0).sum(axis=0),
        "years_rpt_better": (np.median(d_rpt, axis=0) > 0).sum(axis=1),
        "kept": np.median(keep, axis=0),
        "Sharpe_med": np.median(ev["sharpe"], axis=0),
        "R_yr_coin": np.median(ev["r_yr_coin"], axis=0),
        "maxDD": np.median(ev["max_dd"], axis=0),
        "trades_yr_coin": np.median(ev["trades_yr_coin"], axis=0),
        "R_per_trade": np.median(ev["r_yr_coin"] / np.maximum(ev["trades_yr_coin"], 1e-9), axis=0),
    }, index=names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", choices=list(cl.BASELINES), default="A")
    parser.add_argument("--explore", action="store_true")
    args = parser.parse_args()
    data = np.load(cl.OUT / f"baseline_{args.baseline}.npz")
    R, N = data["R"], data["N"]  # coins x sets x conds x months
    coins, groups, names = list(data["coins"]), data["groups"], list(data["conditions"])
    all_months = pd.period_range(str(data["first_month"]), periods=R.shape[-1], freq="M")
    sel = (all_months >= pd.Period(FIRST, "M")) & (all_months <= pd.Period(LAST, "M"))
    months = all_months[sel]
    active = active_months(coins, str(data["first_month"]), R.shape[-1])[:, sel]
    R, N = R[..., sel], N[..., sel]
    n_years = len(np.unique(months.year))

    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    out = {}
    for group in ("dev", "confirm"):
        g = groups == group
        out[group] = table(evaluate(R[g], N[g], active[g], months), names)
    dev = out["dev"]
    dev["D1"] = (dev.sets_better >= 7) & (dev.dSharpe_med > 0)
    dev["D2"] = dev.years_rpt_better >= 4
    dev["D3"] = dev.kept >= 0.25
    dev.loc["none", ["D1", "D2", "D3"]] = False
    dev["dev_pass"] = dev.D1 & dev.D2 & dev.D3
    print(f"=== baseline {args.baseline}: DEV coins ({int((groups == 'dev').sum())}), {FIRST}..{LAST}, "
          f"{n_years} years; sorted by median dSharpe")
    print(dev.sort_values("dSharpe_med", ascending=False).round(3).to_string())
    passed = list(dev.index[dev.dev_pass])
    print(f"\nPass D1-D3 on dev: {len(passed)} / {len(names) - 1}: {passed}")
    conf = out["confirm"]
    conf["C1"] = (conf.sets_better >= 6) & (conf.dSharpe_med > 0)
    if passed:
        print(f"\n=== CONFIRM coins ({int((groups == 'confirm').sum())}) for the dev passers")
        print(conf.loc[["none", *passed]].round(3).to_string())
        final = [c for c in passed if conf.loc[c, "C1"]]
        print(f"\nPASS (D1-D3 + C1): {final}")
    if args.explore:
        print("\n=== EXPLORATORY: confirm coins, every condition")
        print(conf.sort_values("dSharpe_med", ascending=False).round(3).to_string())
        both = pd.DataFrame({"dev_dSharpe": dev.dSharpe_med, "conf_dSharpe": conf.dSharpe_med})
        print("\nSpearman dev vs confirm dSharpe over conditions:",
              round(both.drop("none").corr(method="spearman").iloc[0, 1], 3))
    pd.concat({"dev": dev, "confirm": conf}).to_csv(cl.OUT / f"baseline_{args.baseline}_summary.csv")


if __name__ == "__main__":
    main()
