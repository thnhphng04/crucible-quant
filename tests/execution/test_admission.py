"""Admission: one equity snapshot per timestamp, canonical order, the 10% cap (P3-08, INV-92).

Ten strategies want to enter on the same bar. They share one account, so the order in which they
are considered decides who gets in — and that order must not depend on dict iteration, thread
scheduling or which sandbox finished first.

The rule from the frozen requirements: settle exits, stops, liquidations and funding first,
snapshot equity once, then consider entries in canonical order (instrument ascending, long
before short). Every entry in the batch uses the same E and the same R.

Because q and d both freeze at entry, each open position's risk commitment is a constant in
USDT. That makes the cap arithmetic rather than a separate hand-written breach rule.
"""

from __future__ import annotations

import pytest

from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.execution.admission import (
    EntryRequest,
    admit_batch,
)

RISK_PCT = 0.01
CAP = 0.10


def req(instrument: str, side: ScopeDirection, stop_distance: float = 100.0) -> EntryRequest:
    return EntryRequest(
        instrument=instrument, direction=side, price=1_000.0, stop_distance=stop_distance
    )


def test_ten_simultaneous_entries_share_one_equity_snapshot() -> None:
    """All ten size off the same E, so all ten commit the same R."""
    requests = [req(f"S{i:02d}", "long") for i in range(10)]
    decisions = admit_batch(requests, equity=100_000.0, risk_pct=RISK_PCT, cap=CAP, open_risk=0.0)
    admitted = [d for d in decisions if d.admitted]
    assert len(admitted) == 10
    assert {round(d.risk, 9) for d in admitted} == {1_000.0}


def test_the_eleventh_entry_is_denied_with_a_coded_reason() -> None:
    requests = [req(f"S{i:02d}", "long") for i in range(11)]
    decisions = admit_batch(requests, equity=100_000.0, risk_pct=RISK_PCT, cap=CAP, open_risk=0.0)
    assert sum(d.admitted for d in decisions) == 10
    denied = [d for d in decisions if not d.admitted]
    assert len(denied) == 1
    assert denied[0].reason == "portfolio_risk_cap"


def test_already_open_risk_counts_against_the_cap() -> None:
    """The cap is on the whole book, not on this batch."""
    requests = [req("S00", "long"), req("S01", "long")]
    decisions = admit_batch(
        requests, equity=100_000.0, risk_pct=RISK_PCT, cap=CAP, open_risk=9_000.0
    )
    assert [d.admitted for d in decisions] == [True, False]
    assert decisions[1].reason == "portfolio_risk_cap"


def test_shuffled_input_produces_the_same_canonical_admission() -> None:
    """The load-bearing property: who gets in cannot depend on input order."""
    names = [f"S{i:02d}" for i in range(11)]
    forward = [req(n, "long") for n in names]
    backward = [req(n, "long") for n in reversed(names)]
    a = {d.instrument for d in admit_batch(forward, 100_000.0, RISK_PCT, CAP, 0.0) if d.admitted}
    b = {d.instrument for d in admit_batch(backward, 100_000.0, RISK_PCT, CAP, 0.0) if d.admitted}
    assert a == b
    assert "S10" not in a  # the last instrument alphabetically is the one denied


def test_long_is_considered_before_short_on_one_instrument() -> None:
    """Part of the canonical order, and the reason ADR-0032 fixes symbol leverage for a
    campaign: without that, this ordering would bias the long-vs-short comparison itself."""
    requests = [req("S00", "short"), req("S00", "long")]
    decisions = admit_batch(requests, 100_000.0, RISK_PCT, cap=0.01, open_risk=0.0)
    assert decisions[0].instrument == "S00" and decisions[0].direction == "long"
    assert decisions[0].admitted and not decisions[1].admitted


def test_a_falling_equity_breach_blocks_new_entries_and_moves_no_stop() -> None:
    """Existing exposure above the cap blocks new entries. It never closes an old position or
    widens a stop to make room — the requirements forbid both."""
    decisions = admit_batch(
        [req("S00", "long")], equity=50_000.0, risk_pct=RISK_PCT, cap=CAP, open_risk=9_000.0
    )
    assert not decisions[0].admitted
    assert decisions[0].reason == "portfolio_risk_cap"


def test_a_non_positive_stop_distance_is_denied_not_sized() -> None:
    decisions = admit_batch([req("S00", "long", stop_distance=0.0)], 100_000.0, RISK_PCT, CAP, 0.0)
    assert not decisions[0].admitted and decisions[0].reason == "no_stop_distance"


def test_quantity_is_risk_over_stop_distance() -> None:
    decisions = admit_batch([req("S00", "long", stop_distance=25.0)], 100_000.0, RISK_PCT, CAP, 0.0)
    assert decisions[0].admitted
    assert decisions[0].quantity == pytest.approx(40.0)  # 1_000 / 25


def test_zero_equity_admits_nothing() -> None:
    decisions = admit_batch([req("S00", "long")], 0.0, RISK_PCT, CAP, 0.0)
    assert not decisions[0].admitted and decisions[0].reason == "no_equity"
