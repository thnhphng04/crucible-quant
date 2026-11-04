# Forex Bollinger + RSI notebooks on 1h bars: frozen before any 1h run (2026-10-21)

Dev: the 9 used pairs (AUDCAD AUDNZD NZDCAD EURUSD EURGBP EURCHF AUDCHF NZDCHF CADCHF), timeframe 1h, fee bars 8,
entry fee 0.0002, holding fee 0.00001 per 8h, MAX_FEE_R 0.2 (the scripts' rule), notebook grids and rankings,
max holding 288 bars, min trades 50, schedules 3y/1y and 18m/6m; strategies short_bollinger+RSI+trend,
short_bollinger+RSI, short_bollinger+RSI+fade, long_bollinger+RSI+fade.

Criteria (the transfer bar's grid median degenerates when most sets barely trade, so it is reported, not used):
top-10 mean test R/month > 0, top-10 cross-pair mean positive in >= 2/3 of windows, top 25% mean > 0.

A strategy that meets them on dev 3y/1y is run once on the untouched JPY crosses EURJPY GBPJPY AUDJPY CADJPY NZDJPY
(both schedules, same settings); it passes only if it meets the same criteria there on both schedules.
