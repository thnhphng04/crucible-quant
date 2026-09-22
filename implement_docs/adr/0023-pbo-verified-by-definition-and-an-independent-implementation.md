# ADR-0023 — PBO verified by its definition and an independent implementation

- **Status:** Accepted (decided by the user)
- **Date:** 2026-09-22
- **Task:** P1-03, phase-1 gate · **Arch:** §7 (phase 1), §6 note on `purgedcv` · **Open item:** closes O18

## Context

The phase-1 gate asked DSR and PBO to match the numerical examples in the original papers. DSR is tested against a published numerical example. PBO had no published fixture: `test_pbo_reference_example` is computed by hand from the CSCV definition of Bailey et al. (2017), and `test_matches_purgedcv` compares with another library — which the previous wording overstated as "matches the paper".

## Decision

1. **Gate wording (arch §7, edited VI first, then EN):** DSR matches a published numerical example in the paper; PBO matches hand-computed fixtures from the CSCV/PBO definition and the result of an independent implementation on the same input.
2. **Hand-computed fixtures** have fixed expected values and explain how they were computed (`tests/validation/test_pbo.py::test_pbo_reference_example`: two S = 2 cases, PBO = 1 and PBO = 0, with the logits).
3. **The cross-check records its reference version:** `PURGEDCV_VERSION = "0.1.6"` in `tests/validation/test_pbo.py`; the test fails if another version is installed, so a new reference is reviewed before it is trusted.
4. **Independence is checked, not assumed:** `validation/pbo.py` imports numpy/scipy only — no `purgedcv`, no project module (`test_pbo_is_independent_of_purgedcv`). If our PBO ever called `purgedcv`, comparing with it would not count as verification.
5. A published worked example may be added later as a further fixture.

## Consequences

- The phase-1 gate is met for PBO under the stated wording; the roadmap and INV-41 cite it.
- Upgrading `purgedcv` needs a deliberate change of the pinned version in the test.

## Alternatives considered

- **Find a published worked PBO example first:** not required to close the gate; can be added later.
- **Keep the old wording:** rejected — it claimed more than the tests show.
