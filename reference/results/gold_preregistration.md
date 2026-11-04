# Gold (XAUUSD) Bollinger + RSI notebooks: frozen before any gold run (2026-10-21)

Data: histdata.com free 1-minute bid bars (EST + 5h = UTC, offset verified on forex), 2018 -> 2026, resampled by
the harness. No ask prices, so costs are assumed: entry fee 0.00015 of price per round trip (spread ~0.1-0.3 USD plus
ECN commission); holding fee per 8h 0.00001 for the short strategies (~1%/year), 0.00005 for long fade (~5.5%/year,
long gold pays a large swap). MAX_FEE_R 0.2 (scripts' rule), notebook grids and rankings, max holding 288 bars,
min trades 50.

Runs: short_bollinger+RSI+trend, short_bollinger+RSI, short_bollinger+RSI+fade, long_bollinger+RSI+fade x
timeframes 5min (fee bars 96), 15min (32), 1h (8) x schedules 3y/1y and 18m/6m = 24 runs, all reported.

PASS per run (one instrument, so no transfer bar): top-10 mean test R/month > 0, top-10 positive in >= 2/3 of
windows, top 25% mean > 0. Correlation of the top-10 with gold's window return is reported (gold rose ~3x).

A configuration (strategy x timeframe) that passes on either schedule is run once on silver (XAGUSD, same source and
settings); it is confirmed only if it passes there on the same schedule.
