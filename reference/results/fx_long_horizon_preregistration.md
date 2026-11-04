# Forex holding up to 4 weeks: long-horizon anchored grid vs daily momentum (frozen 2026-10-21, before any run)

Data: histdata.com 1-minute bid bars resampled to 1h (EST + 5h = UTC), 2005 (or the pair's first year) -> 2026-10.
Dev: AUDCAD AUDNZD NZDCAD EURUSD EURGBP EURCHF AUDCHF NZDCHF CADCHF EURAUD EURNZD GBPNZD GBPCAD GBPCHF.
Confirmation (once, only for a strategy that passes dev): EURJPY GBPJPY AUDJPY CADJPY NZDJPY.
Costs: each pair's FXCM median bid-ask spread as a constant spread + commission 35 USD per million per side
(~0.7 pip per round trip), swap -1%/year on both sides.

A. Long-horizon anchored grid (engine.anchor_grid_kernel on 1h bars). Anchor = 120-trading-day mean of daily
closes; sigma_m = 60-day std of daily log returns x sqrt(21) (both known at the previous day's close). At the first
bar of a day, if the previous close is >= 1 sigma_m from the anchor, open toward the anchor. Step 0.5 sigma_m;
lots 1 / 1.5 / 2 / 2.5 / 3; adds at a 1h close beyond the next level, >= 48 bars after the last fill; each lot takes
profit 1 step from its fill (LIFO); all close at the anchor, at the stop 5 steps from the entry (risk 1% of the
pair's sleeve), or after 480 bars (~4 weeks). Sleeves per pair; portfolio = mean of sleeve daily returns.
Variants (reported, not picked): equal lots, entry at 1.5 sigma_m, 250-day anchor, and (added before any run, at the
user's question about martingale) Waka-style doubling lots 1 / 1 / 2 / 4 / 8, risk at the stop still 1%.

B. Daily time-series momentum (the opposite hypothesis). Each pair: position = sign of the 63-day return, sized to
10% annual volatility (60-day), rebalanced every Monday; costs on turnover (half spread + commission per unit
traded) and swap on the gross position. Portfolio = mean of pair returns. Variants: lookbacks 21, 126, 252 days.

Null for both: whole calendar weeks permuted within each calendar year (keeps within-week structure and each
year's drift, destroys multi-week mean reversion or trend), 20 runs, core version.

PASS on dev, per strategy (core): daily Sharpe >= 0.5, max equity DD <= 25%, positive in >= 2/3 of calendar years and
in >= 2/3 of pairs, Sharpe > p95 of the null. Confirmation: Sharpe >= 0.5, >= 2/3 of years, >= 3/5 pairs positive.
