"""A genome plus a configuration set compiles to bounded numeric tables (P3-37, INV-107)."""

from __future__ import annotations

import contextlib
import dataclasses
from typing import Any

import numpy as np
import pytest

from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.core.strategy.genome import (
    Breakout,
    Close,
    Combine,
    Cross,
    Distance,
    Genome,
    Indicator,
    Param,
    Slope,
    Threshold,
    named_params,
)
from quantcrucible.execution.kernels import program as P
from quantcrucible.execution.kernels.program import ProgramError, compile_program


def _n(v: int) -> Param:
    return Param("period", 2, 300, v)


K_STOP = Param("mult", 0.5, 5.0, 2.0)


def _g1() -> Genome:
    return Genome(
        Combine(
            "and",
            (
                Cross(Indicator("ema", _n(20)), True, Indicator("ema", _n(50))),
                Slope(Indicator("ema", _n(100)), True),
            ),
        ),
        K_STOP,
    )


def _defaults(g: Genome) -> dict[str, float | int]:
    return {name: p.value for name, p in named_params(g)}


def test_indicator_instances_are_shared_across_configurations() -> None:
    g = _g1()
    base = _defaults(g)
    configs = [base, {**base, "n1": 50}, {**base, "n2": 20, "k_stop": 3.0}]
    prog = compile_program(g, "long", 1.1, configs, 400)
    instances = set(zip(prog.inst_op.tolist(), prog.inst_period.tolist(), strict=True))
    assert instances == {
        (P.OP_CLOSE, 0), (P.OP_ATR, 14), (P.OP_EMA, 20), (P.OP_EMA, 50), (P.OP_EMA, 100),
    }  # fmt: skip
    assert len(prog.inst_op) == 5  # ema50 is one instance though two slots use it


def test_columns_and_slots_bind_by_tunable_name() -> None:
    g = _g1()
    base = _defaults(g)
    configs = [base, {**base, "n1": 50, "k_stop": 3.5}]
    prog = compile_program(g, "long", 1.1, configs, 400)
    assert prog.names == ("n1", "n2", "n3", "k_stop")
    np.testing.assert_array_equal(prog.pvals[:, prog.names.index("k_stop")], [2.0, 3.5])
    cross = prog.clauses[0]
    assert cross[0] == P.CL_CROSS and cross[4] == 1
    periods = prog.inst_period[prog.slot_inst[:, cross[1]]]
    np.testing.assert_array_equal(periods, [20, 50])  # n1 moves per configuration
    assert prog.combine == P.COMBINE_AND
    assert prog.stop_col == prog.names.index("k_stop")
    assert (prog.tp_mode, prog.tp_ratio, prog.direction) == (P.TP_RATIO, 1.1, 1)


def test_every_clause_type_encodes() -> None:
    lv = Param("level", -3.0, 3.0, 1.5)
    brk = Param("period", 2, 300, 30)
    g = Genome(
        Combine(
            "or",
            (
                Threshold(
                    Indicator("rsi", Param("period", 2, 100, 14)), ">", Param("level", 50, 90, 60)
                ),
                Distance(Close(), Indicator("sma", _n(40)), "<", lv),
                Breakout(False, brk),
            ),
        ),
        K_STOP,
        take_profit=Param("mult", 0.5, 10.0, 3.0),
        stop_kind="bollinger",
    )
    prog = compile_program(g, "short", None, [_defaults(g)], 400)
    kinds = prog.clauses[:, 0].tolist()
    assert kinds == [P.CL_THRESHOLD, P.CL_DISTANCE, P.CL_BREAKOUT]
    brk_row = prog.clauses[2]
    level_inst = prog.slot_inst[0, brk_row[2]]
    assert (prog.inst_op[level_inst], prog.inst_period[level_inst]) == (P.OP_RMIN, 30)
    band = prog.slot_inst[0, prog.band_slot]
    assert (prog.inst_op[band], prog.inst_period[band]) == (P.OP_BOLL_UPPER, 8)  # short side
    assert (prog.tp_mode, prog.direction) == (P.TP_ATR, -1)
    assert prog.combine == P.COMBINE_OR


