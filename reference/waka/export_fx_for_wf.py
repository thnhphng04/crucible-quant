"""Write the FXCM 5min forex bars in the walk-forward harness's format (reference/data_fx_wf/<pair>_futures_5min.csv:
timestamp, open, high, low, close, volume) using mid prices; costs are charged by the harness's --entry-fee.

    uv run python reference/waka/export_fx_for_wf.py --pairs AUDCAD AUDNZD NZDCAD EURUSD EURGBP EURCHF AUDCHF NZDCHF CADCHF
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REFERENCE = Path(__file__).resolve().parents[1]
SRC = REFERENCE / "data_fx"
DST = REFERENCE / "data_fx_wf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True)
    args = ap.parse_args()
    DST.mkdir(exist_ok=True)
    for pair in args.pairs:
        f = pd.read_csv(SRC / f"{pair.lower()}_5min.csv", parse_dates=["timestamp"])
        out = pd.DataFrame({"timestamp": f["timestamp"]})
        for k in ("open", "high", "low", "close"):
            out[k] = (f[f"bid_{k}"] + f[f"ask_{k}"]) / 2
        out["volume"] = f["minutes"]
        out.to_csv(DST / f"{pair.lower()}_futures_5min.csv", index=False, float_format="%.6f")
        print(f"{pair}: {len(out)} bars {out['timestamp'].iloc[0]} -> {out['timestamp'].iloc[-1]}", flush=True)


if __name__ == "__main__":
    main()
