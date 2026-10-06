# ADR-0052 — The MinBTL target Sharpe ceiling is 2.0

- **Status:** Accepted
- **Date:** 2026-10-06
- **Task:** P3-65 · **Arch:** §3.2 row ②, §10 D17, §10.1 · **Open item:** —

## Context

Gate ② refuses a candidate when the IS history is shorter than `MinBTL(N) = (E[max_N] / target)²` years (Bailey et al. 2014). D17 set the target to 1.5 annualized and made 1.5 a hard ceiling: a higher target shortens MinBTL, i.e. loosens the gate. At 1.5, three years of IS allow `N ≤ 121` trials per campaign (ADR-0050), and 600 trials need 4.29 years. The user wants a 1h perpetual campaign of 600 trials on three years of IS and, having been shown the trade-off, decided on 2026-10-06 to raise the ceiling to 2.0.

## Decision

- `MINBTL_TARGET_SHARPE_CEILING` is 2.0: `minbtl_target_sharpe` must lie in `(0, 2.0]`; above 2.0 is refused at load (INV-20).
- An omitted target still means 1.5 (`MINBTL_TARGET_SHARPE_DEFAULT`): only an explicit setting reaches the looser value.
- The target is locked per campaign as before, so an existing lock keeps the target it opened with.
- `config/user.yaml` sets 2.0 with `trial_budget: 600`; at three years of IS the cap is 2,128 trials.

## Consequences

- Gate ② no longer certifies that an IS Sharpe between 1.5 and 2.0 cannot be the luck of the best of `N` trials. A campaign at 2.0 is not comparable on gate ② with one at 1.5; the lock records which.
- Selection bias is still charged at gate ⑤: DSR deflates the consolidated portfolio's Sharpe by `N_eff` and `V[SR]` of the campaign, so more trials make ⑤ harder, not easier.
- `dsr_min`, `pbo_max` and D4 (`holdout_pass`) are unchanged.
- Arch: D17 (now settled at default 1.5, ceiling 2.0), §10.1's config comment and the hard-floor row; CLAUDE.md's invariant table; INV-20.

## Alternatives considered

- A longer IS window (start 2021-04-02, 4.5 years, cap 775 at 1.5) — keeps the ceiling and needs no new data; the user chose three years.
- Making 2.0 the default — would loosen every campaign that omits the key, not only the one asked for.
