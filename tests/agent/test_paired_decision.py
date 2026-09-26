"""Comparison protocol v5: a paired within-scope difference (P3-13, ADR-0033) — INV-98.

Under one scope the old rule was fine: efficiency is one number per seed, and
`mean_gp - mean_random > max(sd)` asks whether the difference exceeds the noise.

With ten scopes it stops being fine, and in a way that looks like it is working. Pooling all
thirty numbers and taking their sd admits *between-instrument* variance — BTC and XRP have
different gate-④ pass rates for reasons that have nothing to do with which engine proposed the
strategies — so the spread balloons and the rule essentially never fires. A machine that always
returns `tie` is worse than no test, because it reads as evidence of no difference.

Pairing removes exactly that variance. For each (instrument, direction, seed) both arms saw the
same instrument, the same window, the same data and the same quota, so their difference is
attributable to the engine. The negative control below documents why.
"""

from __future__ import annotations

from typing import Any

import pytest

from quantcrucible.agent.compare import PROTOCOL, decide, paired_differences

Row = dict[str, Any]


def eff(rows: list[tuple[str, str, int, float]]) -> list[Row]:
    """(instrument, direction, seed, efficiency) rows for one arm."""
    return [
        {"instrument": i, "direction": d, "seed": s, "trial_efficiency": e} for i, d, s, e in rows
    ]


def arms(gp: list[float], rnd: list[float]) -> dict[str, list[Row]]:
    scopes = [("BTCUSDT", "long"), ("BTCUSDT", "short"), ("ETHUSDT", "long")]
    rows = [(i, d, s, 0.0) for i, d in scopes for s in range(3)]
    return {
        "gp": eff([(i, d, s, v) for (i, d, s, _), v in zip(rows, gp, strict=True)]),
        "random": eff([(i, d, s, v) for (i, d, s, _), v in zip(rows, rnd, strict=True)]),
    }


def test_the_difference_is_paired_within_scope() -> None:
    per_scope = arms(gp=[10.0] * 9, rnd=[6.0] * 9)
    diffs = paired_differences(per_scope)
    assert len(diffs) == 9
    assert diffs == [pytest.approx(4.0)] * 9


def test_a_missing_counterpart_is_not_paired() -> None:
    """An arm that never ran a scope cannot be differenced against it."""
    per_scope = arms(gp=[10.0] * 9, rnd=[6.0] * 9)
    per_scope["random"] = per_scope["random"][:-1]
    assert len(paired_differences(per_scope)) == 8


def test_pooling_across_scopes_would_inflate_the_spread() -> None:
    """The negative control, kept because it documents the choice rather than the code.

    Three scopes with very different base rates but a constant +4 engine effect. Pooled, the sd
    is dominated by the base-rate spread and swallows the effect; paired, the effect is all that
    is left.
    """
    base = [2.0, 2.0, 2.0, 40.0, 40.0, 40.0, 80.0, 80.0, 80.0]
    per_scope = arms(gp=[b + 4.0 for b in base], rnd=base)
    diffs = paired_differences(per_scope)
    import statistics

    assert statistics.mean(diffs) == pytest.approx(4.0)
    assert statistics.stdev(diffs) == pytest.approx(0.0, abs=1e-12)
    # pooled, the same data gives a spread of ~32, which no real effect would clear
    pooled = statistics.stdev([float(r["trial_efficiency"]) for r in per_scope["gp"]])
    assert pooled > 30.0


def test_a_consistent_edge_beats_the_noise() -> None:
    per_scope = arms(gp=[10.0, 11.0, 9.0, 20.0, 21.0, 19.0, 5.0, 6.0, 4.0],
                     rnd=[6.0, 7.0, 5.0, 16.0, 17.0, 15.0, 1.0, 2.0, 0.0])  # fmt: skip
    assert decide(per_scope)["outcome"] == "gp_beats_random"


def test_an_inconsistent_edge_is_a_tie() -> None:
    per_scope = arms(gp=[20.0, 2.0, 20.0, 2.0, 20.0, 2.0, 20.0, 2.0, 20.0],
                     rnd=[2.0, 20.0, 2.0, 20.0, 2.0, 20.0, 2.0, 20.0, 2.0])  # fmt: skip
    assert decide(per_scope)["outcome"] == "tie"


def test_an_incomplete_campaign_decides_nothing() -> None:
    per_scope = arms(gp=[10.0] * 9, rnd=[6.0] * 9)
    out = decide(per_scope, unfinished=["BTCUSDT-long-gp-s0"])
    assert out["outcome"] == "incomplete"


def test_neither_arm_meaningful_is_not_a_win() -> None:
    per_scope = arms(gp=[0.4] * 9, rnd=[0.1] * 9)
    assert decide(per_scope)["outcome"] == "neither_meaningful"


def test_an_individual_scope_carries_no_win_or_loss() -> None:
    """Decision 3: the first campaign is exploratory per scope. Hashing that as a protocol
    string means a future campaign wanting per-scope verdicts must bump the version rather
    than reinterpret data that was never gathered for it."""
    assert PROTOCOL["scope_verdicts"].startswith("none")
    per_scope = arms(gp=[10.0] * 9, rnd=[6.0] * 9)
    out = decide(per_scope)
    assert "per_scope" not in out
    assert set(out) == {"mean_difference", "spread", "pairs", "outcome", "action"}


def test_the_protocol_names_the_wider_unit() -> None:
    assert PROTOCOL["version"] == 5
    assert "instrument" in PROTOCOL["unit"] and "direction" in PROTOCOL["unit"]


def test_the_protocol_fixes_the_joint_account_replay_order() -> None:
    """The order a bar is replayed in decides the measured result — funding before liquidation
    changes who survives, and the equity snapshot before admission is what makes every entry on
    a bar size from one number. `joint_account` said that order was hashed into the protocol
    while it was only a comment in the source, which is the same hole ADR-0028 closed for the
    ranking: a measurement rule that can drift silently."""
    replay = PROTOCOL["replay"]
    for earlier, later in (
        ("funding", "liquidation"),
        ("liquidation", "equity snapshot"),
        ("equity snapshot", "admission"),
    ):
        assert replay.index(earlier) < replay.index(later), (earlier, later)
    assert "next bar's open" in replay  # ADR-0003 survives the rewrite
    assert "never from" in replay  # sizes from signals, not from a standalone run's quantities
    assert "isolated" in replay
    assert "never summed" in replay  # several settlements in one bar stay separate
