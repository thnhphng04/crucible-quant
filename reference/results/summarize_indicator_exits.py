"""Summarise the indicator-exit walk-forward and apply the pre-registered acceptance criteria.

Usage: python summarize_indicator_exits.py <dir with wf_ind_exit_* folders> [<out csv>]
Logics: long RSI (= regime grid with the trend filter off), RSI+regime, RSI+volume.
Baseline: no take profit, max_holding_bars 288. An exit is accepted when, versus the baseline,
(a) whole-grid test median >= baseline in >= 2 of 3 logics, (b) top 25% >= baseline in >= 2 of 3,
(c) positive folds of the top 25% >= baseline in >= 2 of 3; (d) drawdown is checked separately.
"""

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(sys.argv[1])
RULES = {0: "none", 1: "rsi", 2: "donchian", 3: "chandelier", 4: "pctb"}
rows = []
for folder in sorted(p for p in ROOT.glob("wf_ind_exit_*") if p.is_dir()):
    per_fold = pd.read_csv(folder / "per_fold.csv")
    for strategy, group in per_fold.groupby("strategy"):
        match = re.match(r"(.*?)( \(filter off\))? \[(.*)\]$", strategy)
        if not match:
            continue
        base, off, labels = match.groups()
        fields = dict(part.split("=") for part in labels.split(", "))
        logic = "long RSI" if off and "regime" in base else base + (" (off)" if off else "")
        rule = RULES[int(float(fields["exit_rule"]))]
        tp = float(fields["tp_sl_ratio"])
        if rule == "none":
            exit_name = "TP 1.1" if tp > 0 else "no TP"
        else:
            exit_name = f"{rule} {float(fields['exit_param']):g}" if "exit_param" in fields else f"{rule} (param chosen on train)"
        rows.append({
            "logic": logic, "exit": exit_name, "hold": int(float(fields["max_holding_bars"])),
            "test_median": group["test_median"].mean(),
            "top_25pct": group["top_25pct"].mean(),
            "top_k": group["top_k"].mean(),
            "top25_pos": int((group["top_25pct"] > 0).sum()),
            "top_k_pos": int((group["top_k"] > 0).sum()),
            "top_k_worst": group["top_k"].min(),
            "spearman": group["spearman"].mean(),
            "top_k_trades": group["top_k_test_trades"].mean(),
        })
table = pd.DataFrame(rows)
table = table[table["logic"].isin(["long RSI", "long RSI+regime", "long RSI+volume"])]
table = table.drop_duplicates(["logic", "exit", "hold"])

baseline = table[(table["exit"] == "no TP") & (table["hold"] == 288)].set_index("logic")
verdicts = []
for (exit_name, hold), group in table.groupby(["exit", "hold"]):
    group = group.set_index("logic")
    common = group.index.intersection(baseline.index)
    a = int((group.loc[common, "test_median"] >= baseline.loc[common, "test_median"]).sum())
    b = int((group.loc[common, "top_25pct"] >= baseline.loc[common, "top_25pct"]).sum())
    c = int((group.loc[common, "top25_pos"] >= baseline.loc[common, "top25_pos"]).sum())
    verdicts.append({"exit": exit_name, "hold": hold,
                     "median>=base": f"{a}/{len(common)}", "top25>=base": f"{b}/{len(common)}",
                     "top25 pos>=base": f"{c}/{len(common)}",
                     "passes a-c": a >= 2 and b >= 2 and c >= 2,
                     "mean test_median": group["test_median"].mean(),
                     "mean top_25pct": group["top_25pct"].mean(),
                     "mean top_k": group["top_k"].mean()})
verdicts = pd.DataFrame(verdicts).sort_values(["passes a-c", "mean top_25pct"], ascending=False)

pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 500)
for metric in ("test_median", "top_25pct", "top_k", "top25_pos", "top_k_worst"):
    pivot = table.pivot_table(index=["exit", "hold"], columns="logic", values=metric)
    print(f"\n=== {metric} (test R/month, mean over 9 folds)" if "pos" not in metric else f"\n=== {metric} (folds > 0 of 9)")
    print(pivot.round(2).to_string())
print("\n=== Acceptance vs baseline (no TP, hold 288)")
print(verdicts.round(3).to_string(index=False))
if len(sys.argv) > 2:
    table.to_csv(sys.argv[2], index=False)
