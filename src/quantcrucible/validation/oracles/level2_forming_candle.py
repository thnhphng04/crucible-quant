# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # LEAKY ORACLE, level 2 (07-VALIDATION-LAYER §9) — NEVER deploy.
    # The close of a still-forming 2-day candle: every bar of a 2-day group gets the group's final
    # close, so the first bar of each group already "knows" the next bar's close.

    def indicators(self, bars: Bars) -> Features:
        import numpy as np

        group = bars.ts.astype("datetime64[D]").astype(np.int64) // 2
        last = np.flatnonzero(np.r_[group[1:] != group[:-1], True])
        end = last[np.searchsorted(last, np.arange(len(group)))]
        return {"close": bars.close, "htf": bars.close[end], "atr": ind.atr(bars, 14)}

    def signal(self, x: FeatureView) -> Signal:
        if x["atr"] > 0 and x["htf"] > x["close"]:
            return Signal("long", 1.0, 2 * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
