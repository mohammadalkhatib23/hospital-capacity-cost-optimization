# Verification: fair comparison across many origins

**PASS ? 17 checks passed.** The authorized boundary and convergence corrections were made before any rolling scoring. Prior stops are preserved in [verification_history.md](verification_history.md).

## Corrections and registration

- The data end is the last month with BOTH G&A beds available and G&A beds occupied non-blank: October 2026. Five blank template rows are ignored; internal blanks are rejected.
- Registration `1d93530aa10a4a110c3f60f979617d50a7c1f0fc50775657ccecacdf08f2f15d` was superseded before scoring after the owner authorized excluding nonconvergent planning origins for every method.
- Both SARIMA targets were checked at all 27 otherwise-eligible origins using only their training windows. October 2025 failed for occupied beds; January 2026 failed for available beds. Both origins were excluded for all five methods. No model specification changed.
- Of 42 training/target candidates, 15 had fewer than nine retained error paths and two failed the planning-origin check. The final 25 origins run from February 2024 through April 2026, omitting the two convergence exclusions.
- Historical backtests January 2023 (available) and October 2025 (occupied) are dropped for all methods; the resulting banks contain 9?34 paths.
- A pre-planning export review ensured the monthly shortfall_cost column uses its own penalty. The inherited total and monthly_cost calculations already did so; no LP, scoring formula or decision rule changed. See registration_amendment.json for the interim and final code digests.

Final config SHA-256: `8715467371b89f2f10a0124613ce7387bdc9296d9f05026c0a91b6e63ca85e48`.
Frozen plans.csv SHA-256: `f797670d1363ffd9fe3256854b4b6cce98a2a52fd0949c44ff64dd139dae54ff`.

All 500 practical plans were written and hashed before new scoring began. Hashes were checked before and after scoring. The 75 hindsight plans were solved afterward. Later-origin calibration legitimately uses some earlier-origin target months; every individual fit and bank remains restricted to its own cutoff.

## Checks

| Check | Result | Detail |
| --- | --- | --- |
| Source, registered config and frozen plan hashes | PASS | config 8715467371b89f2f10a0124613ce7387bdc9296d9f05026c0a91b6e63ca85e48; plans f797670d1363ffd9fe3256854b4b6cce98a2a52fd0949c44ff64dd139dae54ff |
| Approved publication boundary | PASS | 71 complete observations; 5 blank trailing rows ignored; no internal blanks |
| Planning-origin exclusions before scoring | PASS | [{"origin": "2025-10", "failed_targets": ["occupied"]}, {"origin": "2026-01", "failed_targets": ["available"]}] |
| All frozen plans feasible | PASS | 500 six-month plans; cap, ramp, zero start, flows, dates and finiteness |
| Cutoff, training and shared-cache isolation | PASS | 25 origins and 532 reused whole paths checked; each path ends by its planning origin |
| Raw paired scenarios and 0.9 quantiles | PASS | All methods/origins match independent reconstruction; no centering or clipping |
| Identical-scenario Protected equals Point | PASS | ['2024-02', '2026-04']; max difference 0 beds |
| Preservation of pre-existing files | PASS | 212 original file hashes checked; README exemption=False |
| Original and rolling pytest suites | PASS | 61 passed, 3278 warnings in 20.23s |
| Independent accounting and hindsight lower bound | PASS | 575 plans including hindsight; max relative cost difference 3.8e-16; monthly costs reconcile |
| Forecast errors, pinball and coverage | PASS | Recomputed directly from actual observations and saved point/quantile forecasts |
| Fable origins reproduced inside full run | PASS | max relative primary protected-cost difference 0 |
| Complete reruns at two registered sample origins | PASS | max forecast 2.91e-11 beds; bank 5.82e-11; plan 0 |
| Preservation of pre-existing files | PASS | 212 original file hashes checked; README exemption=False |
| Registered decision rules and every summary table | PASS | Independent loops reproduce all four scopes, three penalties, six offset subsets, ranks, pairwise counts, coverage and protection value |
| README numbers, claims, links and figure files | PASS | 20 result rows, 20 horizon rows, 12 robustness rows, all exclusions and winner claims verified; 3 PNGs at 300 dpi plus 3 SVGs. |
| Visual review of all three figures | PASS | Inspected PNGs: readable labels and legends, no clipping; specified colors, distinct markers/line styles; subset ranges labeled as descriptive. |

## Tolerances and interpretation

Costs: 1e-6 relative. Forecasts and bank values: 1e-6 beds absolute. CBC plan comparisons: 1e-3 beds. Monthly accounting was also reconstructed independently from active capacity, changes and actual gaps.
All 61 original and rolling pytest tests passed. Deprecation warnings were from PuLP. No warning was treated as a convergence success.
The six offset subsets are rebuilt within each reporting scope from its sorted retained origin list. Each subset has non-overlapping target windows; subsets overlap one another. Their range is descriptive, not a confidence interval.
Primary result: Seasonal naive meets the registered clear-winner rule on all 25 origins. After removing the two original origins it remains top, but no method meets the rule. The top method changes to ExtraTrees-24 at the indicative 10,000 penalty.
Coverage is above 90% overall for every method, but is below target at Seasonal naive H5/H6, Persistence H1 and SARIMA-24 H5. These are empirical rates from overlapping cases, not formal coverage guarantees.
Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).

## Publication formatting

Git whitespace review found CRLF and trailing spaces in Matplotlib SVG exports. The SVG serializer was normalized to LF with no trailing spaces, without changing numerical results or figure geometry. Figure hashes and the report audit were refreshed; all publication checks pass.
