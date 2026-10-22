# NHS bed forecasts: frozen six-month SARIMA

We forecast NHS England General and Acute (G&A) beds available and occupied, separately. These are monthly national aggregates, not daily or patient-level data. G&A excludes maternity and mental health beds.

## Frozen forecast design

At each origin, we use observations through that cutoff to forecast the following six months. We do not update forecasts using intervening actual observations. Actual outcomes are used afterward to calculate errors. Observation cutoffs do not prove that every return was published by that date.

All tables use the original, **uncorrected target-only SARIMA(1,0,0)(0,1,0)[12]** point forecasts: a 12-month seasonal period, no trend or external predictors. The same specification is fitted separately to each bed series through each origin, with training-only scaling.

## Actual versus predicted beds

Actuals retain their saved precision, including fractional monthly averages. Predictions and absolute errors are displayed to two decimals; metrics use unrounded saved values. H1 is the first month after the cutoff.

### February 2025 origin

Last observed month: **February 2025**. Frozen forecast: **Mar 2025-Aug 2025**.

**Available beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Mar 2025 | H1 | 102,895 | 102,847.76 | 47.24 |
| Apr 2025 | H2 | 102,205 | 102,554.27 | 349.27 |
| May 2025 | H3 | 101,673 | 101,900.53 | 227.53 |
| Jun 2025 | H4 | 101,065 | 101,268.56 | 203.56 |
| Jul 2025 | H5 | 100,640 | 100,844.37 | 204.37 |
| Aug 2025 | H6 | 100,398 | 100,414.98 | 16.98 |

**Occupied beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Mar 2025 | H1 | 96,132 | 96,465.38 | 333.38 |
| Apr 2025 | H2 | 94,853 | 95,939.47 | 1,086.47 |
| May 2025 | H3 | 94,321 | 94,952.31 | 631.31 |
| Jun 2025 | H4 | 93,496 | 94,033.89 | 537.89 |
| Jul 2025 | H5 | 92,733 | 93,210.25 | 477.25 |
| Aug 2025 | H6 | 91,930 | 91,805.38 | 124.62 |

### June 2025 origin

Last observed month: **June 2025**. Frozen forecast: **Jul 2025-Dec 2025**.

**Available beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Jul 2025 | H1 | 100,640 | 100,651.00 | 11.00 |
| Aug 2025 | H2 | 100,398 | 100,231.29 | 166.71 |
| Sep 2025 | H3 | 100,615 | 100,421.89 | 193.11 |
| Oct 2025 | H4 | 100,916 | 101,102.86 | 186.86 |
| Nov 2025 | H5 | 100,906 | 101,694.21 | 788.21 |
| Dec 2025 | H6 | 101,200 | 101,958.98 | 758.98 |

**Occupied beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Jul 2025 | H1 | 92,733 | 92,704.82 | 28.18 |
| Aug 2025 | H2 | 91,930 | 91,330.47 | 599.53 |
| Sep 2025 | H3 | 93,553 | 93,040.07 | 512.93 |
| Oct 2025 | H4 | 94,306 | 94,552.76 | 246.76 |
| Nov 2025 | H5 | 94,325 | 95,260.64 | 935.64 |
| Dec 2025 | H6 | 93,177 | 94,400.83 | 1,223.83 |

### August 2025 origin

Last observed month: **August 2025**. Frozen forecast: **Sep 2025-Feb 2026**.

**Available beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Sep 2025 | H1 | 100,615 | 100,579.99 | 35.01 |
| Oct 2025 | H2 | 100,916 | 101,252.78 | 336.78 |
| Nov 2025 | H3 | 100,906 | 101,836.38 | 930.38 |
| Dec 2025 | H4 | 101,200 | 102,093.80 | 893.80 |
| Jan 2026 | H5 | 103,272 | 103,516.05 | 244.05 |
| Feb 2026 | H6 | 102,505 | 103,261.14 | 756.14 |

**Occupied beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Sep 2025 | H1 | 93,553 | 93,603.28 | 50.28 |
| Oct 2025 | H2 | 94,306 | 95,081.84 | 775.84 |
| Nov 2025 | H3 | 94,325 | 95,757.66 | 1,432.66 |
| Dec 2025 | H4 | 93,177 | 94,867.74 | 1,690.74 |
| Jan 2026 | H5 | 97,157 | 97,456.04 | 299.04 |
| Feb 2026 | H6 | 96,411 | 97,242.57 | 831.57 |

### February 2026 origin

Last observed month: **February 2026**. Frozen forecast: **Mar 2026-Aug 2026**.

**Available beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Mar 2026 | H1 | 101,328 | 102,119.38 | 791.38 |
| Apr 2026 | H2 | 100,758 | 101,466.86 | 708.86 |
| May 2026 | H3 | 100,412.677419355 | 100,970.53 | 557.85 |
| Jun 2026 | H4 | 100,428 | 100,396.47 | 31.53 |
| Jul 2026 | H5 | 100,305 | 100,003.78 | 301.22 |
| Aug 2026 | H6 | 99,675.6129032258 | 99,792.52 | 116.90 |

**Occupied beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Mar 2026 | H1 | 94,681 | 95,401.26 | 720.26 |
| Apr 2026 | H2 | 93,869 | 94,165.76 | 296.76 |
| May 2026 | H3 | 93,592.4193548387 | 93,674.67 | 82.25 |
| Jun 2026 | H4 | 93,531 | 92,888.15 | 642.85 |
| Jul 2026 | H5 | 93,227 | 92,161.34 | 1,065.66 |
| Aug 2026 | H6 | 92,061.5483870968 | 91,392.37 | 669.18 |

