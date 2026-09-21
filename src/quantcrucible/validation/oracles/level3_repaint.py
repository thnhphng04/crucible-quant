# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # LEAKY ORACLE, level 3 (07-VALIDATION-LAYER §9) — NEVER deploy.
    # A repainting indicator: a centred 5-bar moving average uses the next 2 bars, so its value
    # at a bar changes once those bars arrive.

    def indicators(self, bars: Bars) -> Features:
        import numpy as np

        centred = np.convolve(bars.close, np.ones(5) / 5, mode="same")
        return {"close": bars.close, "centred": centred, "atr": ind.atr(bars, 14)}

    def signal(self, x: FeatureView) -> Signal:
        if x["atr"] > 0 and x["centred"] > x["close"]:
            return Signal("long", 1.0, 2 * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
