# Verification (Phase 4)

Result: **ALL CHECKS PASS** (18 of 18).

Numerical tolerances: 1e-06 relative for costs, 1e-06 beds for forecasts and plans. Versions: python 3.10.11, numpy 2.2.6, pandas 2.3.3, scikit_learn 1.7.2, statsmodels 0.14.6, pulp 3.3.2.

| # | Check | Result | Detail |
| ---: | --- | --- | --- |
| 1 | Unit tests (pytest scripts/clean_checkpoint) | PASS | 29 passed in 5.77s |
| 2 | Config and plan hashes unchanged; config matches the code | PASS | config 8406460d682a6d53..., plans 7269b6c2aed2186d...; workbook SHA-256 matches SOURCE.json |
| 3 | No training window, fit or feature uses data after the cut-off | PASS | logged dates of every window, SARIMA label, ExtraTrees row and Seasonal naive source are <= the cut-off and inside the 24-month window; the forecasting code refuses a frame that holds later months |
| 4 | SARIMA-24 converged (4 of 4 fits, 24 labels); ExtraTrees-24 has 12 training rows (4 of 4 fits) | PASS | converged 4/4; training rows [12, 12, 12, 12] |
| 5 | Seasonal naive forecasts equal the actuals one year earlier | PASS | largest difference 0 beds (24 values) |
| 6 | Every plan satisfies all constraints (22 plans) | PASS | 0 <= x <= 15,000; 0 <= a, r <= 5,000; x_t = x_(t-1) + a_t - r_t from x_0 = 0; largest violation 0 |
| 7 | Scored plans are the frozen plans | PASS | x, a and r of the 20 forecast-based plans are bit-identical to plans_frozen.csv |
| 8 | Monthly costs sum to totals; costs recomputed independently from x and the actual gaps | PASS | largest relative difference in a total 1.9e-16; largest monthly difference 0 units; components add up within 1.9e-09 units |
| 9 | LP planned cost <= Rule planned cost for every method (cost against the forecast gap) | PASS | largest (LP - Rule) / Rule = 1e-07 over 10 method-origin pairs; tolerance 1e-06 |
| 10 | LP realised cost <= Rule realised cost for every method | PASS | largest (LP - Rule) / Rule = 5.6e-08; tolerance 1e-06. Strictly: Mean-24 at O2_mar2026: LP higher by 0.79 units (5.6e-08 of cost) (see note 1) |
| 11 | Hindsight cost <= every plan's cost | PASS | smallest (plan - hindsight) = 0.872m |
| 12 | Accuracy recomputed independently from forecasts and actuals | PASS | largest difference 0 beds (MAE and mean signed error, 3 series) |
| 13 | Decision rules recomputed independently | PASS | joint best {'O1_sep2025': ['Seasonal naive LP', 'SARIMA-24 LP'], 'O2_mar2026': ['Persistence Rule', 'Persistence LP']}; overall first SARIMA-24 LP, runner-up ExtraTrees-24 LP; clear winners none; best forecast SARIMA-24 (clear: False) |
| 14 | Improvement measures recomputed independently (30 rows: per origin and combined) | PASS | largest relative difference 0 |
| 15 | LP plans agree with a second solver (HiGHS) | PASS | 12 LPs (10 forecast-based, 2 hindsight): largest relative objective difference 1e-07; largest plan difference 4.8e-05 beds |
| 16 | Rerunning the pipeline gives identical results (ExtraTrees within 1e-6 beds) | PASS | fresh run of all three stages: 2 of 13 CSV files byte-identical; largest difference 4.4e-11 beds in forecasts and plans, 7.6e-13 relative in any number; config hash, rankings and decisions identical; frozen-plan hash differs (ExtraTrees float noise) |
| 17 | Cross-check: Origin 1 against the earlier Sep 2025 results (outputs/sep2025_comparison/) | PASS | largest relative difference in a total 9.7e-15 (ExtraTrees-24 Rule); tolerance 1e-06; details below |
| 18 | README.md equals the text generated from outputs/clean_checkpoint/ (every number comes from the saved results) | PASS | README.md matches the text generated from outputs/clean_checkpoint/ (166 lines) |

## Cross-check against the earlier September 2025 results

The earlier files were produced by the project's own code (scripts/compare_methods_sep2025.py and the project modules) on the September 2026 workbook. This checkpoint uses new code and the October 2026 workbook. Largest differences at Origin 1:

| Item | Largest difference |
| --- | ---: |
| Actuals (occupied, available, gap) | 0 beds |
| Forecasts, Mean-24 | 0 beds |
| Forecasts, Persistence | 0 beds |
| Forecasts, Seasonal naive | 0 beds |
| Forecasts, SARIMA-24 | 0 beds |
| Forecasts, ExtraTrees-24 | 2.91e-11 beds |
| Plans x_t (10 plans and hindsight) | 2.91e-11 beds |
| Monthly costs | 1.15e-07 units |
| Totals (relative) | 9.72e-15 (ExtraTrees-24 Rule) |
| Cost components | 2.92e-07 units |

No difference in a total is above 1e-6 relative, so no explanation is needed and neither version shows an error.

## Notes

1. The LP solver (CBC through PuLP) returns plan values with 8 significant digits, so an LP plan can differ from the exact optimum by up to about 5e-5 beds. For the flat forecasts (Mean-24, Persistence) the Rule plan and the LP plan are the same plan; their costs differ by less than 1 model unit in more than 10 million. decision.json lists these cases strictly (lp_worse_than_rule: ['Mean-24 at O2_mar2026']). Only the order inside those two pairs depends on this rounding; no joint-best set, no position against another forecast and no decision does.
2. LP beds minus Rule beds, month by month: the LP plan is never below the Rule plan by more than 4.8e-05 beds. With shortfall at 5,000 and at most 1,350 to open, run and close a bed for one month, the LP covers the forecast gap whenever the Rule does, which is why the realised check holds here although it is not guaranteed in general.
3. SARIMA warnings recorded in fit_log.json: UserWarning: Non-stationary starting autoregressive parameters found. Using zeros as starting parameters.; ValueWarning: No frequency information was provided, so inferred frequency MS will be used..
