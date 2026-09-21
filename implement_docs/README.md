# Implementation docs

How to **build** Crucible Quant. The design itself — what to build and why — lives in the architecture ([EN](../research_docs/Architecture_Design.md) · [VI, source of truth](../research_docs_vi/Architecture_Design.md)). These docs don't restate it; they point to its sections (`§3.4`) and add only what the architecture leaves open: task order, acceptance tests, code conventions, and implementation-level decisions.

English is the original; [implement_docs_vi/](../implement_docs_vi/README.md) is a 1:1 Vietnamese translation (same line count and headings, checked by `scripts/check_doc_mirror.py`).

| File | Use it when |
|---|---|
| [01-ROADMAP-TASKS.md](01-ROADMAP-TASKS.md) | Choosing the next task. Phases 0–1 are broken into tasks with acceptance tests; phases 2–6 are milestones to be broken down when the phase starts |
| [02-CONVENTIONS.md](02-CONVENTIONS.md) | Writing any code: layout, typing, determinism, time, money, errors, tests, dependencies |
| [03-INVARIANT-TEST-MAP.md](03-INVARIANT-TEST-MAP.md) | Checking that a rule from the architecture is actually enforced — every invariant → mechanism → test |
| [04-MODULE-MAP.md](04-MODULE-MAP.md) | Deciding where code goes and what it may import |
| [05-OPEN-ITEMS.md](05-OPEN-ITEMS.md) | Hitting an unknown. Each item names the task that resolves it |
| [adr/](adr/) | Recording an implementation decision (`/new-adr`). Architecture-level decisions stay in the arch §10 register |

Agent instructions: [../CLAUDE.md](../CLAUDE.md). Claude Code helpers: `/phase-check`, `/sync-docs`, `/new-adr` (in `.claude/commands/`).

## Keeping these docs honest

- When a task lands, tick it in `01`, fill its test names into `03`, and close any `05` item it resolved — in the same change.
- Every edit goes into both languages in the same commit: English first, then `implement_docs_vi/` line for line.
- A test named in `03` must exist and pass before the row is marked ✅.
- If implementation shows the architecture is wrong, don't patch around it: open an item in `05` and raise it with the user.
