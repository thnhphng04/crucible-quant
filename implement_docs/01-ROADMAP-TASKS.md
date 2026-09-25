# 01 — Roadmap tasks

Arch §7 broken into tasks. Each task says: **goal**, **arch** reference, **files**, **accept when** (the test(s) that prove it), and **needs** (task dependencies). Status: ☐ todo · ◐ in progress · ✅ done.

Rules:
- Don't start a task whose `needs` aren't ✅.
- "Accept when" is the definition of done. Add the test names to [03-INVARIANT-TEST-MAP.md](03-INVARIANT-TEST-MAP.md) when a task enforces an invariant.
- Estimates are the architecture's (§7); task-level sizes are guesses — revise them freely.

---

## Phase 0 — Harness + ledger + oracle suite (2–3 weeks)

> **Phase gate (arch §7):** the pipeline **rejects all 4 leaky-oracle levels**, and the hand-written EMA crossover passes every phase-0 gate with every result in the ledger. The gate is *"can the harness reject cheating"*, not *"is anything profitable"*.

No agent code in this phase (P1).

### ✅ P0-01 Scaffold
- **Goal:** repo tooling so every later task starts from green checks.
- **Files:** `pyproject.toml`, `src/quantcrucible/**/__init__.py`, `tests/`, `.github/workflows/ci.yml`, `.claude/`, `scripts/check_doc_mirror.py`, `config/user.yaml`.
- **Accept when:** `ruff`, `mypy --strict`, `lint-imports` (7 contracts), `pytest`, `check_doc_mirror.py` all pass locally and in CI.

### ✅ P0-02 Ledger schema + append-only API
- **Goal:** the single source of truth for every trial (P2), created before anything that produces trials.
- **Arch:** §4.1 (`generation_log`, `trials`, `portfolio_variants`, views), §4.2 (`campaigns`, `holdout_access`).
- **Files:** `ledger/schema.sql`, `ledger/db.py`, `tests/ledger/`.
- **Details:**
  - SQLite, WAL mode. Schema is the arch DDL verbatim, plus the additions in [05](05-OPEN-ITEMS.md) O4/O5 (decide via ADR).
  - Append-only is enforced **in the database**, not only in Python: `BEFORE DELETE` triggers raise on every table; `BEFORE UPDATE` raises on every table (clusters go to `trial_clusters`, ADR-0002); `campaigns.status` may only move `OPEN → FROZEN → BURNED`.
  - Python API exposes `log_event(...)`, `record_trial(...)`, `record_portfolio_variant(...)`, `trial_stats()`, `starved_cells()`. No generic `execute()`.
- **Accept when:** tests show DELETE/UPDATE rejected at the SQL level (raw `sqlite3` connection, bypassing the API); a second `holdout_access` insert for the same campaign raises; illegal status transitions raise; `trial_stats` returns `n_raw`, `n_eff` (falls back to `n_raw` when `cluster_id` is NULL), `var_sr` on a fixture.

### ✅ P0-03 Config loader + campaign lock
- **Goal:** `config/user.yaml` → validated settings; opening a campaign generates `evaluation.lock.yaml`.
- **Arch:** §10, §10.1, §4.2.
- **Files:** `config/schema.py` (frozen dataclasses), `config/loader.py`, `config/lock.py`, `tests/config/`.
- **Details:**
  - Missing key ⇒ default from the §10 table. Unknown key ⇒ error (catches typos that would silently use a default).
  - Validate: `dsr_min ≥ 0.95`, `pbo_max ≤ 0.5` (hard floors), engine shares sum to 1, enums (`engine_mode`, `evolve_scope`, `rebalance`), positive numbers.
  - `open_campaign()` copies `research:` into `evaluation.lock.yaml` (+ template hash + pessimistic fill config later), sets it read-only, stores its SHA256 in `campaigns`.
  - Every run entry point calls `assert_lock_matches()` — Group B in `user.yaml` differing from the lock ⇒ refuse to run with a clear message (revert or open a new campaign).
  - `holdout_pass: null` is valid until P1-10 tries to open the holdout.
- **Accept when:** tests for each hard floor, unknown key, defaults, lock read-only, mid-campaign Group-B edit refused, Group-A edit allowed.
- **Needs:** P0-02.

### ✅ P0-04 Strategy contract + indicator whitelist
- **Goal:** the core contract every strategy speaks (P3).
- **Arch:** §3.3, §3.1.6 (DSL), §3.3.1.
- **Files:** `core/strategy/base.py` (`Signal`, `Strategy`, `Bars`, `Features`), `core/strategy/registry.py` (`ind`), `tests/core/strategy/`.
- **Details:**
  - `Signal` validates on construction: `strength ∈ [0,1]`; `stop_distance > 0` unless `direction == "flat"`; `flat` ⇒ `strength == 0`.
  - `ind.*` is the whitelist the static gate checks against. Start small: `ema`, `sma`, `atr`, `rsi`, `rolling_max`, `rolling_min`, `cross_up`, `cross_down`, `zscore`.
  - **Every indicator is causal.** One generic test: for random series and random `t`, perturbing bars after `t` must not change the indicator at `≤ t`. New indicators are added only with this test passing.
- **Accept when:** `Signal` rejects invalid values; the causality test passes for every registered indicator; the EMA crossover from §3.3.1 is expressible.

### ✅ P0-05 Template + TUNABLE parser
- **Goal:** fixed region vs `EVOLVE-BLOCK`, enforced in code.
- **Arch:** §3.3.1 (rules 1–3, named blocks, `evolve_scope`).
- **Files:** `core/strategy/template.py`, `core/strategy/tunable.py`, `tests/core/strategy/`.
- **Details:** parse `EVOLVE-BLOCK-START[: name]` / `END` markers; compute `SHA256(prefix) ‖ SHA256(suffix)` (with locked blocks counted as fixed per `evolve_scope`); parse `# TUNABLE: name = v, bounds=(a,b)` → typed params (≤ 6, finite bounds, default inside bounds); expose the PBO grid generator (D13: `values_per_param`, `range`, `max_configs`).
- **Accept when:** round-trip tests on the §3.3.1 example; tampering one byte of the fixed region changes the hash; 7 TUNABLE lines rejected; bounds violations rejected; whole-file replacement is detectable (markers missing/duplicated).
- **Needs:** P0-04.

