# Logic and sanity review

## Part A — design review before registration

Reviewed against the saved monthly and rolling studies at commit `5233111`, before registering the comparison, fitting any schedule for Combo or computing any Combo score.

| Design question | Rating | Reason |
| --- | --- | --- |
| 1. Does the bank represent the forecast? | OK | For either target, on the same backtest and horizon with observed value y and k forecasts, **y − (Σ fᵢ)/k = Σ(y − fᵢ)/k**. Applying occupied / 0.90 − available preserves this identity for the gap. The entire six-month path keeps its date and the two targets stay paired. |
| 2. Is the comparison fair? | OK | Combo combines existing forecasts with fixed equal weights. All competitors use the same observations, training cutoff, feasible schedules, shortage price and solver conventions. Each retained historical origin supplies one equally weighted scenario per method, including Combo. Matching complete backtest keys and counts is a condition of scoring. |
| 3. Is the primary population appropriate? | Acceptable with disclosure | Excluding September 2025 and March 2026 removes the two direct examples used to suggest the idea. Neighbouring origins still share target months with them, and the individual methods' results were previously inspected. This retrospective comparison cannot establish performance in a new year. The inclusive populations are descriptive. |
| 4. Can future data enter the plan? | OK | Each frozen point forecast was made using information through its origin. Historical paths qualify only when their last target has been observed by that origin. Combo averages available forecasts and same-date errors before adding raw historical paths. Plan files must be frozen and hashed before loading evaluation actuals for scoring. |
| 5. What could SARIMA non-convergence change? | Acceptable with disclosure | All methods lose the same historical backtests. This keeps scenario counts equal but can omit difficult conditions for the other methods as well. The five-method conclusions apply where SARIMA converged. October 2025 (occupied) and January 2026 (available) are separate four-method comparisons. The direction of any selection effect is unknown. |
| 6. What else affects interpretation? | Acceptable with disclosure | Equal weights can cancel opposing errors. A shared miss can survive averaging, and a small six-month mean can hide a shortage in a costly month. A narrower error bank alone does not demonstrate better coverage. LP minimises cost against the forecast, so superiority over the Rule on realised demand is an empirical check. Costs and limits are illustrative. The 23 primary origins represent about four independent six-month windows, and the 13 monthly origins about two to three. |

The design can proceed. Three items are OK and three require disclosure. The available data support a paired retrospective comparison under fixed prices.

## Part B — results sanity check

### B1. Forecast lies inside the individual range

**PASS**. The Combo gap fell inside the individual forecasts' range in all 162 monthly predictions (150 five-method and 12 four-method forecasts).

### B2. Signed-error identity

**PASS**. With signed error defined as actual minus forecast, the maximum discrepancy was 2.91e-11 beds over individual months and 8.25e-12 over six-month means. Both are within 1e-6 beds.

### B3. Combo LP versus Combo Rule

**PASS**. At the base shortage price of 5,000, LP saved money at 19 origins and tied the Rule at 8 using a tolerance of 1e-6 times the Rule cost. Both four-method origins are included.

### B4. Monthly hand calculation

**PASS**. At the **2024-02 origin, target month 2024-03**, the five forecasts are listed in the order Mean-24, Persistence, Seasonal naive, SARIMA-24 and ExtraTrees-24. Displayed inputs are rounded to six decimals. The calculation used the saved precision.

- Mean occupied = (92,931.708333 + 97,256.000000 + 94,802.000000 + 96,412.373666 + 96,441.787778) / 5 = 95,568.773955.
- Mean available = (99,677.583333 + 103,408.000000 + 100,910.000000 + 103,094.109937 + 103,104.250556) / 5 = 102,038.788765.
- Combo gap = 95,568.773955 / 0.90 − 102,038.788765 = **4,148.737852 beds**.
- Actual gap = 96,529 / 0.90 − 102,931 = 4,323.444444 beds. Frozen capacity moved from 0.000000 to 4,148.737900, opening 4,148.737900 and closing 0.000000 beds.
- Shortfall = max(0, 4,323.444444 − 4,148.737900) = **174.706544 beds**.
- LP monthly cost = 500 × 4,148.737900 + 550 × 4,148.737900 + 300 × 0.000000 + 5,000 × 174.706544 = **5,229,707.52 model units**, matching the saved bill.

### B5. Exact reproduction of individual costs

**PASS**. All 250 individual LP and Protected costs in the rolling study and all 126 in the monthly study agree exactly as saved. The two partial origins have monthly records only. Overlapping records were checked against both sources.

### B6. Concentration by origin

**Result**. For each comparator at the 23 primary origins, Δ = Combo Protected cost minus its Protected cost, in millions. A negative amount favours Combo. The hindsight benchmark cancels because both schedules face the same origin.

