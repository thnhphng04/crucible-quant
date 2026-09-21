# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # TUNABLE: fast = 20, bounds=(5, 60)
    # TUNABLE: slow = 100, bounds=(40, 300)
    # TUNABLE: k_atr = 2.0, bounds=(1.0, 4.0)

    def indicators(self, bars: Bars) -> Features:
        return {
            "f": ind.ema(bars.close, self.p.fast),
            "s": ind.ema(bars.close, self.p.slow),
            "atr": ind.atr(bars, 14),
        }

    def signal(self, x: FeatureView) -> Signal:
        if x["f"] > x["s"]:
            return Signal("long", 1.0, self.p.k_atr * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
