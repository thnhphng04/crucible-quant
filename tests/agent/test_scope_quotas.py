"""The search unit widens to (instrument, direction, engine, seed) (P3-12, ADR-0033) — INV-96.

Each scope is an independent arm: its own quota, its own RNG stream, its own archive. Two things
fail silently if this is done carelessly, and both have a test here.

The first is the RNG. `engine_seed` folded only engine and seed, so twenty arms of one
scope-pair would draw *identical* sequences and the "independent search per scope" premise would
be false at the RNG level — while producing perfectly plausible results.

The second is the split. A budget that does not divide exactly across scopes x arms x seeds
leaves some scope short, and a comparison whose arms did not get the same quota is not a
comparison. It is refused rather than rounded.
"""

from __future__ import annotations

from typing import Any

import pytest

from quantcrucible.agent.run import engine_seed, grammar_config
from quantcrucible.agent.scheduler import Scope, TrialScheduler, campaign_scopes, quotas
from tests.factories import unit

SHARES = {"gp": 0.5, "random": 0.5}
SCOPES = (Scope("BTCUSDT", "long"), Scope("BTCUSDT", "short"), Scope("ETHUSDT", "long"))


def test_the_budget_divides_across_scopes_arms_and_seeds() -> None:
    q = quotas(trial_budget=540, shares=SHARES, seeds=3, scopes=SCOPES)
    assert len(q) == 3 * 2 * 3 == 18  # scopes x arms x seeds
    assert set(q.values()) == {30}
    assert sum(q.values()) == 540


def test_the_requirements_600_trial_shape_divides() -> None:
    """5 instruments x 2 directions x 2 engines x 3 seeds x 10 = 600, from the frozen spec."""
    scopes = tuple(Scope(i, d) for i in ("A", "B", "C", "D", "E") for d in ("long", "short"))
    q = quotas(trial_budget=600, shares=SHARES, seeds=3, scopes=scopes)
    assert len(q) == 60 and set(q.values()) == {10}


def test_a_budget_that_does_not_divide_exactly_is_refused() -> None:
    """A comparison whose arms did not get the same quota is not a comparison (INV-73)."""
    with pytest.raises(ValueError, match="does not divide"):
        quotas(trial_budget=600, shares=SHARES, seeds=3, scopes=SCOPES)  # 600 / 18 is not whole


def test_unequal_engine_shares_are_refused() -> None:
    """0.8/0.2 cannot give both arms the same quota, so it is not a comparison budget."""
    with pytest.raises(ValueError, match="must be equal"):
        quotas(540, {"gp": 0.8, "random": 0.2}, seeds=3, scopes=SCOPES)


def test_the_key_carries_the_whole_scope() -> None:
    q = quotas(trial_budget=108, shares=SHARES, seeds=3, scopes=SCOPES)
    key = next(iter(q))
    assert key.instrument in {"BTCUSDT", "ETHUSDT"}
    assert key.direction in {"long", "short"}
    assert key.engine in SHARES
    assert 0 <= key.seed < 3


def test_two_scopes_draw_different_sequences() -> None:
    """Silent and plausible if wrong: every scope-pair would search the same thing."""
    seeds = {engine_seed("gp", 0, instrument=s.instrument, direction=s.direction) for s in SCOPES}
    assert len(seeds) == len(SCOPES)


def test_the_two_sides_of_one_contract_draw_different_sequences() -> None:
    a = engine_seed("gp", 0, instrument="BTCUSDT", direction="long")
    b = engine_seed("gp", 0, instrument="BTCUSDT", direction="short")
    assert a != b


def test_engines_and_seeds_still_separate_within_a_scope() -> None:
    kw = {"instrument": "BTCUSDT", "direction": "long"}
    assert engine_seed("gp", 0, **kw) != engine_seed("random", 0, **kw)
    assert engine_seed("gp", 0, **kw) != engine_seed("gp", 1, **kw)


def test_the_legacy_call_still_works_and_is_stable() -> None:
    """Pre-P3 campaigns recorded runs under the two-argument form; it must not move."""
    assert engine_seed("gp", 0) == 1_000_003 * 2
    assert engine_seed("random", 1) == 1_000_003 + 1


def test_quota_is_counted_within_its_own_scope() -> None:
    q = quotas(trial_budget=36, shares=SHARES, seeds=3, scopes=SCOPES)  # 2 per unit
    scheduler = TrialScheduler(q)
    key = next(iter(q))
    other = next(k for k in q if k != key)
    for _ in range(q[key]):
        assert scheduler.reserve(key)
        scheduler.settle(key, measured=True)
    assert not scheduler.reserve(key)  # this scope is exhausted
    assert scheduler.reserve(other)  # its neighbour is untouched


def test_in_flight_reservations_still_bound_the_quota() -> None:
    """INV-61, widened: concurrent workers cannot push a scope past its limit."""
    q = quotas(trial_budget=36, shares=SHARES, seeds=3, scopes=SCOPES)  # 2 per unit
    scheduler = TrialScheduler(q)
    key = next(iter(q))
    assert all(scheduler.reserve(key) for _ in range(q[key]))
    assert not scheduler.reserve(key)  # nothing settled yet, but the quota is spoken for


