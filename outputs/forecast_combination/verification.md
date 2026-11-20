# Forecast combination verification

- **PASS** Registered design, raw_v6 workbook, imported specifications, saved individual forecasts, calibration banks, plans and scoring source manifests verified. Config SHA-256 `a04097b8ea7e34bcbde295b4cf5236312922ca27b33c235df84ade43613097d4`.

- **PASS** Toy tests for the combined forecast, paired bank and four-method fallback. 3 passed in 0.63s.

- **PASS** Forecast linearity, matched backtest keys, paired series, shared sample sizes and origin cutoffs. Complete origins reproduce individual saved 0.9 quantiles. All combined schedules are feasible and frozen before scoring. Plans SHA-256 `e3fc9483baf463940ae3ac5233099d77213f1550a043a62733818fc20e3bfb8b`.

- **PASS** All 726 existing six-month schedules and scores including Hindsight reconcile to frozen decisions or saved Hindsight schedules, actuals and monthly accounts. Common individual LP and Protected totals match both source studies exactly.

- **PASS** The frozen file yielded 185 new schedules. Their monthly costs sum to their totals at the stated penalty, all plans respect Hindsight and the workbook agrees with the saved actuals. The config and plans hashes match before and after scoring.

All Phase 2 checks passed.

## Publication checks

- **PASS** An independent calculation from the frozen forecasts, quantiles, actuals and costs reproduced E1–E6, the pairwise counts, the sensitivity results and the P1/P3 decisions.
- **PASS** The published section reproduced from the saved outputs. The earlier README content was preserved. All four PNGs have 300-dpi metadata, each SVG is readable, and the 13-origin heatmap annotations match the saved Protected excess costs.
