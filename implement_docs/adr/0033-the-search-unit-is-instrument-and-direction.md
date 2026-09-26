# ADR-0033 — The search unit is (instrument, direction)

- **Status:** Accepted
- **Date:** 2026-09-25
- **Task:** P3-11 … P3-15 · **Arch:** §3.1.11, §3.2.1, §4.1 · **Supersedes:** the `(engine, seed)` unit of ADR-0024 and ADR-0027

## Context

Until now one strategy was searched and then run across five spot pairs at once. `validation/run.py::make_candidate` made that so in a single line — `universe = tuple(is_data)` — and every candidate was therefore a basket strategy whether or not anyone intended it.

`PLAN-PER-INSTRUMENT-STRATEGIES.en.md`, frozen 2026-09-25, replaces that with an independent search for each `(instrument, direction)`: BTC-long and BTC-short are different searches, not one strategy read two ways. That widens the unit that everything downstream is keyed by — the scheduler's quota, the random seed, the MAP-Elites archive, the portfolio slot, the comparison protocol and the review UI all assumed `(engine, seed)`.

The spec asks for a ledger migration that "uses an explicit legacy value" for the 1,325 existing trials. That is impossible: the append-only triggers in `schema.sql:148-197` forbid `UPDATE` on every table except `campaigns`. A backfill cannot be written, and the constraint is a feature — P2 exists so a recorded trial cannot be revised after the fact.

## Decision

1. **`instrument` and `direction` are real ledger columns**, added by `migration_007_scope.sql` on `trials` and `generation_log`, following the precedent of migration 005. They do not ride on `universe`, which is a comma-joined TEXT of a whole basket that `is_gates.universe_bars` parses to select bars; overloading it would turn every query into string matching, and `direction` would have nowhere to live at all.
2. **Legacy scope is resolved at read time, never written.** A pre-P3-11 row stores NULL and reads as `legacy_spot` / `long`. `LEGACY_INSTRUMENT` is a **label for absent scope, not a contract**: `Key.is_legacy` and `Key.searched_instrument` keep it from travelling as one. It escaped once and made a legacy campaign's candidate ask for bars that do not exist — caught **only** by the `-m docker` end-to-end runs while all 764 unit tests stayed green.
3. **`engine_seed` mixes the scope into the seed.** `base ^ (sha256("instrument|direction")[:12] << 8)`. Without it BTC-long and BTC-short in the same arm draw the identical random sequence, and the "independent search" is one search reported twice.
4. **Quota is counted within its own scope**, and a budget that does not divide exactly across units is refused rather than silently rounded. INV-61 — concurrent workers cannot overshoot a quota — is unchanged and still holds at the wider key.
5. **The MAP-Elites archive and the §3.2.1 cell dedup are scoped.** `build_portfolio` deduplicated cells campaign-wide, so BTC-long and XRP-short landing in the same behavioural cell would have eliminated each other for reasons that have nothing to do with either.
6. **The portfolio is slot-based: at most one member per `(instrument, direction)`.** A slot with no gate-④ pass **stays empty** rather than taking a post-hoc replacement — filling it from the next-best candidate is selection after the fact, which is what the gates exist to prevent.
7. **`decide()` becomes a paired within-scope difference.** For each `(instrument, direction, seed)`, `d = eff_gp − eff_random`, and `gp_beats_random ⟺ mean(d) > sd(d)`. Pooling all thirty numbers and taking their sd admits **between-instrument** variance — BTC and XRP clear gate ④ at different rates for reasons unrelated to the engine — and the spread swallows any real effect, turning the rule into a machine that always returns `tie`. A tie reads as evidence of no difference, so that is worse than having no test. The pooled form is kept as a negative-control test.
8. **Protocol version 5**, with the wider `unit` string and `scope_verdicts = "none"` hashed as data: an individual `(instrument, direction)` is exploratory and carries no win or loss, and reporting one requires a new protocol version rather than a reinterpretation of data that was never gathered for it.
9. **The review reader labels rather than blanks, and opens what exists.** A NULL scope renders as the legacy scope, so a v4 campaign still reads as a campaign and not as missing data; a scope filter finds nothing in a legacy campaign, because a legacy row belongs to no perpetual scope. `READABLE_SCHEMAS` is v6 **and** v7: the reader is forbidden to migrate, so it must not also demand a migration — on v6 the two absent columns become NULL literals, which is the same shape a legacy row has on v7.
10. **The quota shown in the UI divides the budget by the number of units.** The literal `2` it divided by was the arm count, which stopped being the whole story the moment the unit widened.

## Consequences

- **1,325 existing trials are NULL-scoped forever.** They read as legacy and can never be corrected, which is exactly what P2 promises. Five OPEN campaigns keep their meaning as records; none can be resumed, because a v4 campaign is refused under protocol v5 (INV-74).
- **`n_eff` and `trial_stats` stay global.** INV-42 is unchanged: the deflation term counts every trial this research programme has run, across all scopes. Splitting it per scope would let a campaign reset its own multiple-testing penalty by renaming the instrument.
- **Trial budget divides further.** 600 trials across 6 units was 100 each; across 30 units it is 20 each. Whether 20 trials per scope is a search or a sample is an open question for whoever sets the pilot's shape — it is not decided here.
- **The archive is partitioned, so cross-scope structural novelty is no longer visible.** Two scopes never compete for a cell, which is correct for the search; it also means a clause that is novel in BTC-long and commonplace in ETH-long is scored as novel in both.
- **The comparison now needs at least three seeds × the scopes to have pairs at all.** With one scope the paired form degenerates to the old one; the protocol's `complete` rule still withholds a decision until every unit of every arm has spent its quota.
- **`RANKING_CTX` keeps its hardcoded `periods_per_year = 365.0`.** It is the frozen scenario for the ranking fingerprint (INV-82) and must not drift with the timeframe knob.

## Alternatives considered

- **Encode the scope inside `universe`.** Rejected under decision 1: `universe` is parsed to select bars, and `direction` has no representation in it.
- **Backfill legacy trials with an explicit `legacy_spot` value, as the spec asks.** Not possible — append-only triggers forbid the `UPDATE`, and disabling them to run a migration would break P2 for the sake of cosmetics. Read-time resolution gives the same result with no write.
- **Pool the thirty efficiencies and compare the two means.** Rejected under decision 7. It was the original ADR-0027 form and it is correct only when every observation is exchangeable, which stops being true the moment scopes differ.
- **Let each `(instrument, direction)` carry its own verdict.** Rejected under decision 8: thirty simultaneous comparisons at three seeds each is a multiple-testing problem the protocol does not currently correct for, and reporting the winners would be best-of-N by another name.
- **Require the ledger to be migrated to v7 before the review UI opens it.** Rejected under decision 9: the reader is read-only by contract, and a reader that cannot migrate must not be able to demand one — the only ledger that exists is v6.
