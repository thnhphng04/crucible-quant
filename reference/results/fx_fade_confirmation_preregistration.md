# Forex fade, max fee per R: frozen before the confirmation run (2026-10-21)

Dev (9 pairs AUDCAD AUDNZD NZDCAD EURUSD EURGBP EURCHF AUDCHF NZDCHF CADCHF, 15min, 3y/1y, entry fee 0.0002,
holding 0.00001 per 8h, notebook grids, r_martin_monthly ranking, min trades 50) picked, by the rule "highest top-10
test R/month among max fee 3% / 5% / 10% of R":

- short_bollinger+RSI+fade: MAX_FEE_R = 0.05 (dev top-10 +0.110, 7/9 pairs, 5/6 years; 3% gave +0.038, 10% -0.059)
- long_bollinger+RSI+fade: MAX_FEE_R = 0.03 (dev top-10 +0.029, 5/9 pairs, 4/6 years; 5% +0.001, 10% -0.076)

Confirmation, run once: EURAUD EURNZD GBPNZD GBPCAD GBPCHF (GBPAUD is not on FXCM and is not replaced), same costs
and settings, schedules 3y/1y and 18m/6m.

PASS per strategy and schedule: the harness's transfer bar (pairs with test grid median > 0 >= 2/3 and windows with
top 25% > 0 >= 2/3), top-10 mean test R/month > 0, and top-10 positive (cross-pair mean) in >= 2/3 of windows.
