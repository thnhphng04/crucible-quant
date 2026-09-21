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

### ☐ P0-08 Docker sandbox
- **Goal:** all generated code runs isolated.
- **Arch:** §3.3.3.
- **Files:** `validation/sandbox.py`, `docker/sandbox.Dockerfile`, `tests/validation/test_sandbox.py` (marker `docker`).
- **Details:** `docker run --rm --network none --read-only --tmpfs /tmp --memory … --cpus … --env-clear`-equivalent (pass only `PYTHONPATH`); IS data mounted read-only; output = one JSON `EvaluationReport`; stdout/stderr truncated; timeout kills the container. Violations → `SANDBOX_VIOLATION` event.
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

### ☐ P0-10 Backtest runner (NautilusTrader) — phase-0 subset
- **Goal:** run a `Strategy` on bars through NautilusTrader with the pessimistic fill model, so the same path serves live later (P5).
- **Arch:** §3.5 (pessimistic `FillModel`), §3.3.
- **Files:** `execution/engine.py`, `execution/nautilus_bridge.py` (Strategy → Nautilus strategy adapter), `tests/execution/`.
- **Details:**
  - Add `nautilus_trader` (pin; see O3).
  - Signals computed on a **closed** bar execute at the **next** bar's open — never on the bar that produced them (defeats oracle level 2).
  - Fees per venue schedule, fixed slippage in ticks, limit orders fill only on trade-through.
  - Sizing: until P1-06, a clearly named `PlaceholderSizer` (fixed notional × `strength`) that P1-06 deletes.
  - Output: returns series + trade list → `EvaluationReport.public`.
- **Accept when:** a deterministic golden test (fixed bars → exact trades/PnL); a test that a signal on bar *t* fills at bar *t+1*; a limit that only touches is not filled.
- **Needs:** P0-04, P0-09.

### ☐ P0-11 Leaky-oracle suite + gate ①b
- **Goal:** prove the harness catches cheating — the phase-0 gate.
- **Arch:** 07-VALIDATION-LAYER §9, §3.2 row ①b.
- **Files:** `validation/oracles/level{1..4}_*.py` (strategies in template form), `validation/guardrail.py` (dynamic part), `tests/validation/test_oracles.py`.
- **Details:**
  1. `shift(-1)` — expected to die at ①a (static).
  2. Uses the forming bar's close — dynamic check: re-run with the last bar's close perturbed; signals for that bar must not change.
  3. Repainting indicator (e.g. centered window / future pivot) — dynamic truncation test: run on data truncated at *t* vs full data; all signals `≤ t` must be identical.
  4. Late-published information (PIT error) — a synthetic feature available with delay *d* but joined at event time; caught by the truncation test on the joined feature. See O8.
- **Accept when:** each of the 4 oracles is rejected at the expected gate with a ledger row, and the EMA crossover passes ①b.
- **Needs:** P0-07, P0-08, P0-10.

### ☐ P0-12 Gates ② MinBTL + ③ IS backtest; end-to-end run
- **Goal:** close phase 0.
- **Arch:** §3.2 rows ②, ③; 07-VALIDATION-LAYER §4.4; D15.
- **Files:** `validation/statistical.py` (MinBTL only for now), `validation/gates.py`, `tests/e2e/test_phase0.py`.
- **Details:** MinBTL from the running `N`; gate ③ rejects `trades < min_trades`, average holding `< min_holding_bars`, indicator pairs with IS `|ρ| > max_indicator_corr`; from ③ on each candidate is a `trials` row.
- **Accept when:** `tests/e2e/test_phase0.py` runs the EMA crossover + all 4 oracles through ①a → ①b → ② → ③; oracles rejected, EMA recorded as a trial; the `ledger` shows the expected `generation_log` + `trials` rows. **Phase-0 gate met.**
- **Needs:** P0-11.

---

## Phase 1 — Full validation layer + Risk & Sizing (2–3 weeks)

> **Phase gate (arch §7):** the hand-written strategy clears every gate; the ledger separates audit/trials correctly; `N_eff` + `V[SR]` are computable; DSR/PBO match the numerical examples in the original papers; the ½-size test passes.

### ☐ P1-01 Verify `purgedcv` (first task of phase 1)
- **Arch:** §6 note, 07-VALIDATION-LAYER §7.
- **Goal:** decide *wrap* vs *implement ourselves* for DSR/PBO; keep `purgedcv` for CPCV/purging if sound.
- **Details:** read the installed source; pin the version; check whether DSR accepts `N` and `V[SR]` separately, and what PBO's input matrix and selection rule are.
- **Accept when:** an ADR records the decision with the checked signatures; O2 closed.

### ☐ P1-02 DSR (+ PSR)
- **Arch:** §3.1.6, §4.1, 07 §4.
- **Files:** `validation/statistical.py`.
- **Accept when:** matches the numerical example of Bailey & López de Prado (2014) to stated precision; monotonic tests (more trials ⇒ lower DSR; higher `V[SR]` ⇒ lower DSR); reports at both `N_raw` and `N_eff`.

### ☐ P1-03 PBO via CSCV + gate ④
- **Arch:** §3.2 ("What PBO actually tests"), D13.
- **Accept when:** reproduces the Bailey et al. (2017) example; simulation tests — pure noise matrix ⇒ PBO ≈ 0.5 (not → 1), a planted-edge configuration ⇒ PBO → 0; the configuration set comes from the TUNABLE grid + ledger variants, capped at `M`; gate ④ rejects PBO ≥ `pbo_max`.
- **Needs:** P0-05, P1-01.

