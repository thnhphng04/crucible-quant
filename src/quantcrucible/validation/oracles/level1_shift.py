# ═══ FIXED REGION — only the evolvable block may change; this hash is locked per campaign ═══
# Crucible Quant strategy template (Architecture §3.3.1). Signals only — sizing is not here.
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.strategy.registry import ind


class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # LEAKY ORACLE, level 1 (07-VALIDATION-LAYER §9) — NEVER deploy.
    # Blatant look-ahead: tomorrow's close, via .shift(-1).

    def indicators(self, bars: Bars) -> Features:
        import pandas as pd

        return {
            "close": bars.close,
            "future": pd.Series(bars.close).shift(-1).to_numpy(),
            "atr": ind.atr(bars, 14),
        }

    def signal(self, x: FeatureView) -> Signal:
        if x["atr"] > 0 and x["future"] > x["close"]:
            return Signal("long", 1.0, 2 * x["atr"])
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
