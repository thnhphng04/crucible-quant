---
description: Report implementation progress against the current phase's tasks, invariants and phase gate
---

Assess where implementation stands. Read-only — change nothing.

1. Read `implement_docs/01-ROADMAP-TASKS.md` and `implement_docs/03-INVARIANT-TEST-MAP.md`. The current phase is the lowest one with a task not marked ✅ (or the phase given in the arguments: $ARGUMENTS).
2. For each task in that phase, check the code, not the checkbox:
   - do the listed files exist and contain a real implementation (not a stub)?
   - do the tests named in "Accept when" and in the invariant map exist?
3. Run `uv run pytest -q -m "not docker and not network"` and `uv run lint-imports`; note failures. If Docker is running, also run `uv run pytest -q -m docker`.
4. Report in Vietnamese, compactly:
   - a table: task id · status in the doc · actual status (done / partial / not started) · evidence (file or test);
   - mismatches between the doc's checkboxes and reality;
   - invariants for this phase whose test is missing or failing;
   - whether the **phase gate** is met, and if not, the shortest path to it (next 1–3 tasks, respecting `needs`);
   - open items in `05-OPEN-ITEMS.md` that block the next tasks.