### ☐ P1-04 CPCV with purging/embargo + IS→OOS curve
- **Arch:** §3.2 (degradation curve), 07 §3.
- **Accept when:** purging/embargo removes the overlapping observations on a hand-built fixture; CPCV-OOS median Sharpe goes into `EvaluationReport.private`, never `public`.

### ☐ P1-05 `N_eff` (ONC clustering)
- **Arch:** §4.1 ("Estimating `N_eff`").
- **Files:** `validation/n_eff.py`.
- **Accept when:** on synthetic trials built from *k* independent sources plus noise, the estimate recovers *k* (± tolerance); appends a clustering run to `trial_clusters`; see O10.

### ☐ P1-06 Risk & Sizing
- **Arch:** §3.4 (all), D7, D12.
- **Files:** `core/sizing/vol_target.py`, `core/sizing/position_sizer.py`; delete `PlaceholderSizer`.
- **Accept when:** **the ½-size test** — double both `vol_estimate` and ATR ⇒ size exactly halves when the stop cap does not bind; the stop cap binds as `min`, never as a multiplier; `round_to_lot` respects step/min/max; FX conversion to base currency; portfolio-level scaling back to `target_vol` with a leverage cap and IDM re-estimation.

### ☐ P1-07 Portfolio construction
- **Arch:** §3.2.1 steps 1–6, D9.
- **Files:** `validation/portfolio.py`.
- **Accept when:** each step unit-tested on a fixture (one per cell, `|ρ|` filter in DSR-rank order, cap K, naive risk parity, monthly rebalance); the `portfolio_hash` is deterministic and changes with any member/weight/rule change; each built portfolio is a `portfolio_variants` row.

### ☐ P1-08 Gate ⑤ — portfolio DSR
- **Arch:** §3.2 row ⑤, §3.1.6 DSR box, §4.1.
- **Accept when:** inputs come only from `trial_stats` + `total_portfolio_variants`; a test proves adding trials (any engine, any verdict) lowers the result; DSR is never computed per cell (no API for it).

### ☐ P1-09 Gate ⑥′ — data-source robustness + cost sensitivity
- **Arch:** §3.2 row ⑥′, §6.1 (two-tier data).
- **Accept when:** fees × 2 and slippage × 2 re-run; Sharpe > 0 and DSR within the campaign-locked drop; broker/second-source re-run within the 30% Sharpe-drop rule.

### ☐ P1-10 Holdout evaluator process + campaign state machine
- **Arch:** §4.2 (procedure, 3 layers), P6, D4.
- **Files:** `holdout/evaluator_proc.py` (own entry point), `holdout/campaign.py`.
- **Details:** separate process (CLI) that takes only `portfolio_hash`; checks campaign is `FROZEN`, `frozen_at < now`, `holdout.lock` hash matches; refuses if `holdout_pass` is unset (D4); writes `holdout_access`; prints exactly `PASS` or `FAIL`; moves the campaign to `BURNED`. Nothing else imports `quantcrucible.holdout` (import-linter).
- **Accept when:** tests on a **synthetic** holdout in a tmp dir: second opening raises; tampered holdout file ⇒ refuse; output is one token; `sharpe_oos` is stored but never printed.
- **Needs:** P0-02, P0-03, P0-09, P1-07.

### ☐ P1-11 Calibration (step 5b)
- **Arch:** §3.2.1 step 5b, §3.3.1 (optimizer off in the loop).
- **Accept when:** Optuna over TUNABLE bounds with a fixed budget; **every** evaluation is a `trials` row with `source='param_opt'`; PBO and DSR are recomputed afterwards.
- **Needs:** P1-03, P1-08.

### ☐ P1-12 End-to-end phase-1 run
- **Accept when:** `tests/e2e/test_phase1.py` takes the EMA crossover (and a few hand-written variants) through ⓪–⑥′, builds a portfolio, and freezes it; the phase gate above holds.

---

## Phases 2–6 — milestones (break into tasks when the phase starts)

| Phase | Milestones | Arch |
|---|---|---|
| **2** Agent loop, crypto only (6–8 wk) | Model routing + LLM client (only under `agent/`); prompt builder with the `private`-key leak test; Research / Coding / Evaluation agents; patcher (SEARCH/REPLACE + block rewrite); gate ⓪ drift (layers 1–2) + calibration of Δ thresholds; feature map with fixed bin bounds; islands + migration + island integration test; async pipeline; engines A/B/C + scheduler enforcing trial-budget shares; ≥ 150 autonomous generations; multi-engine comparison in `isolated` mode with the decision rule locked **before** running; Research-Agent model A/B | §3.1.x, §3.1.11, §3.3.1, §7 |
| **3** Breadth (2–3 wk) | 15–30 weakly-correlated instruments; `collaborative` engine mode; no instrument > 20% of risk | §3.1.11, §3.4 |
| **4** IB → forex + intl equities (3–4 wk) | IB adapter via Nautilus; sessions/calendars; Stooq data source; indices/ETFs only (survivorship) | §3.5, §6.1 |
| **5** Futures (5–7 wk) | Own roll module (ratio adjustment, OI/volume roll) from TurtleTrader data; match a reference source | §6.1 |
| **6** SSI → VN30F1M (3–6 wk) | InstrumentProvider + DataClient + ExecutionClient; reconciliation on reconnect with open positions | §3.5 |

Before the first holdout opening: **D4 must be decided** (arch §10).
