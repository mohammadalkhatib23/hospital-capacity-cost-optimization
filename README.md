# NHS bed forecasts: capacity-gap accuracy

We forecast NHS England General and Acute (G&A) beds available and occupied, separately, then evaluate the **capacity-gap input used by the planner**. These are monthly national aggregates, not daily or patient-level data. G&A excludes maternity and mental health beds.

## Frozen forecast design and metric

At each origin, observations through that cutoff produce one frozen six-month path. No intervening actuals update the forecasts; actual outcomes score them afterward. Observation cutoffs do not establish historical publication availability.

The predictions remain the original **uncorrected target-only SARIMA(1,0,0)(0,1,0)[12]** forecasts, fitted separately to each series with training-only scaling, no trend and no external predictors. **Only the evaluation metric changes: SARIMA is not retrained with a different loss function.** No model selection, scenarios or optimization were run for this update.

At the fixed 90% occupancy threshold:

```text
actual_gap        = actual_occupied / 0.90 - actual_available
predicted_gap     = predicted_occupied / 0.90 - predicted_available
signed_gap_error  = actual_gap - predicted_gap
absolute_gap_error = abs(signed_gap_error)
```

Units are **bed-equivalents**. The gap is an occupancy-adjusted planning requirement, not observed unmet patient demand. Positive signed error means underestimated requirements; negative means overestimated requirements. All gap values below are displayed to two decimals, but calculations use full-precision saved records.

## Early versus later capacity-gap errors

H1-H3 and H4-H6 are parts of **one frozen six-month forecast**, not separate runs. MAE is the mean absolute gap error. Difference = later MAE minus early MAE; change = 100 x difference / early MAE. Percentage change is undefined when early MAE is zero; no such case occurs here.

| Origin | H1-H3 gap MAE | H4-H6 gap MAE | Difference | Change % |
| --- | ---: | ---: | ---: | ---: |
| February 2025 | 583.17 | 291.81 | -291.36 | -49.96% |
| June 2025 | 306.19 | 313.18 | +6.99 | +2.28% |
| August 2025 | 425.87 | 413.61 | -12.25 | -2.88% |
| February 2026 | 284.83 | 808.68 | +523.85 | +183.92% |
| March 2026 | 498.70 | 618.48 | +119.78 | +24.02% |

## Monthly actual versus predicted gaps

### February 2025 origin

Last observed month: **February 2025**. Frozen forecast: **Mar 2025-Aug 2025**.

| Month | Horizon | Actual gap | Predicted gap | Signed error | Absolute error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mar 2025 | H1 | 3,918.33 | 4,335.99 | -417.66 | 417.66 |
| Apr 2025 | H2 | 3,187.22 | 4,045.15 | -857.93 | 857.93 |
| May 2025 | H3 | 3,128.11 | 3,602.03 | -473.92 | 473.92 |
| Jun 2025 | H4 | 2,819.44 | 3,213.54 | -394.10 | 394.10 |
| Jul 2025 | H5 | 2,396.67 | 2,722.57 | -325.90 | 325.90 |
| Aug 2025 | H6 | 1,746.44 | 1,591.01 | +155.44 | 155.44 |

**Full six-month gap MAE: 437.49 bed-equivalents.** Later errors improved by 49.96%. April had the largest error (857.93), rather than the most distant month.

<details>
<summary>Supporting evidence: individual available- and occupied-bed forecasts</summary>

Actuals retain their saved precision; predictions and absolute errors are shown to two decimals.

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

</details>

### June 2025 origin

Last observed month: **June 2025**. Frozen forecast: **Jul 2025-Dec 2025**.

| Month | Horizon | Actual gap | Predicted gap | Signed error | Absolute error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Jul 2025 | H1 | 2,396.67 | 2,354.36 | +42.31 | 42.31 |
| Aug 2025 | H2 | 1,746.44 | 1,247.01 | +499.43 | 499.43 |
| Sep 2025 | H3 | 3,332.78 | 2,955.96 | +376.81 | 376.81 |
| Oct 2025 | H4 | 3,868.44 | 3,955.76 | -87.32 | 87.32 |
| Nov 2025 | H5 | 3,899.56 | 4,150.95 | -251.39 | 251.39 |
| Dec 2025 | H6 | 2,330.00 | 2,930.83 | -600.83 | 600.83 |