def test_the_legacy_sentinel_never_travels_as_an_instrument() -> None:
    """`legacy_spot` labels absent scope; it is not a contract.

    Letting it through made a candidate ask for bars that do not exist, which the phase-2
    end-to-end runs caught and the unit tests did not — so it is pinned here.
    """
    legacy = Scope("legacy_spot", "long")
    key = next(iter(quotas(6, SHARES, seeds=3, scopes=(legacy,))))
    assert key.is_legacy
    assert key.searched_instrument is None

    real = next(iter(quotas(6, SHARES, seeds=3, scopes=(Scope("BTCUSDT", "short"),))))
    assert not real.is_legacy
    assert real.searched_instrument == "BTCUSDT"


FIVE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]


def _lock(market: str, exit_protocol: str | None, **data: object) -> dict[str, object]:
    derived = {} if exit_protocol is None else {"exit_protocol": exit_protocol}
    return {
        "research": {"data": {"symbols": FIVE, "market": market, **data}},
        "derived": derived,
    }


def test_a_bracket_spot_campaign_searches_each_symbol_long() -> None:
    """A lock generated from user.yaml names symbols, never `instruments`. Read as legacy, a
    bracket campaign handed the whole basket to a replay that needs exactly one instrument,
    and every candidate died at gate ③ without a trial. Spot cannot short."""
    scopes = campaign_scopes(_lock("spot", "bracket_timeout_v1"))
    assert scopes == [Scope(s, "long") for s in sorted(FIVE)]
    q = quotas(600, SHARES, seeds=3, scopes=scopes)
    assert len(q) == 30 and set(q.values()) == {20}
    assert not any(k.is_legacy for k in q)


def test_a_bracket_perpetual_campaign_searches_both_sides() -> None:
    """The frozen per-instrument spec: 5 contracts x 2 sides x 2 engines x 3 seeds x 10."""
    scopes = campaign_scopes(_lock("usdt_m_perpetual", "bracket_timeout_v1"))
    assert scopes == [Scope(s, d) for s in sorted(FIVE) for d in ("long", "short")]
    assert set(quotas(600, SHARES, seeds=3, scopes=scopes).values()) == {10}


def test_a_pre_bracket_lock_keeps_its_legacy_scope() -> None:
    """Campaigns that ran before the bracket exit keep the shape they ran under."""
    assert campaign_scopes(_lock("spot", None)) == [Scope("legacy_spot", "long")]


def test_explicit_instruments_still_win() -> None:
    lock = _lock("spot", "bracket_timeout_v1", instruments=["ETH/USDT"], directions=["long"])
    assert campaign_scopes(lock) == [Scope("ETH/USDT", "long")]


def test_the_grammar_renders_the_side_its_scope_searches() -> None:
    """A short scope whose engines rendered `Signal("long", …)` would record long strategies
    under a short label."""
    lock = {"research": {"exit": {"tp_sl_ratio": 1.1}}, "derived": {
        "exit_protocol": "bracket_timeout_v1"}}  # fmt: skip
    assert grammar_config(lock, unit(instrument="BTC/USDT", direction="short")).direction == "short"
    assert grammar_config(lock, unit(instrument="BTC/USDT")).direction == "long"
    assert grammar_config(lock, unit()).direction == "long"  # legacy: long-or-flat
    assert grammar_config(lock, unit()).tp_sl_ratio == 1.1


def test_the_lock_decides_the_stop_kinds() -> None:
    """INV-116 (ADR-0042): a lock allowing only the ATR stop samples no Bollinger stop; a
    bracket lock written before the key keeps both."""
    derived = {"exit_protocol": "bracket_timeout_v1"}
    exit_: dict[str, Any] = {"tp_sl_ratio": 1.1}
    old = {"research": {"exit": exit_}, "derived": derived}
    atr = {"research": {"exit": {**exit_, "stop_kinds": ["atr"]}}, "derived": derived}
    both = {"research": {"exit": {**exit_, "stop_kinds": ["atr", "bollinger"]}}, "derived": derived}
    key = unit(instrument="BTC/USDT")
    assert grammar_config(old, key).boll_stop_probability == 0.5
    assert grammar_config(atr, key).boll_stop_probability == 0.0
    assert grammar_config(both, key).boll_stop_probability == 0.5
    legacy = {"research": {"exit": exit_}, "derived": {}}
    assert grammar_config(legacy, key).boll_stop_probability == 0.0


def test_the_lock_decides_the_stop_period_gene_and_the_cap() -> None:
    """INV-114 / INV-115 (ADR-0041): both engines read the same grammar from the lock."""
    derived: dict[str, Any] = {"exit_protocol": "bracket_timeout_v1"}
    old = {"research": {"exit": {"tp_sl_ratio": 1.1}}, "derived": derived}
    new = {**old, "derived": {**derived, "stop_period": "tunable_v1", "max_tunables": 7}}
    cfg = grammar_config(old, unit(instrument="BTC/USDT"))
    assert (cfg.stop_period, cfg.max_params) == (False, 6)
    cfg = grammar_config(new, unit(instrument="BTC/USDT"))
    assert (cfg.stop_period, cfg.max_params) == (True, 7)
