# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # TUNABLE: n = 120, bounds=(40, 250)
    # TUNABLE: k_atr = 2.5, bounds=(1.0, 4.0)

    def indicators(self, bars: Bars) -> Features:
        return {
            "c": bars.close,
            "m": ind.sma(bars.close, self.p.n),
            "atr": ind.atr(bars, 14),
        }

    def signal(self, x: FeatureView) -> Signal:
        if x["c"] > x["m"]:
            return Signal("long", 1.0, self.p.k_atr * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