**Full six-month gap MAE: 309.68 bed-equivalents.** Later errors were only 2.28% larger on average. December accounts for about 64% of later absolute error; the other two later months average 169.35, below the early average of 306.19.

<details>
<summary>Supporting evidence: individual available- and occupied-bed forecasts</summary>

Actuals retain their saved precision; predictions and absolute errors are shown to two decimals.

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

</details>

### August 2025 origin

Last observed month: **August 2025**. Frozen forecast: **Sep 2025-Feb 2026**.

| Month | Horizon | Actual gap | Predicted gap | Signed error | Absolute error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sep 2025 | H1 | 3,332.78 | 3,423.65 | -90.87 | 90.87 |
| Oct 2025 | H2 | 3,868.44 | 4,393.71 | -525.26 | 525.26 |
| Nov 2025 | H3 | 3,899.56 | 4,561.02 | -661.47 | 661.47 |
| Dec 2025 | H4 | 2,330.00 | 3,314.79 | -984.79 | 984.79 |
| Jan 2026 | H5 | 4,680.22 | 4,768.44 | -88.22 | 88.22 |
| Feb 2026 | H6 | 4,618.33 | 4,786.16 | -167.83 | 167.83 |

**Full six-month gap MAE: 419.74 bed-equivalents.** Later errors improved by 2.88%, despite both individual bed series having larger later MAE. December was difficult (984.79); January and February were much closer.

<details>
<summary>Supporting evidence: individual available- and occupied-bed forecasts</summary>

Actuals retain their saved precision; predictions and absolute errors are shown to two decimals.

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

</details>

### February 2026 origin

Last observed month: **February 2026**. Frozen forecast: **Mar 2026-Aug 2026**.

| Month | Horizon | Actual gap | Predicted gap | Signed error | Absolute error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mar 2026 | H1 | 3,873.11 | 3,882.01 | -8.90 | 8.90 |
| Apr 2026 | H2 | 3,540.89 | 3,161.76 | +379.13 | 379.13 |
| May 2026 | H3 | 3,578.90 | 3,112.44 | +466.46 | 466.46 |
| Jun 2026 | H4 | 3,495.33 | 2,812.58 | +682.75 | 682.75 |
| Jul 2026 | H5 | 3,280.56 | 2,397.71 | +882.85 | 882.85 |
| Aug 2026 | H6 | 2,615.00 | 1,754.56 | +860.44 | 860.44 |

**Full six-month gap MAE: 546.75 bed-equivalents.** Later errors were 183.92% larger. Every later absolute error exceeded every early one, so this was not driven by a single late month; the path still improved slightly from H5 to H6.

<details>
<summary>Supporting evidence: individual available- and occupied-bed forecasts</summary>

Actuals retain their saved precision; predictions and absolute errors are shown to two decimals.

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

</details>

### March 2026 origin

Last observed month: **March 2026**. Frozen forecast: **Apr 2026-Sep 2026**.

| Month | Horizon | Actual gap | Predicted gap | Signed error | Absolute error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Apr 2026 | H1 | 3,540.89 | 3,163.34 | +377.54 | 377.54 |
| May 2026 | H2 | 3,578.90 | 3,123.50 | +455.40 | 455.40 |
| Jun 2026 | H3 | 3,495.33 | 2,832.17 | +663.16 | 663.16 |
| Jul 2026 | H4 | 3,280.56 | 2,424.96 | +855.59 | 855.59 |
| Aug 2026 | H5 | 2,615.00 | 1,788.68 | +826.32 | 826.32 |
| Sep 2026 | H6 | 3,560.97 | 3,387.43 | +173.54 | 173.54 |