### ✅ P0-06 Gate framework + EvaluationReport
- **Goal:** the pipeline skeleton every gate plugs into.
- **Arch:** §3.2 (contract, ordering table), §3.3.2.
- **Files:** `validation/gates.py`, `validation/report.py`, `tests/validation/`.
- **Details:**
  - `GateResult`, `Gate` protocol, `StrategyCandidate`. Pipeline runs gates in the arch order and stops at the first failure.
  - **Every** `GateResult` (pass or fail) is written to the ledger. Before gate ③ → `generation_log`; from gate ③ on → a `trials` row (§4.1).
  - **Fail closed:** an exception inside a gate ⇒ REJECT + ledger row with the traceback in `detail`.
  - `EvaluationReport(public, private, feedback, error)`; `feedback` is generated from `public` only.
- **Accept when:** ordering test; exception ⇒ reject + row; a test proves `feedback` cannot change when only `private` changes.
- **Needs:** P0-02.

### ✅ P0-07 Gate ①a — static guardrail
- **Goal:** reject bad code before it ever executes.
- **Arch:** §3.2 row ①a, §3.3.1 rules 1–3, §3.1.6 (DSL), 07-VALIDATION-LAYER §9.
- **Files:** `validation/guardrail.py`, `tests/validation/test_guardrail.py`. The AST whitelist lives in `validation/`; the agent's `dsl.py` (phase 2) reuses it rather than defining its own.
- **Details:** AST walk of the evolvable region: only whitelisted names/calls (`ind.*`, arithmetic, comparisons, `Signal`, `self.p.*`); ban `import`, `exec`/`eval`/`compile`, `open`, dunder access, `getattr`, globals; static look-ahead scan (negative shifts, `iloc[i+k]`, future indexing); undeclared numeric constants (rule 2, with the whitelist 0, 1, 14…); template hash + TUNABLE validity from P0-05.
- **Accept when:** a table-driven test of ≥ 20 malicious/invalid snippets, each rejected with the right reason; the EMA crossover passes; leaky oracle level 1 (`shift(-1)`) is rejected **here**.
- **Needs:** P0-05, P0-06.

### ✅ P0-08 Docker sandbox
- **Goal:** all generated code runs isolated.
- **Arch:** §3.3.3.
- **Files:** `validation/sandbox.py`, `docker/sandbox.Dockerfile`, `tests/validation/test_sandbox.py` (marker `docker`).
- **Details:** `docker run --rm --network none --read-only --tmpfs /tmp --memory … --cpus … --env-clear`-equivalent (`env -i`: an empty environment — [ADR-0004](adr/0004-docker-sandbox.md)); IS data mounted read-only; output = one JSON `EvaluationReport`; stdout/stderr truncated; timeout kills the container. Violations → `SANDBOX_VIOLATION` event.
- **Accept when:** docker-marked tests prove: no network (socket connect fails), empty env, `holdout/`, `config/`, `ledger/` not visible, writes outside tmp fail, timeout kills, oversized output truncated.
- **Needs:** P0-06. See [05](05-OPEN-ITEMS.md) O6 (Windows Docker specifics).

### ✅ P0-09 Data layer + holdout carve-out
- **Goal:** free crypto bars for research, with the holdout physically separated before any research touches the data.
- **Arch:** §6.1 (`DataSource`), §4.2 (layer 2), A7.
- **Files:** `data/source.py` (protocol), `data/ccxt_source.py`, `data/store.py` (parquet cache under root `data/`), `data/holdout_split.py`.
- **Details:**
  - Add `ccxt` + a parquet lib (pinned, license checked).
  - Daily/4h bars for a small crypto universe (e.g. BTC, ETH, SOL, BNB, XRP perp or spot).
  - The split writes the holdout segment to root `holdout/`, makes it read-only (ADR-0001: `attrib +R` / `icacls` on Windows, `chmod 0400` elsewhere), and writes its SHA256 to `holdout.lock`. The research loader **refuses** any request overlapping a campaign's `holdout_range`.
- **Accept when:** offline tests with a fixture exchange; a request overlapping the holdout range raises; the hash in `holdout.lock` matches; download tests exist under the `network` marker.
- **Needs:** P0-02, P0-03.

### ✅ P0-10 Backtest runner (NautilusTrader) — phase-0 subset
- **Goal:** run a `Strategy` on bars through NautilusTrader with the pessimistic fill model, so the same path serves live later (P5).
- **Arch:** §3.5 (pessimistic `FillModel`), §3.3.
- **Files:** `execution/engine.py`, `execution/nautilus_bridge.py` (Strategy → Nautilus strategy adapter), `tests/execution/`.
- **Details:**
  - Add `nautilus_trader` (pin; see O3).
  - Signals computed on a **closed** bar execute at the **next** bar's open — never on the bar that produced them (defeats oracle level 2).
  - Fees per venue schedule, slippage (charged as extra taker fee in bps — [ADR-0003](adr/0003-nautilus-fill-semantics.md)), limit orders fill only on trade-through.
  - Sizing: until P1-06, a clearly named `PlaceholderSizer` (fixed notional × `strength`) that P1-06 deletes.
  - Output: returns series + trade list → `EvaluationReport.public`.
- **Accept when:** a deterministic golden test (fixed bars → exact trades/PnL); a test that a signal on bar *t* fills at bar *t+1*; a limit that only touches is not filled.
- **Needs:** P0-04, P0-09.

### ✅ P0-11 Leaky-oracle suite + gate ①b
- **Goal:** prove the harness catches cheating — the phase-0 gate.
- **Arch:** 07-VALIDATION-LAYER §9, §3.2 row ①b.
- **Files:** `validation/oracles/level{1..4}_*.py` (strategies in template form), `validation/guardrail.py` (dynamic part), `tests/validation/test_oracles.py`.
- **Details:**
  1. `shift(-1)` — expected to die at ①a (static).
  2. Uses the forming bar's close — dynamic check: re-run with the last bar's close perturbed; signals for that bar must not change.
  3. Repainting indicator (e.g. centered window / future pivot) — dynamic truncation test: run on data truncated at *t* vs full data; all signals `≤ t` must be identical.
  4. Late-published information (PIT error) — a synthetic feature available with delay *d* but joined at event time; caught by the truncation test on the joined feature. See O8.