### March 2026 origin

Last observed month: **March 2026**. Frozen forecast: **Apr 2026-Sep 2026**.

**Available beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Apr 2026 | H1 | 100,758 | 100,709.31 | 48.69 |
| May 2026 | H2 | 100,412.677419355 | 100,245.38 | 167.30 |
| Jun 2026 | H3 | 100,428 | 99,702.34 | 725.66 |
| Jul 2026 | H4 | 100,305 | 99,339.36 | 965.64 |
| Aug 2026 | H5 | 99,675.6129032258 | 99,156.55 | 519.07 |
| Sep 2026 | H6 | 100,034.1 | 99,430.04 | 604.06 |

**Occupied beds**

| Forecast month | Horizon | Actual beds | SARIMA prediction | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Apr 2026 | H1 | 93,869 | 93,485.39 | 383.61 |
| May 2026 | H2 | 93,592.4193548387 | 93,031.99 | 560.43 |
| Jun 2026 | H3 | 93,531 | 92,281.07 | 1,249.93 |
| Jul 2026 | H4 | 93,227 | 91,587.89 | 1,639.11 |
| Aug 2026 | H5 | 92,061.5483870968 | 90,850.70 | 1,210.85 |
| Sep 2026 | H6 | 93,235.5666666667 | 92,535.73 | 699.84 |

## Early versus later forecast errors

H1-H3 and H4-H6 are two parts of **one frozen six-month path**, not separate forecasting runs. MAE is the mean absolute error in beds. Difference = later MAE minus early MAE; change = difference / early MAE. A positive difference means larger later errors. A zero early MAE would make percentage change undefined; none occurs here.

| Origin | Target | H1-H3 MAE | H4-H6 MAE | Difference | Change % |
| --- | --- | ---: | ---: | ---: | ---: |
| February 2025 | Available | 208.01 | 141.63 | -66.38 | -31.91% |
| February 2025 | Occupied | 683.72 | 379.92 | -303.80 | -44.43% |
| June 2025 | Available | 123.61 | 578.02 | +454.41 | +367.63% |
| June 2025 | Occupied | 380.21 | 802.08 | +421.86 | +110.95% |
| August 2025 | Available | 434.06 | 631.33 | +197.27 | +45.45% |
| August 2025 | Occupied | 752.92 | 940.45 | +187.53 | +24.91% |
| February 2026 | Available | 686.03 | 149.89 | -536.14 | -78.15% |
| February 2026 | Occupied | 366.42 | 792.56 | +426.14 | +116.30% |
| March 2026 | Available | 313.88 | 696.26 | +382.38 | +121.82% |
| March 2026 | Occupied | 731.33 | 1,183.27 | +451.94 | +61.80% |

## What we observed

- Later MAE was larger in **7 of 10** origin/target comparisons. Both February 2025 series improved later, as did available beds from February 2026. February 2026 therefore had opposite patterns for the two targets.
- **None** of the ten paths had absolute errors increasing every month. February 2025's April errors were particularly large (349.27 available; 1,086.47 occupied), contributing to its higher early averages.
- Some averages depend strongly on a difficult month. For August 2025 occupied beds, December's 1,690.74-bed error accounts for about 60% of later absolute error. Without December, the other two later errors average 565.31 beds, below the early average of 752.92. By contrast, both June 2025 series still have larger later averages after removing their worst later month.
- Comparing horizons also compares different calendar months. This does **not** isolate a causal effect of forecast distance. Individual-target accuracy is not capacity-gap accuracy or planning performance.

## Limits and evidence

The 30 origin-month cases cover **19 distinct outcome months**, with two target records per case. Overlapping windows are not independent trials. The 2025 outcomes were repeatedly inspected during development. The locked 2026 evaluation was a partially blind retrospective exercise: only four distinct months, June-September 2026, were previously unread; March-May had already been inspected. These forecasts do not demonstrate NHS cash savings or patient outcomes.

**2025 evidence:** the [published forecast records](results/six_month_sarima_forecasts.csv), filtered to `policy=SARIMA`, contain the original predictions and actuals from the May 2026 workbook, sheet `Timeseries all acute trusts`. They match the local canonical predictions and actuals; tiny serialization differences in saved signed-error fields do not change these calculations.

**2026 evidence:** original `A_hat` and `O_hat` in local `confirm_inputs_v5b.csv`; separate actuals corroborated by local `monthly_scores_v6.csv` and the September 2026 workbook, sheet `TS - All Acutes`. That release revised May 2026, so its actuals are retained only for the 2026 trials, without replacing the 2025 vintage. The two workbook histories match through March 2026. The separate saved actuals match the newer workbook to within 0.000000001 bed; all 60 origin-target-horizon records and their date mappings were checked.

**Reproduction boundary:** the local 2026 records, latest helper/notebook and September workbook are **not published here**. These README tables report checked saved results, not a complete public reproduction bundle. No models or optimization were rerun for this update. Fresh end-to-end reproduction has not been verified. [Data provenance](DATA_PROVENANCE.md) and the [existing reproduction review](REPRODUCTION_REVIEW.md) cover the earlier bundle and May workbook, not the latest local study. Historical publication timing, data revisions and documented reuse/licensing questions remain unresolved.
