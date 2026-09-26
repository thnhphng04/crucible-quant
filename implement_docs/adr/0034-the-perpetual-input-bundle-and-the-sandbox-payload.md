# ADR-0034 — The perpetual input bundle and the sandbox payload

- **Status:** Accepted
- **Date:** 2026-09-26
- **Task:** P3-18 · **Arch:** §3.3.3, §3.5, §6.1 · **Adds:** INV-99

## Context

Every module built for perpetual trading in P3-05 … P3-13 — bracket margin, the isolated-wallet account, intrabar path summaries, admission, the joint replay — is imported by nothing outside its own cluster. The reason is one line: `SandboxJob.bars` is documented as *"the only data the container will see"*, and it carries trade bars alone. A backtest inside the container cannot receive a mark price, a funding rate or a bracket table, so no gate can run perpetual mechanics, so nothing calls any of it.

Widening that channel is the unblocking change for P3-19 through P3-23. It is also the one place where a mistake is invisible: a missing series raises, but a **desynchronised** one does not. A mark series cut one bar differently from the trade series marks every position at its neighbour's price for the rest of the run, and no error is produced anywhere.

## Decision

1. **Four series travel as one object.** `core.perp_inputs.PerpBundle` holds the mark bars, the funding table, one intrabar path per bar and the leverage brackets, for one contract. There is exactly one windowing operation, `PerpBundle.slice(start, stop)`, and it moves all four. There is no API that cuts one of them.
2. **One set of conventions, stated once.** Timestamps are nanoseconds, UTC, labelling a bar's **close** time, as `Bars` does. Windows are half-open `[start, stop)`, as `Bars.slice` is. `brackets` is a campaign constant, not a window, so slicing carries it through unchanged.
3. **`bar_ix` is renumbered on every slice.** The funding table indexes the bundle it belongs to, so a sliced bundle cannot be read with an unsliced one's indices. The alternative — a raw timestamp the caller re-resolves — was rejected because getting it wrong charges funding to a bar offset by the slice, which changes results and produces no error.
4. **Several funding events inside one bar stay separate rows.** Funding cadence can change over time or by contract. Summing events to one rate per bar would hide the liquidation-price move each settlement causes mid-bar, which is exactly what ADR-0032 decision 6b cut the path summaries into segments to capture.
   A funding event at 08:00 uses the mark **close of 07:59**, the last completed minute at settlement. The 08:00 minute close is future information. An event exactly at the requested window's start has no prior minute in that window and is excluded; no position can have opened within the window before it.
5. **Alignment is checked, twice.** `assert_aligned` runs on the host inside `prepare`, where the traceback is readable, and `load_inputs` checks again inside the container, because the container is the thing that must not proceed on desynchronised data.
6. **The bundle is required for `backtest` and `grid_backtest`, and its absence fails closed** with a message naming the contracts it is missing for. `signals` and `leak_check` read direction and stop distance only and never price a position, so requiring it there would stage megabytes for jobs that never look at it.
7. **The requirement is read off the symbol, not off a config flag.** A symbol containing `:` is a ccxt perpetual and needs a bundle; `BTC/USDT` is spot and does not. So the rule cannot be switched off by a lock that forgot to mention the market, and the legacy spot path — which has no mark price and no funding to model — keeps working unchanged.
8. **All four series go to files; `job.json` records only their names.** The original plan put the brackets in `job.json` as an optimisation. One mechanism is easier to verify than a parquet-and-JSON split, and the brackets are a few rows, so the saving was not worth a second path. `options` is JSON-serialised, so numpy would have had to be converted by hand there anyway.
9. **The path table is long, not nested** — one row per breakpoint, `(bar_ix, segment_ix, start_minute, side, minute, price)` — because that is what parquet stores well and what reconstructs without a bespoke codec. `side` is 0 for the running minimum and 1 for the running maximum.
10. **Mark bars are never routed through `job.bars`.** `job.bars` flows straight into `build_engine`, so a mark series placed there would be added to the venue as a **tradable instrument**. Sidecars only.

## Consequences

- **INV-99: a raw minute series never enters a container.** The summaries exist for that, and a test asserts it over the staged files rather than trusting the writer.
- **`source_hash` covers all of `src/`, so this change rebuilds the sandbox image once.** The first gate run after it pays a silent multi-minute `docker build`, which is worth pre-building in a smoke step rather than discovering inside a timed job.
- **`load_inputs` is now a 4-tuple and `Job` takes the bundle via `job["perp_inputs"]`.** That is the runner's public shape, and widening it touched all four job functions.
- **The payload grows by the mark bars, the funding table and the path table.** Measured on simulated minute paths that is ~1.6 MB per instrument-series over 1,840 bars; it must be measured again on real Binance data in P3-19, because real microstructure produces more breakpoints than a random walk and a strongly trending day pushes one side to 200–400.
- **`prepare` still enforces exactly one timeframe** across a job's bars. The sidecars are not a second timeframe, so that constraint is unaffected.
- **Nothing calls this yet.** P3-19 and P3-21 are what make the gates use it; this ADR records the contract they will be built against.

## Alternatives considered

- **Pass mark bars through `job.bars` with a naming convention.** Rejected under decision 10: the venue would trade them.
- **Ship minute closes instead of summaries** (~21 MB per instrument, ~15 MB compressed, against a 2 GB container limit). Rejected: the size is survivable and the CPU cost is smaller than first assumed, but the summary is what makes the ambiguity rule exact, and it is what keeps the download and host storage bounded across the universe × two series × every campaign.
- **Keep the series as separate arguments to `SandboxJob`.** Rejected under decision 1: four independently sliceable arguments is precisely the shape in which a desynchronised cut is expressible.
- **Resolve funding by timestamp in the container rather than by `bar_ix`.** Rejected under decision 3. It moves a boundary decision into every call site, and each site would have to re-derive the same half-open rule.
