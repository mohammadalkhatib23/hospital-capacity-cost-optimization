# Verification (Phase 4)

Result: **ALL CHECKS PASS** (17 of 17).

Numerical tolerances: 1e-06 relative for costs, 1e-06 beds for forecasts and scenarios, 0.001 beds between solvers.

| # | Check | Result | Detail |
| ---: | --- | --- | --- |
| 1 | Unit tests (pytest scripts/fair_comparison): scenarios, pinball loss, service level, SAA | PASS | 19 passed in 7.78s |
| 2 | Config and plan hashes unchanged; config matches the code | PASS | config 164f4ba712b15492..., plans a3a0d180b952032c...; workbook SHA-256 matches SOURCE.json |
| 3 | Leakage: every backtest target month and every training month is at or before the origin | PASS | {'O1_sep2025': 28, 'O2_mar2026': 34} error paths; all targets <= origin, all 24-month windows end at the backtest origin and start on or after Dec 2020; the bank builder refuses a frame that holds later months |
| 4 | Backtests recomputed independently (Persistence, Seasonal naive, Mean-24; actuals; paired gap errors) | PASS | largest difference 0 beds |
| 5 | Scenarios are the point forecast plus the method's own error paths; quantiles follow the rank rule | PASS | largest difference 0 beds; ranks O1_sep2025: {'0.5': 15, '0.8': 24, '0.9': 27, '0.95': 28}; O2_mar2026: {'0.5': 18, '0.8': 28, '0.9': 32, '0.95': 34} |
| 6 | With protection removed, point forecasts and LP plans reproduce the clean checkpoint | PASS | Mean-24, Persistence, Seasonal naive, SARIMA-24: identical (difference 0); ExtraTrees-24: 2.9e-11 beds (allowed 1e-6) |
| 7 | All plans feasible (128 plans); scored plans are the frozen plans | PASS | 0 <= x <= 15,000; ramps <= 5,000; x_t = x_(t-1) + a_t - r_t from zero; largest violation 0; frozen x bit-identical |
| 8 | Monthly costs sum to totals; costs recomputed independently at each plan's penalty | PASS | largest relative difference 3.5e-16 |
| 9 | Hindsight cost <= every plan's cost (each penalty level has its own hindsight) | PASS | smallest (plan - hindsight) / hindsight = 0.00443 |
| 10 | Protected plan is never worse than the point plan on its own scenarios (40 pairs) | PASS | largest (SAA - point) / point average scenario cost = -5.21e-06 |
| 11 | Service-level plans meet the required quantile every month (20 plans) | PASS | largest shortfall against the requirement 4.8e-05 beds; months where the requirement was lowered to the reachable level: 14 |
| 12 | All plans agree with a second solver (HiGHS; 128 programs) | PASS | largest relative objective difference 2.2e-07; largest plan difference 5e-05 beds |
| 13 | Gap MAE, pinball loss, coverage and CRPS recomputed independently | PASS | largest difference 5.4e-13 beds |
| 14 | Decision rules recomputed independently (winner, previous winner, protection effect, sweep, calibration) | PASS | protected joint best {'O1_sep2025': ['ExtraTrees-24'], 'O2_mar2026': ['Seasonal naive']}; overall order ['Seasonal naive', 'ExtraTrees-24', 'SARIMA-24', 'Mean-24', 'Persistence']; outcome: no clear winner; sweep outcomes p=1000: Mean-24, p=2500: no clear winner, p=5000: no clear winner, p=10000: Seasonal naive; best calibrated ExtraTrees-24 (clear: False) |
| 15 | Improvement measures recomputed independently (15 rows: per origin and combined) | PASS | largest relative difference 0 |
| 16 | Rerunning the three stages gives the same results (ExtraTrees within 1e-6 beds) | PASS | 10 of 25 CSV files byte-identical; largest difference 6.1e-11 beds in forecasts, errors, scenarios and plans, 4e-12 relative in any number; config hash, rankings and decisions identical; frozen-plan hash identical |
| 17 | README.md equals the clean-checkpoint text plus the section generated from outputs/fair_comparison/ | PASS | README.md matches the clean-checkpoint text plus the generated fair-comparison section (276 lines) |

## Notes

1. Backtests dropped because SARIMA-24 did not converge (dropped for all five methods, as registered): {'O1_sep2025': ['2023-01'], 'O2_mar2026': ['2023-01']}. The config expected 29 and 35 paths; the banks hold 28 and 34.
2. The LP solver (CBC through PuLP) returns plan values with 8 significant digits, as in the clean checkpoint.
3. Inside the SAA program 0.0001 per bed-month is added to the running cost so that equally cheap plans resolve to the smaller one. Reported costs do not include it.
4. ExtraTrees-24 fits differ between runs by about 1e-11 beds (parallel summation), so the frozen-plan hash can differ between runs.
5. The clean-checkpoint README check compares the whole README with the checkpoint text. After this section is appended, the check that applies is the one above: README.md must equal the unchanged checkpoint text followed by the generated section.