- **Accept when:** each of the 4 oracles is rejected at the expected gate with a ledger row, and the EMA crossover passes ①b. As built, all four die at ①a and ①b alone rejects all four too — [ADR-0005](adr/0005-leaky-oracles-and-dynamic-guardrail.md).
- **Needs:** P0-07, P0-08, P0-10.

### ✅ P0-12 Gates ② MinBTL + ③ IS backtest; end-to-end run
- **Goal:** close phase 0.
- **Arch:** §3.2 rows ②, ③; 07-VALIDATION-LAYER §4.4; D15.
- **Files:** `validation/statistical.py` (MinBTL only for now), `validation/is_gates.py`, `validation/run.py` + `cli validate`, `tests/e2e/test_phase0.py` — [ADR-0006](adr/0006-minbtl-and-in-sample-gates.md).
- **Details:** MinBTL from the running `N`; gate ③ rejects `trades < min_trades`, average holding `< min_holding_bars`, indicator pairs with IS `|ρ| > max_indicator_corr`; from ③ on each candidate is a `trials` row.
- **Accept when:** `tests/e2e/test_phase0.py` runs the EMA crossover + all 4 oracles through ①a → ①b → ② → ③; oracles rejected, EMA recorded as a trial; the `ledger` shows the expected `generation_log` + `trials` rows. **Phase-0 gate met.**
- **Needs:** P0-11.

---

## Phase 1 — Full validation layer + Risk & Sizing (2–3 weeks)

> **Phase gate (arch §7):** the hand-written strategy clears every gate; the ledger separates audit/trials correctly; `N_eff` + `V[SR]` are computable; DSR matches a published numerical example in the paper; PBO matches hand-computed fixtures from the CSCV/PBO definition and the result of an independent implementation on the same input ([ADR-0023](adr/0023-pbo-verified-by-definition-and-an-independent-implementation.md)); the ½-size test passes.

### ✅ P1-01 Verify `purgedcv` (first task of phase 1)
- **Arch:** §6 note, 07-VALIDATION-LAYER §7.
- **Goal:** decide *wrap* vs *implement ourselves* for DSR/PBO; keep `purgedcv` for CPCV/purging if sound.
- **Details:** read the installed source; pin the version; check whether DSR accepts `N` and `V[SR]` separately, and what PBO's input matrix and selection rule are.
- **Accept when:** an ADR records the decision with the checked signatures; O2 closed. Done: [ADR-0007](adr/0007-purgedcv-wrap-vs-implement.md).

### ✅ P1-02 DSR (+ PSR)
- **Arch:** §3.1.6, §4.1, 07 §4.
- **Files:** `validation/statistical.py`.
- **Accept when:** matches the numerical example of Bailey & López de Prado (2014) to stated precision; monotonic tests (more trials ⇒ lower DSR; higher `V[SR]` ⇒ lower DSR); reports at both `N_raw` and `N_eff`. Done: [ADR-0008](adr/0008-dsr-units-and-trial-count.md).

### ✅ P1-03 PBO via CSCV + gate ④
- **Arch:** §3.2 ("What PBO actually tests"), D13.
- **Accept when:** reproduces the Bailey et al. (2017) example; simulation tests — pure noise matrix ⇒ PBO ≈ 0.5 (not → 1), a planted-edge configuration ⇒ PBO → 0; the configuration set comes from the TUNABLE grid + ledger variants, capped at `M`; gate ④ rejects PBO ≥ `pbo_max`. Done: [ADR-0011](adr/0011-gate4-pbo-configuration-set-and-cscv.md).
- **Needs:** P0-05, P1-01.

### ✅ P1-04 CPCV with purging/embargo + IS→OOS curve
- **Arch:** §3.2 (degradation curve), 07 §3.
- **Accept when:** purging/embargo removes the overlapping observations on a hand-built fixture; CPCV-OOS median Sharpe goes into `EvaluationReport.private`, never `public`. Done: [ADR-0012](adr/0012-cpcv-for-rule-based-strategies.md) — computed inside gate ④ from its matrix.

### ✅ P1-05 `N_eff` (ONC clustering)
- **Arch:** §4.1 ("Estimating `N_eff`").
- **Files:** `validation/n_eff.py`.
- **Accept when:** on synthetic trials built from *k* independent sources plus noise, the estimate recovers *k* (± tolerance); appends a clustering run to `trial_clusters`; see O10. Done: [ADR-0009](adr/0009-n-eff-onc-with-significance-guard.md).

### ✅ P1-06 Risk & Sizing
- **Arch:** §3.4 (all), D7, D12.
- **Files:** `core/sizing/vol_target.py`, `core/sizing/position_sizer.py`; delete `PlaceholderSizer`.
- **Accept when:** **the ½-size test** — double both `vol_estimate` and ATR ⇒ size exactly halves when the stop cap does not bind; the stop cap binds as `min`, never as a multiplier; `round_to_lot` respects step/min/max; FX conversion to base currency; portfolio-level scaling back to `target_vol` with a leverage cap and IDM re-estimation. Done: [ADR-0010](adr/0010-risk-sizing-in-the-execution-path.md); `PlaceholderSizer` deleted.

### ✅ P1-07 Portfolio construction
- **Arch:** §3.2.1 steps 1–6, D9.
- **Files:** `validation/portfolio.py`.
- **Accept when:** each step unit-tested on a fixture (one per cell, `|ρ|` filter in DSR-rank order, cap K, naive risk parity, monthly rebalance); the `portfolio_hash` is deterministic and changes with any member/weight/rule change; each built portfolio is a `portfolio_variants` row. Done: [ADR-0013](adr/0013-portfolio-construction-and-strategy-archive.md).

