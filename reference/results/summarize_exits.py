"""Compact view of the asymmetric-exit walk-forward: per logic, metrics by exit variant."""

import re
import sys
from pathlib import Path

import pandas as pd

SP = Path(sys.argv[1])
pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 400)
frames = []
for folder in ("wf_exit_engine", "wf_exit_legacy"):
    s = pd.read_csv(SP / folder / "summary.csv")
    per_fold = pd.read_csv(SP / folder / "per_fold.csv")
    worst = per_fold.groupby("strategy")["top_k"].min().rename("top_k_worst")
    s = s.join(worst, on="strategy")
    frames.append(s)
s = pd.concat(frames, ignore_index=True)

def parse(name: str):
    match = re.match(r"(.*?)( \(filter off\))?( \[(.*)\])?$", name)
    logic = match.group(1) + (" (off)" if match.group(2) else "")
    exit_ = match.group(4) or "ALL exits"
    exit_ = exit_.replace("tp_sl_ratio=", "tp ").replace("trail_r=", "trail ")
    return logic, exit_

s[["logic", "exit"]] = s["strategy"].apply(lambda n: pd.Series(parse(n)))
for metric in ("test_median", "top_k", "top_25pct"):
    table = s.pivot(index="exit", columns="logic", values=metric)
    order = ["ALL exits"] + sorted(i for i in table.index if i != "ALL exits")
    print(f"\n=== {metric} (test R/month, mean of 9 folds)")
    print(table.loc[order].round(2).to_string())
print("\n=== top_k positive folds")
table = s.pivot(index="exit", columns="logic", values="top_k_pos")
print(table.loc[["ALL exits"] + sorted(i for i in table.index if i != "ALL exits")].to_string())
print("\n=== top_k worst fold")
table = s.pivot(index="exit", columns="logic", values="top_k_worst")
print(table.loc[["ALL exits"] + sorted(i for i in table.index if i != "ALL exits")].round(2).to_string())
