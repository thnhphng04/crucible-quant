# ADR-0051 — Choosing the data window and the IS/holdout cut

- **Status:** Accepted
- **Date:** 2026-10-05
- **Task:** P3-64 · **Arch:** §4.2, §6.1, §10 D18, D28, §10.1 · **Open item:** —

## Context

The user wants to choose the backtest period: a data window holding IS and holdout, then the date that cuts it in two. The holdout was always the last `holdout_months` of the window. And the legacy stores (`data/is`, `data/perp`) are read whole, so `research.data.start` did not cut anything there. A lock could record `start: 2022-10-02` while every backtest ran from 2020-09-15. Moving the cut earlier also raises a risk the old rule never met: the ledger already holds trials searched on 2018-01-01 → 2025-09-20 of the same five coins, so a holdout reaching back into that period would test on data research has already seen.

## Decision

- `research.data.holdout_start` (date, default `null`) is the cut. When it is set, IS = `[start, holdout_start)` and holdout = `[holdout_start, end]`. When it is `null`, the holdout is the last `holdout_months` months, as before. A holdout lasts at least 28 days. `data/window.py::holdout_window` is the only place the cut is computed: the legacy fetch, the perpetual fetch and preflight, and the dataset registry all call it.
- The dataset registry records the cut as `DatasetSpec.holdout_start_utc`. It enters the spec hash only when set, so every published dataset keeps its id. Studio refuses a dataset whose cut differs from the draft. Studio's data step has a cut-date field; while it is filled, the months field is disabled.
- **A clean holdout (D28).** `Ledger.latest_is_end()` is the last IS day of any recorded trial, whatever its campaign, symbol or market (spot and perpetual of one coin move together). A holdout must start after that day. The check runs:
  - before a dataset is downloaded (`fetch_and_publish(latest_is_end=…)`, from the Studio job);
  - when a campaign opens (`validation/run.py::admission_problems`), on the CLI, in the dry run and in Studio.
- **The legacy stores are cut at `data.start`.** A new lock carries `derived.data_window: v1`, and its session cuts the IS at `data.start` (`data/window.py::is_from`): trade bars and perpetual bundles at the same bar, both sources alike. A legacy store whose first bar comes after `data.start` is refused when the campaign opens, because the lock would record history the backtests never get. A holdout other than the stored one is prepared as a dataset in Studio.
- A lock without `data.holdout_start` still matches a config that leaves the key `null` (`config/lock.py::_comparable`).

## Consequences

- With the ledger of 2026-10-05, a holdout may start on 2025-09-21 at the earliest. A cut earlier than that needs data no recorded trial has seen, which these five coins do not have.
- A later IS start on a stored window costs nothing: the session cuts it. A different holdout costs a download through the registry, including the minute paths of a perpetual, because research code never reads the holdout files.
- Limit: the check looks at trials already recorded. A later campaign whose IS runs past another campaign's unclaimed holdout is not refused here. That holdout's claim remains guarded only by `holdout_collision` (overlap with *claimed* holdouts).
- Arch: D18 says the window and the cut are chosen; D28 is the clean-holdout rule; the §10.1 example config shows `holdout_start`.

## Alternatives considered

- Refuse a legacy store longer than the window — this forces a full re-download of data already on disk just to start the IS later.
- Check overlap only per symbol and market — spot and perpetual BTC are close to one series; a per-market check would let a spot search contaminate a perpetual holdout.