### ✅ P1-08 Gate ⑤ — portfolio DSR
- **Arch:** §3.2 row ⑤, §3.1.6 DSR box, §4.1.
- **Accept when:** inputs come only from `trial_stats` + `total_portfolio_variants`; a test proves adding trials (any engine, any verdict) lowers the result; DSR is never computed per cell (no API for it). Done: [ADR-0014](adr/0014-gate5-portfolio-dsr-and-portfolio-pipeline.md).

### ✅ P1-09 Gate ⑥′ — data-source robustness + cost sensitivity
- **Arch:** §3.2 row ⑥′, §6.1 (two-tier data).
- **Accept when:** fees × 2 and slippage × 2 re-run; Sharpe > 0 and DSR within the campaign-locked drop; broker/second-source re-run within the 30% Sharpe-drop rule. Done: [ADR-0015](adr/0015-gate6p-robustness-costs-and-second-source.md); second source = Gate.io (`cli data-fetch-second`).

### ✅ P1-10 Holdout evaluator process + campaign state machine
- **Arch:** §4.2 (procedure, 3 layers), P6, D4.
- **Files:** `holdout/evaluator_proc.py` (own entry point), `holdout/campaign.py`.
- **Details:** separate process (CLI) that takes only `portfolio_hash`; checks campaign is `FROZEN`, `frozen_at < now`, `holdout.lock` hash matches; refuses if `holdout_pass` is unset (D4); writes `holdout_access`; prints exactly `PASS` or `FAIL`; moves the campaign to `BURNED`. Nothing else imports `quantcrucible.holdout` (import-linter).
- **Accept when:** tests on a **synthetic** holdout in a tmp dir: second opening raises; tampered holdout file ⇒ refuse; output is one token; `sharpe_oos` is stored but never printed. Done: [ADR-0016](adr/0016-holdout-evaluator-and-campaign-freeze.md); freeze lives in `validation/freeze.py` (the CLI may not import the evaluator package); D4 still open — the evaluator refuses until `holdout_pass` is set.
- **Needs:** P0-02, P0-03, P0-09, P1-07.

### ✅ P1-11 Calibration (step 5b)
- **Arch:** §3.2.1 step 5b, §3.3.1 (optimizer off in the loop).
- **Accept when:** Optuna over TUNABLE bounds with a fixed budget; **every** evaluation is a `trials` row with `source='param_opt'`; PBO and DSR are recomputed afterwards. Done: [ADR-0017](adr/0017-calibration-every-evaluation-a-trial.md).
- **Needs:** P1-03, P1-08.

### ✅ P1-12 End-to-end phase-1 run
- **Accept when:** `tests/e2e/test_phase1.py` takes the EMA crossover (and a few hand-written variants) through ⓪–⑥′, builds a portfolio, and freezes it; the phase gate above holds. Done: [ADR-0018](adr/0018-phase1-workflow-and-e2e.md) — workflow in `validation/research_run.py`, CLI `validate` (①a → ④) / `portfolio [--calibrate]` / `freeze`; synthetic data only; a real-data run waits on O16.

---

## Phase 2 — Engine C, no LLM, crypto only (4–6 weeks)

> **Phase gate (arch §7, v0.6):** ≥ 150 C-gp generations run autonomously; every `s_new` reaches the ledger; the feature map does not collapse into one bin; the island integration test passes; C-gp vs C-random compared in `isolated` mode with the decision rule locked before running. Engines A/B (LLM) are deferred (D19): no LLM code and no gate ⓪ drift in this phase — drift only guards LLM refinement passes.

### ✅ P2-01 Break phase 2 into tasks + orchestration ADR
- **Arch:** §3.1.11, §3.1.8, §7.
- **Goal:** this task list, the phase-2 invariant rows, and [ADR-0024](adr/0024-engine-c-orchestration-and-derived-evolution-state.md): plain-Python orchestration, evolution state derived from the ledger, scheduling by statistical trials.
- **Accept when:** `scripts/check_doc_mirror.py` passes; ADR-0024 accepted.

### ✅ P2-02 Config for engine C
- **Arch:** §10.1, D14, D19, D20.
- **Files:** `config/schema.py`, `config/loader.py`, `config/user.yaml`.
- **Details:** engine keys `gp`, `random`, `quantevolve`, `simple_loop`; `gp: {param_only_max, plateau_threshold}`; `campaign: {purpose: research|harness_test, trial_budget}` — all Group B, locked per campaign.
- **Accept when:** loader tests — shares sum to 1; A/B shares > 0 refused while D19 defers them; `param_only_max` ∈ [0, 1]; `trial_budget` > 0; a lock written with the old engine keys is refused by `assert_lock_matches` (a new campaign is needed). Done: `tests/config/test_loader.py::test_engine_c_is_the_focus_by_default`, `tests/config/test_lock.py::test_a_lock_with_the_old_engine_keys_is_refused`.
- **Needs:** P2-01.

### ✅ P2-03 Candidate provenance + ledger schema v5
- **Arch:** §4.1, §3.1.5.
- **Files:** `validation/run.py`, `validation/research_run.py`, `ledger/migration_005_*.sql`, `ledger/db.py`, `ledger/records.py`.
- **Details:** a `Provenance` (engine, seed, run_id, cell_id, island, parent hashes, mutation type) passed through `submit` into `StrategyCandidate`, `generation_log` and `trials`; new column `island`; parents and mutation type in `detail`; reads by (campaign, engine, seed).
- **Accept when:** provenance round-trips to both tables; migration 4 → 5 runs on a v4 database; the append-only triggers still hold. Done: `validation/run.py::Provenance`, `submit(..., params, provenance)`, `Ledger.trials(campaign, engine, seed)`, `Ledger.events_for`; the island columns are added only if missing so every migration stays idempotent.
- **Needs:** P2-01.