def test_the_stop_period_gene_binds_its_own_slot_per_configuration() -> None:
    """ADR-0041: an ATR stop reads ATR(n_stop) while the guard keeps ATR(14); a Bollinger stop's
    band period is n_stop. Both move with the configuration."""
    n_stop = Param("period", 5, 50, 21)
    g = dataclasses.replace(_g1(), stop_period=n_stop)
    base = _defaults(g)
    prog = compile_program(g, "long", 1.1, [base, {**base, "n_stop": 9}], 400)
    assert prog.names == ("n1", "n2", "n3", "k_stop", "n_stop")
    atr = prog.slot_inst[:, prog.atr_slot]
    stop = prog.slot_inst[:, prog.stop_slot]
    assert prog.inst_period[atr].tolist() == [14, 14]
    assert prog.inst_op[stop].tolist() == [P.OP_ATR, P.OP_ATR]
    assert prog.inst_period[stop].tolist() == [21, 9]
    boll = dataclasses.replace(g, stop_kind="bollinger")
    prog = compile_program(boll, "short", 1.1, [base, {**base, "n_stop": 9}], 400)
    band = prog.slot_inst[:, prog.band_slot]
    assert prog.inst_op[band].tolist() == [P.OP_BOLL_UPPER, P.OP_BOLL_UPPER]
    assert prog.inst_period[band].tolist() == [21, 9]
    assert prog.stop_slot == prog.atr_slot  # unused by a Bollinger stop
    fixed = compile_program(_g1(), "long", 1.1, [_defaults(_g1())], 400)
    assert fixed.stop_slot == fixed.atr_slot  # INV-114: no gene, the ATR(14) of before


def test_a_campaign_ratio_overrides_the_genome_take_profit() -> None:
    g = dataclasses.replace(_g1(), take_profit=Param("mult", 0.5, 10.0, 3.0))
    prog = compile_program(g, "long", 1.1, [_defaults(g)], 400)
    assert (prog.tp_mode, prog.tp_ratio) == (P.TP_RATIO, 1.1)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: {**c, "n1": 20.0},  # a float period: the oracle's _check_period refuses it
        lambda c: {**c, "n1": True},
        lambda c: {**c, "n1": 0},
        lambda c: {**c, "n1": P.MAX_PERIOD + 1},
        lambda c: {k: v for k, v in c.items() if k != "n2"},  # missing
        lambda c: {**c, "extra": 1.0},  # unknown
        lambda c: {**c, "k_stop": float("nan")},
        lambda c: {**c, "k_stop": "2"},
    ],
)
def test_a_bad_configuration_is_refused(mutate: Any) -> None:
    g = _g1()
    with pytest.raises(ProgramError):
        compile_program(g, "long", 1.1, [_defaults(g), mutate(_defaults(g))], 400)


@pytest.mark.parametrize(
    ("direction", "ratio", "lookback", "n_configs"),
    [("flat", 1.1, 400, 1), ("long", -1.0, 400, 1), ("long", 1.1, 1, 1),
     ("long", 1.1, 400, 0), ("long", 1.1, 400, P.MAX_CONFIGS + 1)],
)  # fmt: skip
def test_bad_job_settings_are_refused(
    direction: str, ratio: float, lookback: int, n_configs: int
) -> None:
    g = _g1()
    with pytest.raises(ProgramError):
        compile_program(g, direction, ratio, [_defaults(g)] * n_configs, lookback)


def test_an_unknown_operator_is_refused() -> None:
    g = Genome(Slope(Indicator("__import__", _n(5)), True), K_STOP)
    with pytest.raises(ProgramError):
        compile_program(g, "long", 1.1, [_defaults(g)], 400)


def test_compiling_is_total_on_arbitrary_input() -> None:
    """Whatever the genome or configurations, the only exception is ProgramError."""
    rng = np.random.default_rng(0)
    cfg = GrammarConfig(take_profit_probability=0.5, boll_stop_probability=0.5)
    junk: list[Any] = [None, "x", -1, 0.5, float("inf"), 10**9, [], {}, True]
    for _ in range(400):
        g = sample_genome(rng, cfg)
        base = _defaults(g)
        prog = compile_program(g, "long", 1.1, [base], 400)  # a sampled genome always compiles
        assert prog.n_configs == 1
        names = list(base)
        bad = dict(base)
        bad[names[int(rng.integers(len(names)))]] = junk[int(rng.integers(len(junk)))]
        with contextlib.suppress(ProgramError):  # refused or compiled: never anything else
            compile_program(g, "long", 1.1, [bad], 400)
    for thing in junk:
        with pytest.raises(ProgramError):
            compile_program(thing, "long", 1.1, [{}], 400)
