# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-09-24
- Primary product surfaces: local research-review web app for Phase 0–2.
- Evidence reviewed: `implement_docs/01-ROADMAP-TASKS.md`, ledger schema and records, CLI,
  comparison/monitor code, archived strategy and measurement formats. No previous UI, brand kit,
  screenshots or component library existed.

## Brand
- Personality: precise, calm, technical and candid about uncertainty.
- Trust signals: immutable identifiers, snapshot timestamps, explicit evidence and missing-data states.
- Avoid: trading-terminal theatrics, profit-first language, unexplained red/green scores.

## Product goals
- Goals: explain what a campaign established, why it has or lacks a verdict, and let reviewers trace
  every summary to candidates, gates and audit evidence.
- Non-goals: run research, edit configuration, freeze/abandon campaigns, open holdout, or trade.
- Success signals: a reviewer can understand a campaign without reading raw logs and opening the UI
  never changes the ledger.

## Personas and jobs
- Primary persona: the repository owner reviewing local experiments.
- User jobs: compare engines, inspect rejected candidates, audit calibration and portfolio history.
- Key contexts of use: desktop browser beside an IDE while research may still be writing the ledger.

## Information architecture
- Primary navigation: campaign selector, then Overview, GP ↔ Random, Candidates, Portfolios, Archive,
  Audit & lock.
- Core routes/screens: `/`, `/campaign/:id/:tab`, `/campaign/:id/candidate/:candidateId`.
- Content hierarchy: verdict and evidence first, aggregate metrics second, raw records last.

## Design principles
- Evidence before interpretation: distinguish facts, warnings and decisions.
- Missing is not zero: unavailable data is always labelled “Chưa có”.
- Read-only by construction: the browser exposes no mutating action.
- Tradeoff: dense desktop analysis takes priority over a phone-first layout.

## Visual language
- Color: charcoal surfaces; blue for GP, violet for random; green/amber/red only for state.
- Typography: system sans for prose and tables; monospace for IDs, hashes, code and numeric values.
- Spacing/layout rhythm: 4/8px scale, compact tables, 16–24px panel padding.
- Shape/radius/elevation: 8px panels, subtle borders, no decorative shadows.
- Motion: short opacity transitions only; respect reduced motion.
- Imagery/iconography: inline symbols with text labels; charts are data, not decoration.

## Components
- Existing components to reuse: none.
- New/changed components: shell, campaign selector, status badge, metric card, data table, SVG line/bar
  chart, gate timeline, filter bar, empty/error/loading state and code viewer.
- Variants and states: pass, reject, error, pending, warning and neutral.
- Token/component ownership: CSS custom properties and React components under `ui/src`.

## Accessibility
- Target standard: WCAG 2.1 AA.
- Keyboard/focus behavior: visible focus ring, semantic links/buttons, tab-accessible navigation.
- Contrast/readability: status always includes text; table headers and axes remain readable at 200%.
- Screen-reader semantics: landmarks, headings, captions and accessible SVG labels.
- Reduced motion and sensory considerations: no flashing; disable transitions under reduced motion.

## Responsive behavior
- Supported breakpoints/devices: desktop 1440px and compact desktop/tablet 1024px; usable below 760px.
- Layout adaptations: sidebar becomes a top block, cards become one column, tables scroll horizontally.
- Touch/hover differences: all essential information is visible without hover; tooltips duplicate labels.

## Interaction states
- Loading: skeleton-style panels with explicit loading text.
- Empty: explain whether the campaign has no data or the current filter matched nothing.
- Error: show a Vietnamese cause and retry action without hiding available navigation.
- Success: timestamp the last successful snapshot.
- Disabled: no disabled write controls because write controls do not exist.
- Offline/slow network: preserve current data and show refresh failure.

## Content voice
- Tone: direct Vietnamese, explanatory without claiming statistical certainty.
- Terminology: keep metric names (Sharpe, PBO, DSR, N_raw, N_eff) with Vietnamese explanations.
- Microcopy rules: gate-④ rate is “tỷ lệ qua ④”, never investment performance; OPEN is campaign state,
  never evidence that a process is running; “Chưa kết luận” is never styled as a loss.

## Implementation constraints
- Framework/styling system: React, TypeScript, Vite, React Router and CSS Modules; FastAPI/Uvicorn.
- Design-token constraints: one dark theme in v1, defined as CSS variables.
- Performance constraints: server pagination, 5-second polling only while the tab is visible.
- Compatibility constraints: bind localhost only; SQLite is opened with `mode=ro` and `query_only`.
- Test/screenshot expectations: API immutability tests, component/build checks, Playwright desktop smoke and
  screenshots at 1440px and 1024px.

## Open questions
- [ ] Revisit authentication and multi-user behavior only if the app is ever exposed beyond localhost.
- [ ] Add light theme only after the dark review workflow is stable.