### ✅ P2-04 Typed DSL + scale invariance at gate ①a
- **Arch:** §3.1.6, §3.3.1 rule 3, §3.1.11 rule 2.
- **Files:** `core/strategy/registry.py` (`OpSpec`: input/output types, parameter kinds and bounds, warm-up), `validation/guardrail.py`, `agent/dsl.py`.
- **Accept when:** test first — comparing a price-scale series (e.g. `bars.close`) with a constant or a TUNABLE is `AST_REJECT`; every zoo strategy still passes ①a; type-inference unit tests. Done: scale invariance is a unit (dimension) check in `core/strategy/dims.py`, run by gate ①a after the structural whitelist; `OPS` in the registry carries each operator's output unit, period range, value range and warm-up (no separate `agent/dsl.py`).
- **Needs:** P2-01.

### ✅ P2-05 Grammar sampler + renderer = engine C-random
- **Arch:** §3.1.11 (C-random), §3.3.1.
- **Files:** `agent/grammar.py`, `agent/engines/random_search.py`.
- **Details:** typed syntax tree → evolvable-block text through `template.render`; ≤ 6 TUNABLE, uniform within bounds; the warm-up/finiteness guard always emitted; dedup by `strategy_hash`; deterministic per seed; the engine API takes no results.
- **Accept when:** 1,000 consecutive samples pass ①a (property test); the same seed gives the same sequence; every clause type is produced; C-random cannot read metrics (API + import test). Done: `agent/grammar.py` (typed genome: 7 clause types over price series and oscillators, and/or, ATR stop; renderer), `agent/engines/random_search.py`; `tests/agent/engines/test_random_search.py`. No take-profit until O19.
- **Needs:** P2-04.