| Comparator | Net Δ | Drop first origin | Net after one | Also drop | Net after two | Top two / net |
| --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | -74.835 | 2024-02 | -68.299 | 2024-03 | -61.882 | 17.3% |
| Persistence | -101.294 | 2024-08 | -81.374 | 2025-08 | -67.622 | 33.2% |
| Seasonal naive | -12.265 | 2024-08 | -8.891 | 2024-06 | -6.169 | 49.7% |
| SARIMA-24 | -32.314 | 2024-03 | -19.218 | 2024-02 | -12.402 | 61.6% |
| ExtraTrees-24 | -18.587 | 2024-02 | -12.748 | 2024-09 | -10.293 | 44.6% |

For each comparison we removed the origin, then the pair, contributing most in the direction of the original net difference. These removals have the largest possible effect against its sign. Percentages use the absolute sum of those two contributions divided by the absolute net difference, so opposing months can take the percentage over 100%.

Every net advantage survived removing the largest contributor. Every net advantage survived removing the two largest contributors. Two origins account for at least half the net magnitude against **SARIMA-24**. The shared target months also limit the evidence to about four independent windows.

<details>
<summary>Sorted differences and running totals at all primary origins</summary>

Each column reads down independently, from the largest Combo saving to its largest extra cost. Each cell gives the origin, Δ and cumulative Δ in millions. Rounding is for display.

| Rank | Mean-24 | Persistence | Seasonal naive | SARIMA-24 | ExtraTrees-24 |
| --- | --- | --- | --- | --- | --- |
| 1 | 2024-02 · -6.536 · -6.536 | 2024-08 · -19.920 · -19.920 | 2024-08 · -3.374 · -3.374 | 2024-03 · -13.096 · -13.096 | 2024-02 · -5.839 · -5.839 |
| 2 | 2024-03 · -6.417 · -12.953 | 2025-08 · -13.752 · -33.671 | 2024-06 · -2.722 · -6.096 | 2024-02 · -6.816 · -19.912 | 2024-09 · -2.455 · -8.294 |
| 3 | 2024-06 · -5.259 · -18.212 | 2024-02 · -8.377 · -42.049 | 2024-07 · -2.439 · -8.535 | 2024-05 · -3.539 · -23.450 | 2025-06 · -1.703 · -9.997 |
| 4 | 2024-04 · -4.467 · -22.679 | 2024-03 · -6.613 · -48.662 | 2024-09 · -1.332 · -9.867 | 2025-08 · -3.124 · -26.574 | 2024-10 · -1.657 · -11.654 |
| 5 | 2024-05 · -4.202 · -26.881 | 2026-02 · -6.218 · -54.879 | 2024-02 · -1.304 · -11.171 | 2025-07 · -2.053 · -28.627 | 2024-08 · -1.443 · -13.097 |
| 6 | 2024-08 · -4.157 · -31.039 | 2025-02 · -5.646 · -60.526 | 2024-10 · -1.184 · -12.355 | 2024-08 · -1.575 · -30.202 | 2025-07 · -1.185 · -14.282 |
| 7 | 2024-07 · -4.089 · -35.127 | 2025-12 · -5.428 · -65.954 | 2025-08 · -1.125 · -13.480 | 2024-04 · -1.441 · -31.643 | 2025-08 · -1.136 · -15.418 |
| 8 | 2025-04 · -3.657 · -38.785 | 2024-04 · -5.256 · -71.210 | 2024-12 · -1.086 · -14.566 | 2026-04 · -1.405 · -33.048 | 2024-07 · -0.893 · -16.311 |
| 9 | 2025-06 · -3.544 · -42.329 | 2025-03 · -4.291 · -75.501 | 2024-11 · -0.953 · -15.520 | 2025-11 · -1.164 · -34.212 | 2024-11 · -0.875 · -17.186 |
| 10 | 2025-05 · -3.421 · -45.750 | 2025-01 · -4.008 · -79.509 | 2024-05 · -0.898 · -16.418 | 2025-06 · -1.019 · -35.231 | 2024-06 · -0.873 · -18.059 |
| 11 | 2025-03 · -3.115 · -48.865 | 2026-04 · -3.133 · -82.641 | 2026-02 · -0.867 · -17.285 | 2024-12 · -0.983 · -36.214 | 2024-12 · -0.582 · -18.641 |
| 12 | 2025-07 · -3.059 · -51.924 | 2024-11 · -2.756 · -85.398 | 2025-07 · -0.625 · -17.910 | 2024-09 · -0.867 · -37.081 | 2024-05 · -0.395 · -19.036 |
| 13 | 2025-02 · -2.559 · -54.483 | 2025-11 · -2.677 · -88.075 | 2025-06 · -0.498 · -18.407 | 2025-05 · -0.673 · -37.754 | 2025-05 · -0.340 · -19.377 |
| 14 | 2026-04 · -2.541 · -57.024 | 2024-05 · -2.625 · -90.700 | 2025-11 · +0.058 · -18.350 | 2025-01 · -0.009 · -37.763 | 2026-02 · -0.318 · -19.694 |
| 15 | 2024-12 · -2.397 · -59.421 | 2024-10 · -2.567 · -93.267 | 2025-05 · +0.179 · -18.171 | 2024-07 · +0.121 · -37.642 | 2025-02 · -0.275 · -19.969 |
| 16 | 2024-09 · -2.335 · -61.756 | 2024-06 · -2.364 · -95.631 | 2025-04 · +0.284 · -17.887 | 2025-04 · +0.148 · -37.494 | 2025-03 · -0.183 · -20.152 |
| 17 | 2024-10 · -2.308 · -64.065 | 2025-04 · -1.909 · -97.540 | 2024-04 · +0.349 · -17.538 | 2025-02 · +0.368 · -37.126 | 2025-04 · -0.164 · -20.316 |
| 18 | 2026-02 · -2.280 · -66.344 | 2025-07 · -1.793 · -99.332 | 2024-03 · +0.484 · -17.054 | 2024-06 · +0.570 · -36.556 | 2024-04 · -0.157 · -20.473 |
| 19 | 2025-01 · -2.127 · -68.471 | 2025-05 · -1.372 · -100.705 | 2025-01 · +0.627 · -16.427 | 2024-10 · +0.576 · -35.980 | 2025-01 · -0.145 · -20.618 |
| 20 | 2024-11 · -1.929 · -70.401 | 2025-06 · -1.107 · -101.811 | 2025-03 · +0.776 · -15.650 | 2025-03 · +0.666 · -35.314 | 2024-03 · +0.041 · -20.577 |
| 21 | 2025-08 · -1.886 · -72.287 | 2024-12 · -0.339 · -102.150 | 2025-02 · +1.105 · -14.545 | 2024-11 · +0.749 · -34.565 | 2025-11 · +0.180 · -20.396 |
| 22 | 2025-11 · -1.666 · -73.953 | 2024-09 · +0.370 · -101.780 | 2025-12 · +1.139 · -13.406 | 2026-02 · +0.842 · -33.723 | 2026-04 · +0.355 · -20.041 |
| 23 | 2025-12 · -0.882 · -74.835 | 2024-07 · +0.486 · -101.294 | 2026-04 · +1.141 · -12.265 | 2025-12 · +1.409 · -32.314 | 2025-12 · +1.454 · -18.587 |

