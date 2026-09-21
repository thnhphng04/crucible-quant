# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # TUNABLE: n = 30, bounds=(10, 60)
    # TUNABLE: level = 55, bounds=(50, 70)
    # TUNABLE: k_atr = 2.5, bounds=(1.0, 4.0)

    def indicators(self, bars: Bars) -> Features:
        return {
            "r": ind.rsi(bars.close, self.p.n),
            "atr": ind.atr(bars, 14),
        }

    def signal(self, x: FeatureView) -> Signal:
        if x["r"] > self.p.level:
            return Signal("long", 1.0, self.p.k_atr * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
