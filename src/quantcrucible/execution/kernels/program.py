"""Compile a genome and its configuration set into bounded numeric tables (P3-37, ADR-0038).

The kernels never see a genome object, only the :class:`Program` built here: indicator instances
deduplicated across configurations — the reference grid's trick — each configuration's feature
slots bound to those instances, the entry clauses as opcodes, and every TUNABLE as a column of
``pvals`` in the order its render names it. Compiling is total (INV-107): anything outside the
grammar, or a value the CPU oracle would refuse (a float period, say), raises
:class:`ProgramError` and the job takes another engine. Pure numpy — no numba here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from quantcrucible.core.strategy.genome import (
    Bandwidth,
    Breakout,
    Close,
    Combine,
    Compare,
    Cross,
    CrossLevel,
    Distance,
    Genome,
    Indicator,
    Param,
    Series,
    Slope,
    Threshold,
    VolRatio,
    named_params,
)

IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]

# ── limits: beyond them a job is refused rather than computed ───────────────────────────────
MAX_PERIOD = 480  # the kernels' pairwise sum is unrolled for windows up to this length
MAX_LOOKBACK = 10_000
MAX_CONFIGS = 4_096
MAX_CLAUSES = 8
ATR_PERIOD = 14  # the guard and an ATR Distance read ind.atr(bars, 14), unless one ATR (ADR-0047)
BAND_PERIOD = 8  # and a Bollinger stop without its n_stop gene ind.boll_*(bars.close, 8)

# ── opcodes ─────────────────────────────────────────────────────────────────────────────────
OP_CLOSE, OP_SMA, OP_EMA, OP_RSI, OP_ZSCORE = 0, 1, 2, 3, 4
OP_RMAX, OP_RMIN, OP_BOLL_UPPER, OP_BOLL_LOWER, OP_ATR = 5, 6, 7, 8, 9
OP_SPREAD = 10  # spread_stdev(a, b, n): its operands are in ``inst_aux`` (ADR-0045)
OP_BANDWIDTH = 11  # bandwidth(close, n), the band width per √bar (ADR-0047)
# atr_ratio(bars, n, slow) and band_ratio(close, n, slow): ``slow`` is ``inst_aux[i, 0]``
OP_ATR_RATIO, OP_BAND_RATIO = 12, 13
SERIES_OPS = {
    "sma": OP_SMA, "ema": OP_EMA, "rsi": OP_RSI, "zscore": OP_ZSCORE,
    "rolling_max": OP_RMAX, "rolling_min": OP_RMIN,
}  # fmt: skip
CL_COMPARE, CL_CROSS, CL_THRESHOLD, CL_CROSSLEVEL = 0, 1, 2, 3
CL_DISTANCE, CL_BREAKOUT, CL_SLOPE = 4, 5, 6
CMP_GT, CMP_LT = 0, 1
COMBINE_SINGLE, COMBINE_AND, COMBINE_OR = 0, 1, 2
TP_NONE, TP_RATIO, TP_ATR = 0, 1, 2
NO_SLOT = -1


class ProgramError(ValueError):
    """The genome or a configuration is outside what the kernels compute exactly."""


@dataclass(frozen=True, slots=True)
class Program:
    """One job: one genome, ``n_configs`` parameter sets, one lookback.

    ``clauses`` rows are ``(kind, slot_a, slot_b, param_col, flag, den_slot)``: ``flag`` is
    ``CMP_*`` for a comparison and 1/0 for up/down; ``param_col`` indexes ``pvals``; ``den_slot``
    is a ``Distance``'s yardstick — ATR(14) or its ``spread_stdev`` (``NO_SLOT`` if unused).
    ``inst_aux`` rows are ``(a_op, a_period, b_op, b_period)`` for an ``OP_SPREAD`` instance,
    ``(slow, 0, 0, 0)`` for a volatility ratio's (ADR-0047), zeros otherwise. ``atr_slot`` is
    ATR(14), or ATR(n_stop) — then also ``stop_slot`` — when the stop's ATR is the only one.
    """

    names: tuple[str, ...]
    pvals: FloatArray  # [configs, params]
    inst_op: IntArray  # [instances]
    inst_period: IntArray  # [instances]
    inst_aux: IntArray  # [instances, 4]
    slot_inst: IntArray  # [configs, slots]
    clauses: IntArray  # [clauses, 6]
    combine: int
    close_slot: int
    atr_slot: int
    band_slot: int  # NO_SLOT unless the stop is Bollinger
    stop_slot: int  # the ATR an ATR stop multiplies: ``atr_slot``, or ATR(n_stop) (ADR-0041)
    stop_col: int
    tp_mode: int
    tp_ratio: float
    tp_col: int
    direction: int  # +1 long, -1 short
    lookback: int

    @property
    def n_configs(self) -> int:
        return int(self.pvals.shape[0])


def compile_program(
    genome: Genome,
    direction: str,
    tp_sl_ratio: float | None,
    configs: Sequence[Mapping[str, Any]],
    lookback: int,
) -> Program:
    try:
        return _compile(genome, direction, tp_sl_ratio, configs, lookback)
    except ProgramError:
        raise
    except Exception as exc:  # untrusted shapes: any surprise is a refusal (INV-107)
        raise ProgramError(f"{type(exc).__name__}: {exc}") from exc


def _is_number(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _compile(
    genome: Genome,
    direction: str,
    tp_sl_ratio: float | None,
    configs: Sequence[Mapping[str, Any]],
    lookback: int,
) -> Program:
    if not isinstance(genome, Genome):
        raise ProgramError("not a genome")
    if direction not in ("long", "short"):
        raise ProgramError(f"direction must be long or short, got {direction!r}")
    if tp_sl_ratio is not None and not (
        _is_number(tp_sl_ratio) and math.isfinite(tp_sl_ratio) and tp_sl_ratio > 0
    ):
        raise ProgramError("tp_sl_ratio must be finite and > 0")
    if not (isinstance(lookback, int) and 2 <= lookback <= MAX_LOOKBACK):
        raise ProgramError(f"lookback must be an int in [2, {MAX_LOOKBACK}]")
    if not 1 <= len(configs) <= MAX_CONFIGS:
        raise ProgramError(f"need 1 to {MAX_CONFIGS} configurations, got {len(configs)}")

    named = named_params(genome)
    names = tuple(n for n, _ in named)
    column = {id(p): i for i, (_, p) in enumerate(named)}
    periods = {n for n, p in named if p.kind == "period"}
    pvals = np.empty((len(configs), len(names)), dtype=np.float64)
    for m, cfg in enumerate(configs):
        if set(cfg) != set(names):
            raise ProgramError(f"configuration {m} does not bind exactly {list(names)}")
        for j, name in enumerate(names):
            v = cfg[name]
            if name in periods:
                if not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_PERIOD):
                    raise ProgramError(f"{name} must be an int period in [1, {MAX_PERIOD}]")
            elif not (_is_number(v) and math.isfinite(v)):
                raise ProgramError(f"{name} must be a finite number")
            pvals[m, j] = float(v)

    period = genome.stop_period
    if period is not None and not isinstance(period, Param):
        raise ProgramError("stop period must be a parameter")
    b = _Builder(len(configs), pvals, column)
    close_slot = b.slot(("close",), OP_CLOSE, None)
    # the render's only ATR is the stop's ATR(n_stop) when it has one (ADR-0047), else ATR(14)
    atr_slot = b.slot(("atr",), OP_ATR, period if genome.one_atr else ATR_PERIOD)
    b.atr_slot = atr_slot
    clauses = genome.clauses()
    if not 1 <= len(clauses) <= MAX_CLAUSES:
        raise ProgramError(f"need 1 to {MAX_CLAUSES} clauses")
    rows = [b.clause(c) for c in clauses]
    if isinstance(genome.entry, Combine):
        if genome.entry.op not in ("and", "or"):
            raise ProgramError("combine must be and/or")
        combine = COMBINE_AND if genome.entry.op == "and" else COMBINE_OR
    else:
        combine = COMBINE_SINGLE
    stop_slot = atr_slot
    if genome.stop_kind == "bollinger":
        band_op = OP_BOLL_LOWER if direction == "long" else OP_BOLL_UPPER
        band_slot = b.slot(("band", band_op), band_op, BAND_PERIOD if period is None else period)
    elif genome.stop_kind == "atr":
        band_slot = NO_SLOT
        if period is not None and not genome.one_atr:
            stop_slot = b.slot(("atr_stop",), OP_ATR, period)
    else:
        raise ProgramError("stop kind must be atr or bollinger")
    stop_col = b.col(genome.stop)
    if tp_sl_ratio is not None:  # the campaign ratio wins over a k_tp gene (render order)
        tp_mode, tp_ratio, tp_col = TP_RATIO, float(tp_sl_ratio), NO_SLOT
    elif genome.take_profit is not None:
        tp_mode, tp_ratio, tp_col = TP_ATR, 0.0, b.col(genome.take_profit)
    else:
        tp_mode, tp_ratio, tp_col = TP_NONE, 0.0, NO_SLOT
    inst_op, inst_period, inst_aux, slot_inst = b.tables()
    return Program(
        names=names, pvals=pvals, inst_op=inst_op, inst_period=inst_period, inst_aux=inst_aux,
        slot_inst=slot_inst, clauses=np.asarray(rows, dtype=np.int64).reshape(-1, 6),
        combine=combine, close_slot=close_slot, atr_slot=atr_slot, band_slot=band_slot,
        stop_slot=stop_slot,
        stop_col=stop_col, tp_mode=tp_mode, tp_ratio=tp_ratio, tp_col=tp_col,
        direction=1 if direction == "long" else -1, lookback=lookback,
    )  # fmt: skip


class _Builder:
    def __init__(self, n_configs: int, pvals: FloatArray, column: Mapping[int, int]) -> None:
        self.n = n_configs
        self.pvals = pvals
        self.column = column
        self.slots: dict[tuple[Any, ...], int] = {}
        self.slot_cols: list[list[int]] = []  # per slot: the instance of each configuration
        # (op, period, a_op, a_period, b_op, b_period): the operands are zeros but for OP_SPREAD
        self.instances: dict[tuple[int, ...], int] = {}
        self.atr_slot = NO_SLOT

    def col(self, p: object) -> int:
        if not isinstance(p, Param) or id(p) not in self.column:
            raise ProgramError("parameter not declared by the render")
        return self.column[id(p)]

    def _instance(self, op: int, period: int, aux: tuple[int, int, int, int] = (0, 0, 0, 0)) -> int:
        return self.instances.setdefault((op, period, *aux), len(self.instances))

    def _period(self, p: Param | None, m: int) -> int:
        return 0 if p is None else int(self.pvals[m, self.col(p)])

    def spread(self, c: Distance) -> int:
        """The slot of ``spread_stdev(left, right, n)``, n the period of right (of left when
        right is the close) — the render's yardstick, per configuration (ADR-0045)."""
        operands: list[tuple[int, Param | None]] = []
        for s in (c.left, c.right):
            if isinstance(s, Close):
                operands.append((OP_CLOSE, None))
            elif isinstance(s, Indicator) and s.op in ("sma", "ema"):
                operands.append((SERIES_OPS[s.op], s.period))
            else:
                raise ProgramError(f"unsupported spread operand {s!r}")
        (a_op, a_p), (b_op, b_p) = operands
        n = b_p if b_p is not None else a_p
        if n is None:
            raise ProgramError("a spread needs an indicator")
        key = ("spread", a_op, id(a_p), b_op, id(b_p))
        if key in self.slots:
            return self.slots[key]
        per_config = []
        for m in range(self.n):
            aux = (a_op, self._period(a_p, m), b_op, self._period(b_p, m))
            per_config.append(self._instance(OP_SPREAD, self._period(n, m), aux))
        self.slots[key] = len(self.slot_cols)
        self.slot_cols.append(per_config)
        return self.slots[key]

    def ratio(self, c: VolRatio | Bandwidth) -> int:
        """The slot of ``atr_ratio`` / ``band_ratio`` (fast, slow), per configuration."""
        op = OP_ATR_RATIO if isinstance(c, VolRatio) else OP_BAND_RATIO
        key = ("ratio", op, id(c.fast), id(c.slow))
        if key in self.slots:
            return self.slots[key]
        per_config = []
        for m in range(self.n):
            slow = self._period(c.slow, m)
            per_config.append(self._instance(op, self._period(c.fast, m), (slow, 0, 0, 0)))
        self.slots[key] = len(self.slot_cols)
        self.slot_cols.append(per_config)
        return self.slots[key]

    def slot(self, key: tuple[Any, ...], op: int, period: int | Param | None) -> int:
        """A feature slot; ``period`` is a constant, a TUNABLE (varies per config) or none."""
        if key in self.slots:
            return self.slots[key]
        if isinstance(period, Param):
            j = self.col(period)
            per_config = [self._instance(op, int(self.pvals[m, j])) for m in range(self.n)]
        else:
            inst = self._instance(op, 0 if period is None else period)
            per_config = [inst] * self.n
        self.slots[key] = len(self.slot_cols)
        self.slot_cols.append(per_config)
        return self.slots[key]

    def series(self, s: Series) -> int:
        if isinstance(s, Close):
            return self.slot(("close",), OP_CLOSE, None)
        if isinstance(s, Indicator) and s.op in SERIES_OPS:
            return self.slot(("ind", s.op, id(s.period)), SERIES_OPS[s.op], s.period)
        raise ProgramError(f"unsupported series {s!r}")

    def clause(self, c: object) -> list[int]:
        if isinstance(c, Compare):
            a, b = self.series(c.left), self.series(c.right)
            return [CL_COMPARE, a, b, NO_SLOT, _cmp(c.op), NO_SLOT]
        if isinstance(c, Cross):
            a, b = self.series(c.left), self.series(c.right)
            return [CL_CROSS, a, b, NO_SLOT, int(c.up), NO_SLOT]
        if isinstance(c, Threshold):
            return [CL_THRESHOLD, self.series(c.osc), NO_SLOT, self.col(c.level), _cmp(c.op),
                    NO_SLOT]  # fmt: skip
        if isinstance(c, CrossLevel):
            return [CL_CROSSLEVEL, self.series(c.osc), NO_SLOT, self.col(c.level), int(c.up),
                    NO_SLOT]  # fmt: skip
        if isinstance(c, Distance):
            a, b = self.series(c.left), self.series(c.right)
            if c.scale == "atr":
                den = self.atr_slot
            elif c.scale == "spread":
                den = self.spread(c)
            else:
                raise ProgramError(f"unsupported Distance scale {c.scale!r}")
            return [CL_DISTANCE, a, b, self.col(c.k), _cmp(c.op), den]
        if isinstance(c, Breakout):
            op = OP_RMAX if c.up else OP_RMIN
            name = "rolling_max" if c.up else "rolling_min"
            level = self.slot(("ind", name, id(c.period)), op, c.period)
            close = self.slot(("close",), OP_CLOSE, None)
            return [CL_BREAKOUT, close, level, NO_SLOT, int(c.up), NO_SLOT]
        if isinstance(c, Slope):
            return [CL_SLOPE, self.series(c.series), NO_SLOT, NO_SLOT, int(c.up), NO_SLOT]
        if isinstance(c, VolRatio | Bandwidth):  # one feature against k, as a Threshold
            return [CL_THRESHOLD, self.ratio(c), NO_SLOT, self.col(c.k), _cmp(c.op), NO_SLOT]
        raise ProgramError(f"unsupported clause {c!r}")

    def tables(self) -> tuple[IntArray, IntArray, IntArray, IntArray]:
        by_index = sorted(self.instances.items(), key=lambda kv: kv[1])
        inst_op = np.asarray([key[0] for key, _ in by_index], dtype=np.int64)
        inst_period = np.asarray([key[1] for key, _ in by_index], dtype=np.int64)
        inst_aux = np.asarray([key[2:] for key, _ in by_index], dtype=np.int64).reshape(-1, 4)
        slot_inst = np.asarray(self.slot_cols, dtype=np.int64).T.reshape(self.n, -1)
        return (
            inst_op,
            inst_period,
            np.ascontiguousarray(inst_aux),
            np.ascontiguousarray(slot_inst),
        )


def _cmp(op: str) -> int:
    if op == ">":
        return CMP_GT
    if op == "<":
        return CMP_LT
    raise ProgramError(f"comparison must be > or <, got {op!r}")