### ✅ P2-06 Trial-budget scheduler + harness-test campaign
- **Arch:** §3.1.11 (budget), §4.1, gate ② (MinBTL).
- **Files:** `agent/scheduler.py`, `validation/run.py`, `validation/portfolio.py`, `validation/freeze.py`.
- **Accept when:** quotas per (engine, seed) are never exceeded under concurrent workers; a `harness_test` campaign cannot freeze, so it can never claim a holdout (it may build portfolios: the §3.1.11 comparison needs each engine's portfolio DSR); opening a campaign whose `trial_budget` plus the ledger's `N` exceeds what MinBTL allows for the IS length is refused. Done: `agent/scheduler.py` (quotas by engine share then seed, reservations settled as trial / not a trial), ledger schema v6 `campaign_purposes` with a trigger refusing FROZEN for a harness-test campaign, `validation/run.py::_check_trial_budget`.
- **Needs:** P2-02, P2-03.

### ✅ P2-07 Feature map with fixed bounds + per-engine archive
- **Arch:** §3.1.3, §3.1.11.
- **Files:** `agent/evolution/feature_map.py`, `agent/evolution/archive.py`, `config/lock.py` (bounds in `derived`).
- **Details:** 6 dimensions, 16 bins, bounds locked; the category comes from an AST classification of the DSL tree; the archive is rebuilt from the ledger and the strategy archive; one archive per engine.
- **Accept when:** bounds come only from the lock; out-of-range values land in the edge bin; rebuilding from the ledger reproduces the archive; engines never share an archive in `isolated` mode. Done: `agent/evolution/feature_map.py` (bounds from `derived.feature_map`, defaults in `validation/run.py::FEATURE_MAP`), `agent/evolution/archive.py` (per (engine, seed), from trials + gate results + lineage); gate ③ now keeps its `public` metrics in its detail and reports `sortino_is`; categories come from the genome (`grammar.categories`), recorded as `descriptors` at submission.
- **Needs:** P2-03.

### ✅ P2-08 Async pipeline, safe shutdown, gate-④ throughput
- **Arch:** §3.1.8, O9, O14, [ADR-0004](adr/0004-docker-sandbox.md).
- **Files:** `agent/pipeline.py`, `validation/sandbox.py`.
- **Details:** one producer per (engine, seed) → prefetch queue → K evaluation slots calling `submit`; a single ledger writer; sandbox containers labelled per run and killed on interrupt; gate-④ throughput measured, then improved (parallel grid jobs or a long-lived worker) without leaving NautilusTrader (P5).
- **Accept when:** an interrupt leaves no `qc-sandbox` container (docker test); no ledger lock errors under K slots; measured throughput recorded in an ADR; O14 closed or narrowed. Done: `agent/pipeline.py` (orchestrating thread + `workers` slots, each with its own ledger connection — SQLite serializes the writes), `SandboxRunner(label=…).kill_all()`, cheaper bar windows and Risk-layer bookkeeping (bit-identical equity, ~40% faster); [ADR-0025](adr/0025-evaluation-throughput-and-concurrency.md).
- **Needs:** P2-06.

### ✅ P2-09 CLI `evolve` + e2e for C-random
- **Files:** `cli.py`, `tests/e2e/test_phase2_random.py`.
- **Accept when:** on synthetic data with a temp ledger, every candidate has ledger rows, the run stops exactly at its trial quota, and the feature map has more than one occupied cell. Done: `agent/run.py::evolve` (quotas from the campaign's locked budget, scheduler started from the ledger, C-random replays its seeded sequence on resume), `cli evolve --engine random [--workers N]`; the e2e also checks that a second run adds nothing.
- **Needs:** P2-05, P2-06, P2-07, P2-08.

### ✅ P2-10 Parameter stability at gate ④ (SPP median, plateau)
- **Arch:** §3.2 (v0.6, D20).
- **Files:** `validation/pbo_gate.py`.
- **Accept when:** both come from the existing grid (no extra backtest, no trial); both are `public`; hand-built grid fixtures give the expected median and plateau; PBO and CPCV stay `private`. Done: `pbo_gate.param_stability` (candidate in row 0; plateau 0 without a positive Sharpe); gate ④ reports `spp_median_sharpe` and `plateau` as `public` and keeps them in its detail for the archive.
- **Needs:** P2-01.

### ✅ P2-11 GP operators + ranking score
- **Arch:** §3.1.11 (C-gp), §3.1.6 #1, D20.
- **Files:** `agent/evolution/operators.py`, `agent/evolution/ranking.py`.
- **Details:** typed subtree crossover, subtree and point mutation, TUNABLE mutation within bounds; parameter-only children ≤ `param_only_max`; mutation type recorded; ranking = §3.1.6 #1 with SPP median and plateau as a secondary term — exact form in an ADR.
- **Accept when:** every child is type-valid and passes ①a (property test); a parameter-only child keeps its parent's `strategy_hash`; the cap holds over a run; ranking reads `public` metrics only. Done: [ADR-0026](adr/0026-gp-operators-and-ranking-score.md) — the DSR term is ADR-0013's DSR-rank (PSR vs the deflated benchmark: no per-strategy DSR, INV-42); gate ③ adds the IS return moments to `public`; submissions record the clause `signature`.
- **Needs:** P2-05, P2-10.

### ✅ P2-12 Parent sampling, islands, migration
- **Arch:** §3.1.4, §3.1.5.
- **Files:** `agent/evolution/sampling.py`, `agent/evolution/islands.py`.
- **Accept when:** island integration test — the parent comes from the island being processed; migrants are not duplicated at the destination; a migrant's children belong to the destination; an empty island yields no children; each parent's island is logged. Done: `agent/evolution/islands.py` (N = C + 1 islands, ring migration of the top 10% as `MIGRATION` audit events, populations rebuilt from the ledger), `agent/evolution/sampling.py` (Eq. 1, α = 0.5; the crossover mate drawn the same way).
- **Needs:** P2-07, P2-11.

### ✅ P2-13 Engine C-gp + phase-2 e2e
- **Files:** `agent/engines/gp_search.py`, `agent/loop.py`, `tests/e2e/test_phase2.py`.
- **Accept when:** on synthetic data, ≥ 150 generations run unattended, every `s_new` is in the ledger, the feature map is not collapsed, the island test passes. Done: `agent/engines/gp_search.py` (state rebuilt from the ledger at every proposal: entries, genomes recorded with each submission, migrations, ranking), wired into `agent/run.py` (no separate `loop.py`); `tests/agent/test_gp_loop.py` runs 150 generations × 5 islands through the real pipeline and ledger with deterministic fake gates; `tests/e2e/test_phase2.py` runs C-gp and C-random through the real gates in docker.
- **Needs:** P2-09, P2-12.

### ✅ P2-14 Monitoring + early stop
- **Arch:** §3.2 (IS→OOS curve), §3.1.11 (comparison metrics).
- **Files:** `agent/monitor.py`.
- **Accept when:** coverage, trial efficiency and starved cells are reported per engine; IS→OOS divergence (`is_oos_diverging`) stops the engine; tested on fixtures. Done: `agent/monitor.py` — `engine_report`, `DEGRADATION_CHECKPOINT` audit events every 25 trials (IS record holder vs its CPCV-OOS median; the monitor is the only reader of that private metric), `EarlyStop` wired into the pipeline (`RunStats.stopped`).
- **Needs:** P2-13.

### ✅ P2-15 Comparison protocol + the real run
- **Arch:** §3.1.11 (comparison, decision rule).
- **Details:** an ADR locked before running (metrics, seed-spread definition, `trial_budget`, quotas); a read-only comparison report; one harness-test campaign on real IS data: C-gp and C-random × 3 seeds.
- **Accept when:** the ADR predates the run's first trial (checked against the ledger); the report applies the decision rule. Both hold. [ADR-0027](adr/0027-phase2-comparison-protocol.md) (+ v2 amendment) and [ADR-0028](adr/0028-comparison-protocol-v3-monitor-warns.md) carry the protocol; `agent/compare.py`, `agent/runlock.py` (INV-72) and fair slot sharing (INV-71) carry the run.
- **Runs so far.** v1, campaign `c-20260922-181850`: stopped after 121 trials — the slots never left `gp-s0`, and the review that followed found six more defects. v2, campaign `c-20260923-090957`: **552 trials over 14 h** (≈ 90 s/trial, 8 workers), gp 100/100/100 and random 76/76/100. Outcome **`stopped_early`: no engine decision**, because the monitor stopped `random-s0` and `random-s1` short of their quota. Recorded as suggestive only: trial efficiency gp 56/45/44 vs random 19.7/31.6/26.0 (means 48.3 vs 25.8, spread 6.66), archive coverage 45/34/32 vs 15/24/26 cells, gate-④ backtests per passing strategy 169 vs 240, portfolio DSR 0.0012 vs 0.0988 at `N_eff` 145 — both far under the ≈ 2.3 annualized Sharpe that gate ⑤ demands at that `N`. All 673 trials stay in `N`.
- **What the v2 run established** was a defect in our own harness, not a result about the engines: in both stopped arms two of the three checkpoints were identical repeats, so one record change carried the whole slope ([ADR-0028](adr/0028-comparison-protocol-v3-monitor-warns.md)). Protocol v3 fixes it — fixed budget, the monitor only warns (INV-77), and the protocol hash now covers what the monitor decides (INV-76).
- **v4, campaign `c-20260924-180916` — the run that finished.** 600/600 trials in 11.9 h (12 workers, 71 s/trial), every arm at its quota, none starved, `unfinished` empty: the first campaign to satisfy `complete` (INV-73). Outcome **`gp_beats_random`** — trial efficiency 53/48/50 against 20/30/25, means 50.33 vs 25.00 against a seed spread of 5.00. Three arms earned a `DEGRADATION_WARNING` (gp-s1, random-s0, random-s1) and all three ran on, which is what protocol v3 was for. The ranking margin (INV-81) held on live data at 2.72–9.83. **Neither portfolio is deployable**: DSR 0.0144 (gp) and 0.0201 (random) against `dsr_min` 0.95, as arch §11 warned for simple rules on daily crypto. The verdict is "evolution beats chance", not "this makes money".
- **Needs:** P2-13, P2-14.

---

### ✅ P2-16 The ranking's primary term, and the protocol that carries it
- **Arch:** §3.1.6 #1, §3.1.11 · **Amends:** [ADR-0026](adr/0026-gp-operators-and-ranking-score.md).
- **Goal:** the returns term of C-gp's ranking score decides parent selection again, a permanent check catches the class of defect that hid it, and the protocol hash covers the ranking as it already covers the monitor.
- **Details:** the primary term becomes the population rank of the DSR-rank (average ties, [0, 1]); no λ moves; `term_dispersion` + `ranking_margin` report the spread per (engine, seed), never stopping an arm; `PROTOCOL["ranking"]` hashes the λ as data and `ranking_fingerprint()` the selection order, protocol v4.
- **Accept when:** the regression reproduces the compression from campaign `c-20260923-090957` and shows the absolute-PSR form failing INV-81 while the rank form passes; the λ are covered by the protocol hash (INV-82); the existing ranking tests pass unchanged. Done: [ADR-0030](adr/0030-gp-ranking-primary-term-is-a-population-rank.md), `agent/evolution/ranking.py`, `tests/agent/evolution/test_ranking.py` (new mirror; the ranking tests moved out of `test_operators.py`), `ranking_margin` in `agent/monitor.py`, `RANKING_SCENARIOS` + `ranking_fingerprint` in `agent/compare.py`, and the calibration bench `tests/agent/test_ranking_recovery.py` (simulated market, fake gates, its own ledger, ≈ 105 s). Protocol hash `0cafe016ef5f…`.
- **Needs:** P2-14.

---

## Phase 3 — Per-(instrument, direction) strategies on USDT-M perpetuals (6–10 weeks)

Arch §3.4 (rewritten), §3.5, §7. Requirements: `PLAN-PER-INSTRUMENT-STRATEGIES.en.md`, frozen 2026-09-25.
Decisions: ADR-0031 (P4 retired, `Q = R/d`), ADR-0032 (host-side perpetual account), ADR-0033 (scope, protocol v5).

### ✅ P3-01 Perpetual data coverage spike
**Goal:** know what Binance USDT-M publishes before any decision is recorded. **Arch:** §6.1, D18. **Needs:** —
**Files:** `scripts/perp_coverage.py` (read-only, no `src/` change).
**Result (2026-09-25):** all three series exist for all five contracts. SOLUSDT is the binding listing at 2020-09-14, giving **5.03 years** of IS after a 12-month holdout. MinBTL at target 1.5 caps N at **1,475** against an `n_eff` of 290 — headroom 1,185. Leverage brackets need a signed endpoint. Mark 1m is ~3.5M rows per contract.
**Accept when:** ✅ the coverage table and a go/no-go line, recorded in ADR-0031.

### ✅ P3-02 Retire P4 in the architecture
**Goal:** the sizing rule changes in the source of truth before it changes in code. **Arch:** §3.4, §7, §10 (D7, D12, D18). **Needs:** P3-01
**Files:** `research_docs_vi/Architecture_Design.md` first, then the EN mirror; `CLAUDE.md`; `implement_docs/adr/0031-*.md` + VI mirror; `03-INVARIANT-TEST-MAP.md` (INV-05 retired, the INV-90..98 band added).
**Accept when:** `uv run python scripts/check_doc_mirror.py` passes; ADR-0031 records who decided, what replaces INV-05, and that `idm` / `portfolio_scale` / `target_vol` are deleted rather than deprecated.

### ✅ P3-03 `Q = R/d`: replace the INV-05 tests, then the code
**Goal:** position size is risk over stop distance, proven by tests written first. **Arch:** §3.4, ADR-0031. **Needs:** P3-02
**Files:** `tests/core/sizing/test_position_sizer.py`, `tests/execution/test_risk.py`, then `core/sizing/position_sizer.py`, `core/sizing/vol_target.py`, `execution/risk.py`, `config/schema.py`, `config/lock.py`, `validation/run.py`, `validation/is_gates.py`.
**Accept when:** the four INV-90 tests pass; the three INV-05 tests are gone; a lock carrying `derived.sizing.max_leverage` is refused with the existing "open a new campaign" error rather than silently mixing two sizing rules.

### ✅ P3-04 `direction` as a grammar axis, and the wrong-side guardrail
**Goal:** a scope's genome renders in its own direction; a wrong-side signal dies at gate ①a. **Arch:** §3.3.1, ADR-0031. **Needs:** P3-02
**Files:** `agent/grammar.py` (the rendered `Signal("long", …)` literal), `validation/guardrail.py`.
**Accept when:** INV-91; a long and a short rendering of the same clauses have different `strategy_hash`.

### ✅ P3-05 The bracket margin model
**Goal:** initial margin, maintenance margin, the lowest funded leverage and isolated liquidation, on a bracket table. **Arch:** §3.5, ADR-0032. **Needs:** P3-01, P3-03
**Files:** new `execution/margin.py`.
**Accept when:** ✅ `tests/execution/test_margin.py` — maintenance margin is continuous across a tier boundary (which is what the venue's `amount` term is for), the boundary itself belongs to the lower tier, leverage above the bracket is refused, and liquidation sits below a long's entry and above a short's.
**Deviation:** the venue switch (`CryptoPerpetual`, `OmsType.HEDGING`, `AccountType.MARGIN`) moved to P3-06. On its own it has no test that can fail for the right reason — a short only becomes observable once `TargetSizer` carries a side. The bracket table stays **data**: `fetchLeverageTiers` is a signed endpoint, so real values arrive with the campaign lock and are recorded there as an assumption.

### ✅ P3-06 The venue trades both sides
**Goal:** a short signal opens a short with its own protective stop. **Arch:** §3.5, P3. **Needs:** P3-04, P3-05
**Files:** `execution/nautilus_bridge.py` (`CryptoPerpetual`, `AccountType.MARGIN`, signed target, short-side stop, `FillRecord.position_side`), `execution/engine.py` (`_round_trips`, `assert_complete`).
**Accept when:** ✅ `tests/execution/test_hedge.py` — a short is traded rather than counted and dropped, and its round trips are counted; without that last part gate ③ rejected every short strategy on `min_trades` for a reason that had nothing to do with the strategy. INV-35's tests unchanged.
**Deviation: `OmsType.NETTING`, not `HEDGING`.** One backtest runs one strategy on one side, so this engine never holds both legs of a contract — the two-sided book is a property of the joint-account replay (P3-08), which is host-side. `HEDGING` also mints a fresh `PositionId` per entry order, which left `reduce_only` stops with nothing to reduce and silently stopped them filling.
**Two tests replaced, not deleted:** `test_short_signal_means_flat_on_spot` asserted the behaviour this task removes → `test_a_short_signal_is_traded_not_dropped`. `test_engine_stop_is_not_a_silent_truncation` drove the guard by exhausting a cash account, which a margin venue does not do → the guard is now `assert_complete` and is tested directly.

### ◐ P3-07 Funding, mark price, liquidation, intrabar path
**Goal:** the three things Nautilus does not model. **Arch:** §3.5, ADR-0032. **Needs:** P3-05, P3-06
**Files:** new `execution/perp_account.py`, new `execution/path_summary.py`, `validation/sandbox.py` and `sandbox_runner.py` (the container's data channel widens).
**Accept when:** INV-93, INV-94; first touch matches a brute-force minute scan; a simultaneous touch is flagged ambiguous and resolved to the worse outcome; minute bars never enter the sandbox.

### ◐ P3-08 Joint-account backtest and the admission rule
**Goal:** one account replay instead of N self-financed streams. **Arch:** §3.4, §3.2.1, ADR-0032. **Needs:** P3-07
**Files:** `execution/engine.py`, new `execution/admission.py`, `validation/sandbox_runner.py`.
**Accept when:** INV-92; account equity reconciles with the sum of slot contributions; the same seed reproduces the same account curve.

### ✅ P3-09 Perpetual data source and manifests
**Goal:** perpetual data with the integrity record the spot parquet never had. **Arch:** §6.1, D18. **Needs:** P3-01, P3-05
**Files:** new `data/perp_source.py`, `data/store.py`, new `data/manifest.py`.
**Accept when:** INV-94; a manifest records checksum, coverage and source; v4 spot bars cannot satisfy a perpetual request; `data` still imports no `execution` (import-linter).

### ☐ P3-10 Perpetual holdout carve — separate, write-once
**Goal:** a second holdout that never touches the first. **Arch:** §4.2, P6. **Needs:** P3-09
**Files:** `data/holdout_split.py`, `cli.py`, `holdout/evaluator_proc.py`.
**Accept when:** its own lock and manifest; the existing lock is never opened or overwritten; a second carve is refused; INV-08 holds for the new lock.

### ✅ P3-11 Migration 007 — scope columns
**Goal:** the ledger carries a scope, and 1,325 legacy rows keep their meaning untouched. **Arch:** §4.1, ADR-0033. **Needs:** —
**Files:** new `ledger/migration_007_scope.sql`, `ledger/db.py`, `ledger/records.py`.
**Accept when:** INV-95 — a legacy row resolves at read time, never by UPDATE, which the append-only triggers forbid. The migration runs against a temporary copy, never the real ledger.

### ✅ P3-12 The search unit becomes (instrument, direction, engine, seed)
**Goal:** each scope is an independent arm. **Arch:** §3.1.11, ADR-0033. **Needs:** P3-04, P3-11
**Files:** `agent/scheduler.py`, `agent/pipeline.py`, `agent/run.py`, `agent/engines/`, `agent/evolution/archive.py`, `islands.py`, `validation/run.py` (`universe=tuple(is_data)`), `validation/gates.py`.
**Accept when:** INV-96; INV-61 and INV-71 widened and still green; INV-65 strengthened — the engine still never sees its instrument.

### ✅ P3-13 Scoped gates, slot portfolios, protocol v5
**Goal:** gates ③④ on one contract and direction; gate ⑤ on one joint-account replay. **Arch:** §3.2, §3.2.1, §3.1.11. **Needs:** P3-08, P3-12
**Files:** `validation/is_gates.py`, `pbo_gate.py`, `portfolio.py`, `robustness.py`, `calibration.py`, `agent/compare.py`, `agent/monitor.py`, `review/repository.py`.
**Accept when:** INV-97, INV-98; a PBO grid never mixes scopes; INV-42 unchanged, so `trial_stats` stays global; a v4 campaign is refused under v5 (INV-74).

### ✅ P3-14 Timeframe is a real knob
**Goal:** switching `research.data.timeframe` to `4h` runs end to end. **Arch:** D18, §3.2. **Needs:** P3-13
**Files:** `validation/portfolio.py` (`Member.from_dict` must refuse a missing timeframe rather than default it), `validation/run.py` (`DEFAULT_LOOKBACK` counts bars), `config/schema.py`, `validation/is_gates.py`.
**Accept when:** the whole path runs at 4h on the same fixtures with correct annualisation; funding lands correctly at 1d, 4h and 8h bars.

### ☐ P3-15 Review API, UI, legacy regression, quality gate
**Goal:** the new results are readable and the v4 ones still are. **Arch:** ADR-0029, ADR-0033. **Needs:** P3-14, P3-10
**Files:** `review/repository.py` (`SUPPORTED_SCHEMA` 6→7, an account-equity endpoint), `review/app.py`, `ui/src/`.
**Accept when:** starting equity plus the summed contributions equals account equity within rounding tolerance; a v4 campaign still renders; INV-79 and INV-80 unchanged; the full quality gate green.

**Phase-3 gate:** fixtures prove the joint-account path and the real-data preflight passes. **No real campaign opens inside this phase** — its shape is a separate decision, and the headroom is 1,185 trials.

---

## Phases 3b–6 — milestones (break into tasks when the phase starts)

| Phase | Milestones | Arch |
|---|---|---|
| **3b** Breadth (2–3 wk) | 15–30 weakly-correlated instruments; `collaborative` engine mode; no instrument > 20% of risk | §3.1.11, §3.4 |
| **4** IB → forex + intl equities (3–4 wk) | IB adapter via Nautilus; sessions/calendars; Stooq data source; indices/ETFs only (survivorship) | §3.5, §6.1 |
| **5** Futures (5–7 wk) | Own roll module (ratio adjustment, OI/volume roll) from TurtleTrader data; match a reference source | §6.1 |
| **6** SSI → VN30F1M (3–6 wk) | InstrumentProvider + DataClient + ExecutionClient; reconciliation on reconnect with open positions | §3.5 |

Before the first holdout opening: **D4 must be decided** (arch §10).