</details>

### B7. Historical error-bank spread by horizon

**Result**. For each primary origin, method and horizon we measured the raw historical gap-error width from the 10th to the 90th percentile using linear interpolation. The first table gives the mean width in beds across origins, with each origin weighted equally.

| Horizon | Combo (beds) | Mean-24 | Persistence | Seasonal naive | SARIMA-24 | ExtraTrees-24 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2,166.7 | 3,723.3 | 2,648.4 | 3,094.3 | 2,428.1 | 2,174.0 |
| 2 | 2,075.3 | 3,215.3 | 3,278.1 | 2,499.9 | 2,778.9 | 2,182.2 |
| 3 | 2,110.5 | 3,130.6 | 3,415.9 | 2,424.0 | 2,789.6 | 2,538.1 |
| 4 | 2,049.2 | 2,375.2 | 3,246.3 | 1,836.1 | 3,380.4 | 2,187.0 |
| 5 | 2,234.5 | 2,458.0 | 4,283.7 | 1,534.6 | 3,359.3 | 2,571.3 |
| 6 | 1,887.1 | 2,633.2 | 4,394.2 | 1,320.7 | 3,378.2 | 2,060.8 |

The second table counts how often Combo's width was smaller than each method's at that horizon, out of 23 origins. Each comparison uses the same historical backtest paths.

| Horizon | Mean-24 | Persistence | Seasonal naive | SARIMA-24 | ExtraTrees-24 |
| --- | --- | --- | --- | --- | --- |
| 1 | 23 | 22 | 23 | 16 | 14 |
| 2 | 23 | 23 | 19 | 23 | 17 |
| 3 | 23 | 23 | 21 | 23 | 18 |
| 4 | 23 | 23 | 4 | 23 | 15 |
| 5 | 17 | 23 | 0 | 23 | 19 |
| 6 | 23 | 23 | 0 | 23 | 20 |

Averaging produced a narrower central range in 570 of 690 paired comparisons. Seasonal naive already had a narrower range at every primary origin for horizons 5, 6. All five errors had the same sign in 686 of 2820 historical origin, path and horizon records. The same past path can appear in several banks. Shared errors survive averaging, and a method with tight errors on its own can have a narrower range than the combination.

### B8. Does this make sense?

**Result**. At the February 2025 origin, all five methods forecast high on average. Even after averaging, the forecast missed by 805.8 beds in that direction, its largest absolute mean miss in the primary population. Across the primary origins, Combo's mean Protected excess cost was 0.533m lower than Seasonal naive's, the cheapest individual Protected method. The schedules establish the cost difference. A plausible explanation is that opposing errors can cancel, while a shared miss can leave beds unused when all forecasts run high or create a shortage when all run low. Confidence in that mechanism's size in another year is limited because 23 overlapping origins represent about four independent windows.

All Part B checks passed.
