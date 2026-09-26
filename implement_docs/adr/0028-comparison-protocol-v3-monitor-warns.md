# ADR-0028 — Comparison protocol v3: fixed budget, the monitor only warns, the hash covers it

- **Status:** Accepted
- **Date:** 2026-09-24
- **Task:** P2-15 · **Arch:** §3.1.11 (comparison, decision rule), §3.2 (IS→OOS curve) · **Supersedes:** the monitor's role in [ADR-0027](0027-phase2-comparison-protocol.md) v2

## Context

The first complete run under protocol v2 (campaign `c-20260923-090957`, 552 trials) ended in `stopped_early`: the monitor stopped `random-s0` and `random-s1` at 76 of their 100 trials, so the report withheld the engine decision. Three things came out of it.

**The stopping rule is thin.** `is_oos_diverging` needs three checkpoints and then reads the sign of two OLS slopes. It does not ask whether the checkpoints share a record holder, whether the OOS drop is material, or whether the signal persists. In both stopped arms two of the three checkpoints were byte-identical repeats and the slope came from **one** record change:

```
random-s0   26 trials: IS 1.249 → OOS 1.214
            51 trials: IS 1.492 → OOS 1.044     record changed once
            76 trials: IS 1.492 → OOS 1.044     repeat of the previous point
```

The IS record line is monotone non-decreasing by construction, so for an arm whose record changes rarely — random search — each change is a more extreme IS outlier and its OOS is expected to be worse by regression to the mean. The data does not separate that from p-hacking.

**Stopping on an observed result makes the comparison endogenous.** §3.1.11 compares the arms *at the same trial budget*. A stop triggered by the OOS metric makes each arm's stopping time a function of the very quantity being measured, and truncates exactly the arms that look worse. Whatever the rule's merits inside an ordinary search, it does not belong in the experiment that measures the search.

**The locked hash does not cover the monitor.** `protocol_hash` hashes the `PROTOCOL` dict, in which `is_oos_diverging` appears only as a string in `supporting`. Changing `FLAT_SLOPE`, `min_points` or the whole function leaves the hash untouched, so a campaign would still pass `assert_protocol_is_the_locked_one` under a monitor that behaves differently. This is the hole ADR-0027 v2 closed for the decision rule, still open for the monitor.

## Decision

1. **A comparison campaign runs its whole budget.** Under protocol v3 the monitor never stops an arm: every (engine, seed) uses its full quota, and the arms are compared at equal budget as §3.1.11 requires.
2. **The monitor warns and the warning is recorded.** On divergence the monitor writes one `DEGRADATION_WARNING` audit event per (engine, seed) and keeps going. The report lists the warnings under `divergence_warnings` and per seed as `diverging`, but they do not enter the decision — they are evaluated after the run, against a rule that does not yet exist.
3. **The protocol carries the monitor.** `PROTOCOL["monitor"]` states the mode, the signal and its parameters, and `protocol_hash` folds in a **behavioural fingerprint**: the verdicts of `is_oos_diverging` over a fixed set of checkpoint shapes (`MONITOR_SCENARIOS`), hashed. A change that flips any of those verdicts changes the protocol hash and so needs a new campaign; renaming, reformatting or editing a docstring does not.
4. **`stopped_early` is retired.** Under v3 an arm cannot be stopped, so the outcome cannot arise. An interrupted run is still `incomplete`.

## Consequences

- Campaign `c-20260923-090957` stands as it is: **no engine decision**. Its 552 trials stay in `N`. C-gp's trial efficiency (48.3 vs 25.8 per 100 trials) and coverage (45/34/32 vs 15/24/26 cells) are recorded as suggestive, not as a verdict.
- v3 changes the hash, so the comparison restarts in a new campaign — roughly 600 more trials in `N`. That is the second such restart; both were bought by defects found before any result was read, which is the order the design intends.
- The fingerprint only detects changes that flip a verdict on its scenarios. The set therefore spans the decision boundary — flat lines, single transitions, repeated records, IS up with OOS up, IS up with OOS down, and sequences below `min_points` — and grows whenever a new boundary case is found.
- Divergence is no longer a brake during a comparison run. A genuinely p-hacking arm will spend its whole quota; at 100 trials per arm that is the price of an unbiased stopping time.

## Alternatives considered

- **Tighten `is_oos_diverging` and keep stopping** (require ≥ 3 distinct IS records, a minimum OOS drop, persistence across checkpoints): rejected *for the comparison campaign*, because no threshold removes the endogeneity — the stopping time still depends on the measured result. Kept as future work for ordinary search, where saving trials is the point; three records still give only two transitions, so the rule needs a magnitude and a persistence condition before it stops anything.
- **Hash the source text of `is_oos_diverging`:** rejected — it breaks on reformatting, and it misses the parameters that live outside the function.
- **Drop the monitor from comparison campaigns entirely:** rejected — the curve is one of §3.1.11's five metrics and costs nothing to record; only its authority to stop is removed.

## Amendment — the thresholds are the lock, the fingerprint is a check (2026-09-24)

Review of the first implementation found the lock pointing the wrong way round. `monitor_fingerprint` hashed the verdicts of eight fixed scenarios and nothing else, so a threshold moved *between* those scenarios' slopes changed real behaviour while the hash stood still: with `flat_slope` at 1e-5 instead of 1e-9 a slowly drifting arm stops being called divergent, yet none of the eight verdicts flips. The consequence named in **Consequences** above — "the fingerprint only detects changes that flip a verdict on its scenarios" — was therefore not a footnote but a hole, because the fingerprint was carrying the whole lock.

`DivergenceRule` (`validation/cpcv.py`) now holds `version`, `min_points` and `flat_slope` as data. `protocol()` writes that rule into `monitor.rule`, so the thresholds are hashed directly; `divergence_rule()` reads it back and the monitor, `stopped_keys` and `engine_report` are all run with the rule **the campaign locked** rather than the module default. `version` covers a rewrite of the arithmetic, which no threshold can describe. The fingerprint stays as a secondary check on exactly that case, and is documented as never being the lock.

The same review found `divergence_warnings` built from each seed's current `diverging` flag instead of from the ledger. An arm that diverged at its third checkpoint and recovered at its fourth kept its `DEGRADATION_WARNING` event but vanished from the report — the one place the warning was supposed to survive, since under v3 the warning is the whole output of the monitor. The report now lists `warned_keys()` and keeps `diverging` beside it as the state of the curve today.

## Additional probes before the next campaign (2026-09-24)

The fingerprint now has 17 fixed scenarios: the original eight plus empty/singleton inputs, slopes below/above the IS and OOS tolerance, recovery, a longer plateau, and an oscillating curve whose OLS direction differs from its endpoints. Explicit expected verdicts protect these probes. The rule and arithmetic are unchanged; adding probes changes the protocol hash, so existing campaign locks must not be rewritten.

The algorithm version remains a manual contract: an arithmetic change requires a version bump. Even these extra probes cannot detect every unversioned rewrite; the regression deliberately demonstrates a small threshold change invisible to the fingerprint but detected by the directly hashed rule.
