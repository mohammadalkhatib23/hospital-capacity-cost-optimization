# Monthly-origin verification

- **PASS** Registered sources, plans and published score hashes. Rolling config `8715467371b89f2f10a0124613ce7387bdc9296d9f05026c0a91b6e63ca85e48`, frozen schedules `f797670d1363ffd9fe3256854b4b6cce98a2a52fd0949c44ff64dd139dae54ff`, Rule config `b95a791442544a9a93ccfa9f0904220c4571b85f2b51eb21a278d318edba7c90` and Rule schedules `1dea041a3fdf0828c7bc3e25b48b8accf7c953f881ec9ead511865e8f1b7cc03`. Scores match the saved input manifests. Only studies using the registered October 2026 workbook were eligible.

- **PASS** Source workbook, nonblank observations and target windows. Workbook SHA-256 `a1afbe8659e4ad984628286935a957d51cbcd9099e96edf4906e671d116151cb`. 71 complete months through October 2026 and 5 trailing blank months. All training and target months are present.

- **PASS** Origin cutoffs and calibration history. All 13 banks use only complete six-month paths with targets observed by their planning origin. Every backtest uses the registered 24-month window. The 11 common origins match the frozen forecasts' fit logs and their saved calibration banks.

- **PASS** Partial origins and reproducibility before scoring. October 2025 (occupied) and January 2026 (available) retain the registered SARIMA-24 convergence failures. Raw workbook histories were truncated at each origin before fitting the other four methods. Two runs reproduced all forecasts, 24 deployable schedules and flows within 1e-6 beds. Frozen schedule SHA-256 `464be3976284b2ad6ed313b817a5d58f0c949e594e9bcd464a6d88a56a4cc01f`.

- **PASS** Reuse of saved scores and reproducibility of new scores and Hindsight. Eleven complete origins reused their saved forecasts, schedules and scores. All published actuals agree with the required workbook. The two partial origins' monthly scores, totals and Hindsight schedules reproduced within 1e-6 relative tolerance.

- **PASS** Feasibility, monthly accounting, coverage and Hindsight bound. 63 method-origin forecasts, 189 deployable plans and 13 Hindsight plans. All 1,212 month-by-plan rows passed date, flow, capacity, ramp, shortfall, spare and cost checks. Largest relative monthly-sum discrepancy 2.82e-16.

- **PASS** Reproduction of the two published case studies. September 2025 and March 2026 match the published forecasts, actuals, 32 schedules and six-month scores at 1e-6 relative tolerance. All 13 original README tables and the case-study Answer also match.

- **PASS** Published numeric files and availability register. Six assembled data, calibration-log and new-plan files have verified SHA-256 digests in `data.sha256`. All costs use the registered illustrative prices (validated: False).

All Phase 1 checks passed.