**Full six-month gap MAE: 558.59 bed-equivalents.** Later errors were 24.02% larger on average, but peaked in July and fell afterward. Without July, the other two later errors average 499.93, close to the early average of 498.70.

<details>
<summary>Supporting evidence: individual available- and occupied-bed forecasts</summary>

Actuals retain their saved precision; predictions and absolute errors are shown to two decimals.

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

</details>

## What the gap metric changes

- Later gap MAE is larger at **June 2025, February 2026 and March 2026**, but improves at **February and August 2025**. June's increase and August's improvement are small; neither supports a general deterioration claim.
- **No path deteriorates every month.** Some apparent changes depend heavily on difficult months, as the origin notes show. Removing a month is only a diagnostic, not a replacement for the full six-month score.
- August 2025's gap MAE improves even though both individual-target MAEs worsen. February 2026's available-bed MAE improves, but occupied-bed and gap MAE worsen. June's much larger individual-target deterioration becomes only a small gap-MAE increase. The gap combines signed errors, so they can cancel or reinforce.
- Horizon comparisons also change calendar months: these observations cannot isolate forecast distance as the cause. Gap MAE penalizes under- and overprediction equally; **lower gap MAE does not automatically imply lower planning cost**.

### How individual errors combine

For this identity, both target errors mean **actual minus prediction**, unlike the opposite sign in some saved target-error fields:

`gap_error = occupied_error / 0.90 - available_error`

Verified examples from the saved monthly records (components shown to six decimals; results calculated unrounded):

- **Cancellation, February 2026 origin -> March 2026:** occupied error = -720.256588; available error = -791.381371. Then `(-720.256588 / 0.90) - (-791.381371)` is approximately **-8.903727**, or **-8.90 bed-equivalents**. Both bed forecasts are too high, but their effects largely cancel in the gap.
- **Reinforcement, February 2025 origin -> March 2025:** occupied error = -333.377470; available error = +47.237849. Then `(-333.377470 / 0.90) - 47.237849` is approximately **-417.657260**, or **-417.66 bed-equivalents**. Overpredicted occupied beds and underpredicted available beds both inflate the predicted requirement. This example belongs to an origin whose later gap errors improved.

<details>
<summary>Supporting evidence: early/later individual-bed MAEs</summary>

These are separate target scores in beds, not capacity-gap MAE.

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

</details>

## Limits, evidence and reproduction

The 30 origin-horizon cases cover **19 distinct outcome months**, with two target records per case. Overlapping outcomes are not independent trials. The 2025 outcomes were repeatedly inspected during development. The locked 2026 evaluation was a partially blind retrospective exercise: only four distinct months, June-September 2026, were previously unread; March-May had already been inspected. These forecasts do not establish NHS cash savings or patient outcomes.

**2025 evidence:** the [published forecast records](results/six_month_sarima_forecasts.csv), filtered to `policy=SARIMA`, contain the original predictions and actuals from the May 2026 workbook, sheet `Timeseries all acute trusts`. Predictions and actuals match the local canonical records. Errors are recomputed from those values using the sign convention above, rather than copied from the CSV's prediction-minus-actual error column.

**2026 evidence:** original `A_hat` and `O_hat` in local `confirm_inputs_v5b.csv`; separate actuals corroborated by local `monthly_scores_v6.csv` and the September 2026 workbook, sheet `TS - All Acutes`. Its revised May 2026 actuals are retained for the 2026 trials, without replacing the 2025 vintage. Workbook histories match through March 2026; separate saved actuals match the newer workbook within 0.000000001 bed. All **60 underlying target records, 30 gap calculations, date mappings and summary metrics** were checked independently.

**Reproduction boundary:** the local 2026 records, latest helper/notebook and September workbook are **not published here**. These are checked saved results, not a complete public reproduction bundle. Fresh end-to-end reproduction has not been verified. [Data provenance](DATA_PROVENANCE.md) and the [existing reproduction review](REPRODUCTION_REVIEW.md) cover the earlier bundle and May workbook, not the latest local study. Historical publication timing, data revisions and documented reuse/licensing questions remain unresolved.
