# ADR-0035 — The shared account sizes, from signals

- **Status:** Accepted
- **Date:** 2026-09-26
- **Task:** P3-20 · **Arch:** §3.2.1, §3.4 · **Supersedes:** the fills-driven `replay_account` of P3-08 · **Adds:** INV-101

## Context

P3-08 built a joint-account replay that took each portfolio member's **fills** — produced by its own standalone backtest — and walked them through one `PerpAccount`. Reviewed before it was wired to anything, that design does not hold **P4′**.

A fill's quantity was sized by `RiskSizer` against the equity of the run that produced it. Replaying it on a shared account means the same quantity against a different balance: an order sized from 100,000 replayed on an account holding 80,000 risks **1.25%**, not 1%. That is a violation at exactly the point P4′ is defined, and it is silent — the numbers all look like numbers. `SlotFill` does not even carry `stop_distance`, so the risk cannot be re-derived to correct it.

The same defect has a second face. `BridgeStrategy._equity()` read the venue's account, whose margin is zero by design (ADR-0032) and which never sees a funding payment, so a single-strategy backtest was already sizing from an equity the host account did not have.

## Decision

1. **The account sizes, at both tiers.** `RiskSizer.target` already takes equity as an argument; what changes is where that number comes from. For one strategy (gate ③/④) it is the host `PerpAccount`'s equity, settled before the bar is sized (P3-19). For a portfolio (gate ⑤) it is the shared account's, snapshotted once per bar.
2. **A portfolio replays signals, not fills.** `SlotPlan` carries a slot's **signal stream** — direction and stop distance per bar, which is exactly what the `signals` sandbox job already produces — and `replay_signals` does the sizing, the admission, the margin and the exits itself. The fills-driven `replay_account` stays as a reconciliation tool for tests; nothing in the pipeline calls it.
3. **`admit_batch` is called for real, on one snapshot per bar.** Canonical order — instrument ascending, long before short — with the 10% portfolio cap counted against the commitment at the stop, frozen at entry. The ≤10 positions bound is arithmetic rather than a separate rule: ten slots risking exactly 1% each exhaust a 10% cap (ADR-0031 decision 5).
4. **Every order fills at the next bar's open** (ADR-0003), entries and exits alike, with the stop anchored to the **signal bar's close** and fixed for the position's life (P3-19). An exit priced at the deciding bar's own close would use information the order could not have acted on; that was a real defect in the first implementation, caught by the hand-priced test.
5. **A stop fills at its trigger, or worse if the bar gapped past it.** Never better — that would be a free option. A bar opening at 90 against a trigger of 95 fills at 90, and the test pins both paths to the resulting 2,000 loss rather than the 1,000 the trigger alone suggests.
6. **Contributions are a time series, and the decomposition is exact by construction.** Every USDT that enters or leaves the shared balance is attributed to exactly one slot, and a slot's contribution is its booked total plus what its open wallet is currently worth. Summing them therefore equals account equity minus the opening balance **at every bar**, including while positions are open — which is the case that matters, and the one a scalar contribution could not express.
7. **A drawdown breach is recorded and acted on by nothing.** `kill_switch_drawdown` is Group A in §10.1 — it *"only matters live"* — so a research replay that flattened on it would invent a rule no decision record covers, and would change which strategies pass. `SignalReplay.drawdown_breach_at` reports it instead. This corrects the P3-20 plan, which listed "the kill switch is called" as an acceptance criterion.
8. **Instruments in one replay must share a timestamp axis, and a set that does not is refused.** Not unioned: a slot whose bars are offset would have its funding and liquidation resolved against another instrument's bar, which is the desynchronisation ADR-0034 exists to prevent.
9. **The re-implementation is pinned against the venue.** With one member and capital to spare, `replay_signals` must reproduce the Nautilus-driven single-strategy equity curve. It does, at `rtol=1e-9`, for both a position that runs to the end and one that gaps through its stop. The tests also assert the curve is not flat, because two constant curves would agree for no reason.

## Consequences

- **This is a second execution engine, and that is the cost of the decision.** `replay_signals` re-implements the next-open convention, the cost model and protective-stop resolution. Decision 9 is the only thing keeping it honest; if that pin ever loosens, the portfolio tier and the candidate tier are measuring different things and the comparison between them is meaningless.
- **`Member.weight` loses its meaning on the perpetual path.** Under `Q = R/d` every slot risks exactly 1% and the cap bounds the total, so there is nothing for naive risk parity to weight. `validation.portfolio` steps 4–5 are replaced by the replay in P3-21; the spot path keeps `combine`.
- **Gate ③ must persist each trial's signal stream**, at `results/signals/{campaign}/{candidate}.parquet`, alongside the returns it already writes. No ledger migration: it follows the existing artifact convention.
- **`PROTOCOL["replay"]` changed**, so the campaign protocol's hash moves. No campaign has opened under v5, so this is free; after one has, it would need a version bump.
- **The fills-driven replay is now dead code with live tests.** Kept deliberately — it is an independent implementation of the same arithmetic and so is useful for reconciliation — but it must not acquire callers.

## Alternatives considered

- **Re-derive each fill's risk from the member's own equity curve and rescale.** Rejected: the rescaling factor depends on the shared account's path, which depends on the rescaling. It is circular, and a fixed-point iteration over five years of bars is both slow and unverifiable.
- **Add `stop_distance` to `SlotFill` and re-size on the fly.** Closer, but it still takes the *entry decision* from a run that saw a different account: a position the standalone run could afford may be one the shared account must deny, and a fill cannot be denied after the fact.
- **Let the portfolio tier run Nautilus too, with all members in one engine.** Attractive for P5, and rejected for the same reason as ADR-0032 decision 1: `MarginAccount` keys margin by `InstrumentId`, so it cannot hold one contract's two isolated wallets, which is what hedge mode is.
- **Flatten on a drawdown breach.** Rejected under decision 7.
