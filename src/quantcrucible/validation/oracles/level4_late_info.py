# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # LEAKY ORACLE, level 4 (07-VALIDATION-LAYER §9, O8) — NEVER deploy.
    # A point-in-time error: a synthetic "report" about period t is published DELAY bars later
    # (here: the realised move to t + DELAY), but it is joined at its event date t instead of its
    # publication date.

    def indicators(self, bars: Bars) -> Features:
        import numpy as np

        delay = 3
        report = np.full(len(bars), np.nan)
        report[:-delay] = bars.close[delay:]
        return {"close": bars.close, "report": report, "atr": ind.atr(bars, 14)}

    def signal(self, x: FeatureView) -> Signal:
        if x["atr"] > 0 and x["report"] > x["close"]:
            return Signal("long", 1.0, 2 * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
