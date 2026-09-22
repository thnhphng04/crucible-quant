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
        # v2: trade only on finite, positive ATR and stop. With n < 14 the RSI is ready before
        # ATR(14); v1 then went long with stop_distance = NaN (calibration, 2026-09-22).
        # `v - v == 0` is False for NaN and ±inf: the finiteness test the template allows.
        atr = x["atr"] + 0
        stop = self.p.k_atr * atr
        ready = atr > 0 and atr - atr == 0 and stop > 0 and stop - stop == 0
        if ready and x["r"] > self.p.level:
            return Signal("long", 1.0, stop)
        return Signal("flat", 0.0, 0.0)

    # ═══ EVOLVE-BLOCK-END ═══
