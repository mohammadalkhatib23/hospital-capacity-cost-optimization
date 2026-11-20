# Does optimisation improve a bed-capacity forecast? Two frozen origins and a monthly comparison

## Question

We freeze a bed-capacity plan before knowing the next six months of demand. Five methods forecast NHS England general and acute (G&A) beds, then we either follow each forecast, optimise the schedule, or optimise with protection against forecast error. Does optimisation make the realised plan cheaper, why, and by how much? How much does protection add on top?

## Method

At each origin, only observations available up to that month are used. Each method uses the 24-month window ending at the origin and forecasts the next six months: **September 2025** covers **October 2025 to March 2026**, and **March 2026** covers **April to September 2026**. All six decisions stay fixed; there is no monthly replanning.

The planning gap is **occupied / 0.90 - available**, measured in beds. It is the additional capacity needed to keep occupancy at or below 90%. A shortfall here means falling short of that capacity target.

Each method forecasts occupied and available beds separately:

- **Mean-24:** repeats the average of its training window.
- **Persistence:** repeats the last observed month.
- **Seasonal naive:** repeats the same calendar month from the previous year.
- **SARIMA-24:** a fixed seasonal time-series model using the training window.
- **ExtraTrees-24:** a fixed tree ensemble using earlier values and calendar features; predictions feed later forecast steps.

**Rule** follows the forecast gap, opening the requested non-negative capacity as far as the limits allow. **LP** optimises the entire schedule: running costs 500 per bed-month, opening 550 per bed opened, closing 300 per bed closed, and shortfall 5,000 per missing bed-month. Capacity carries over between months, starts at zero, is capped at 15,000 beds, and can rise or fall by at most 5,000 beds per month; there is no terminal closing charge.

**Protected** uses the same optimisation with scenarios formed from the method's own past forecast errors, adding a data-driven safety margin. **Hindsight** uses the actual future gap: it is the best possible schedule under these same constraints. The September and March case studies use previously saved forecasts, plans and scores.

Costs are **illustrative model units (validated: False)**, not NHS expenditure estimates. Cost tables use **millions**. Operating cost means running + opening + closing; total cost adds the shortfall penalty. Small solver-rounding differences are treated as equal using the saved relative tolerance of 1e-6.

## Origin 1: September 2025

### What happened

![September 2025 actual planning gap](figures/case_study/sep2025_actual_gap.png)

[SVG](figures/case_study/sep2025_actual_gap.svg)

The gap dipped to **2,330 beds in December**, then peaked at **4,680 in January**. It remained at 4,618 in February before easing to 3,873 in March.

### What each method forecast

![September 2025 gap forecasts against actual](figures/case_study/sep2025_forecasts.png)

[SVG](figures/case_study/sep2025_forecasts.svg)

MAE is the average absolute gap error, in beds. Signed error is **actual minus forecast**: positive means the forecast was too low on average; negative means too high.

| Method | Gap MAE (beds) | Mean signed error (beds) | Forecast on average |
| --- | --- | --- | --- |
| Mean-24 | 709.1 | +289.5 | Too low |
| Persistence | 879.8 | +545.5 | Too low |
| Seasonal naive | 313.0 | -300.3 | Too high |
| SARIMA-24 | 346.0 | -346.0 | Too high |
| ExtraTrees-24 | 454.0 | +205.4 | Too low |

Source: [saved accuracy](outputs/fair_comparison/O1_sep2025/accuracy.csv).

### The cost chain

Each cell is **total (operating + shortfall)**, in millions. Bold marks the cheapest forecast-based plan; hindsight is a benchmark. Cost components are **computed from saved plans** and checked against saved scores. Rounded components may differ from rounded totals.

| Method | Rule | LP | Protected | Hindsight |
| --- | --- | --- | --- | --- |
| Mean-24 | 27.72<br>(12.74 + 14.98) | 27.72<br>(12.74 + 14.98) | 18.42<br>(18.42 + 0.00) | 15.24<br>(15.24 + 0.00) |
| Persistence | 33.21<br>(11.83 + 21.38) | 33.21<br>(11.83 + 21.38) | 17.55<br>(17.55 + 0.00) | 15.24<br>(15.24 + 0.00) |
| Seasonal naive | 16.57<br>(16.38 + 0.19) | 16.14<br>(15.95 + 0.19) | 17.61<br>(17.61 + 0.00) | 15.24<br>(15.24 + 0.00) |
| SARIMA-24 | 16.54<br>(16.54 + 0.00) | **16.11**<br>(16.11 + 0.00) | 19.46<br>(19.46 + 0.00) | 15.24<br>(15.24 + 0.00) |
| ExtraTrees-24 | 23.77<br>(13.88 + 9.89) | 23.66<br>(13.77 + 9.89) | 17.05<br>(17.05 + 0.00) | 15.24<br>(15.24 + 0.00) |

![September 2025 cost chain with operating and shortfall components](figures/case_study/sep2025_chain.png)

[SVG](figures/case_study/sep2025_chain.svg)

Source: [saved total scores](outputs/fair_comparison/O1_sep2025/totals.csv) and [saved Rule/LP/Protected monthly costs](outputs/optimisation_value/monthly_costs.csv).

### Month by month

![September 2025: actual gap, forecast and all capacity plans](figures/case_study/sep2025_monthly_plans.png)

[SVG](figures/case_study/sep2025_monthly_plans.svg)

Capacity below the actual line is short; capacity above it is spare. Overlapping lines show equal values.

![September 2025: monthly realised cost of all plans](figures/case_study/sep2025_monthly_cost_all_plans.png)

[SVG](figures/case_study/sep2025_monthly_cost_all_plans.svg)

**Computed from saved plans and actuals.** Costs are millions of model units; short/spare values are rounded beds. In Total, short is summed shortfall bed-months; if that rounds to zero, spare is summed spare bed-months. Total has its own colour scale, and the unrounded monthly costs sum to the saved total for every plan.

Actual and forecast gaps, rounded to whole beds:

| Month | Actual gap | Mean-24 | Persistence | Seasonal naive | SARIMA-24 | ExtraTrees-24 |
| --- | --- | --- | --- | --- | --- | --- |
| Oct 2025 | 3,868 | 3,589 | 3,333 | 4,244 | 4,303 | 3,564 |
| Nov 2025 | 3,900 | 3,589 | 3,333 | 4,419 | 4,472 | 3,397 |
| Dec 2025 | 2,330 | 3,589 | 3,333 | 3,181 | 3,228 | 3,076 |
| Jan 2026 | 4,680 | 3,589 | 3,333 | 4,642 | 4,684 | 4,100 |
| Feb 2026 | 4,618 | 3,589 | 3,333 | 4,667 | 4,705 | 4,189 |
| Mar 2026 | 3,873 | 3,589 | 3,333 | 3,918 | 3,953 | 3,711 |

Sources: [saved actuals](outputs/fair_comparison/O1_sep2025/actuals.csv) and [saved forecasts](outputs/fair_comparison/O1_sep2025/forecasts.csv).

January and February drive the largest bills for Mean-24 and Persistence under Rule and LP, while October includes the opening costs. In December, LP keeps Seasonal naive and SARIMA-24 capacity open through the dip; Protected keeps every method above the actual gap throughout this window.

### Why optimisation helped

The tables show additional beds open (**x**), additions (**+**) and removals (**-**) each month. All values are beds, rounded to one decimal. These are the frozen schedules, not decisions updated after observing demand.

**Seasonal naive**

| Month | Rule x | Rule + | Rule - | LP x | LP + | LP - |
| --- | --- | --- | --- | --- | --- | --- |
| Oct 2025 | 4,243.9 | 4,243.9 | 0.0 | 4,243.9 | 4,243.9 | 0.0 |
| Nov 2025 | 4,419.4 | 175.6 | 0.0 | 4,419.4 | 175.6 | 0.0 |
| Dec 2025 | 3,181.0 | 0.0 | 1,238.4 | 4,419.4 | 0.0 | 0.0 |
| Jan 2026 | 4,642.0 | 1,461.0 | 0.0 | 4,642.0 | 222.6 | 0.0 |
| Feb 2026 | 4,666.7 | 24.7 | 0.0 | 4,666.7 | 24.7 | 0.0 |
| Mar 2026 | 3,918.3 | 0.0 | 748.3 | 3,918.3 | 0.0 | 748.3 |

LP kept the November capacity through December, reducing the amount that had to reopen next month. It saved **0.43m**: 0.43m in operating cost and 0.00m in shortfall cost.

**SARIMA-24**

| Month | Rule x | Rule + | Rule - | LP x | LP + | LP - |
| --- | --- | --- | --- | --- | --- | --- |
| Oct 2025 | 4,303.2 | 4,303.2 | 0.0 | 4,303.2 | 4,303.2 | 0.0 |
| Nov 2025 | 4,471.9 | 168.7 | 0.0 | 4,471.9 | 168.7 | 0.0 |
| Dec 2025 | 3,227.9 | 0.0 | 1,244.0 | 4,471.9 | 0.0 | 0.0 |
| Jan 2026 | 4,684.2 | 1,456.4 | 0.0 | 4,684.2 | 212.3 | 0.0 |
| Feb 2026 | 4,705.0 | 20.8 | 0.0 | 4,705.0 | 20.8 | 0.0 |
| Mar 2026 | 3,953.4 | 0.0 | 751.6 | 3,953.4 | 0.0 | 751.6 |

LP kept the November capacity through December, reducing the amount that had to reopen next month. It saved **0.44m**: 0.44m in operating cost and 0.00m in shortfall cost.

Mean-24 and Persistence have flat forecasts, so **Rule = LP within solver precision**. Holding beds through a dip can cost more in that month but avoid a larger closing-and-reopening bill; it can also cover demand that the forecast underestimated.

### Why protection helped or hurt

Protection uses past errors, not knowledge of this future. A forecast that is already high can already provide spare capacity; adding protection can then increase cost. The effect below is LP minus Protected, with the saved tie band of 1% of hindsight cost.

| Method | Forecast on average | Point LP cost | Protected cost | Effect |
| --- | --- | --- | --- | --- |
| Mean-24 | Too low | 27.72 | 18.42 | Helped by 9.30m |
| Persistence | Too low | 33.21 | 17.55 | Helped by 15.66m |
| Seasonal naive | Too high | 16.14 | 17.61 | Hurt by 1.47m |
| SARIMA-24 | Too high | 16.11 | 19.46 | Hurt by 3.35m |
| ExtraTrees-24 | Too low | 23.66 | 17.05 | Helped by 6.61m |

### Where the Protected-plan costs occurred

![September 2025 Protected monthly cost heatmap and hindsight](figures/case_study/sep2025_protected_heatmap.png)

[SVG](figures/case_study/sep2025_protected_heatmap.svg)

**Computed from saved plans.** Each monthly cell shows cost and the shortfall or spare capacity, rounded to whole beds. Balanced means neither is visible after rounding. Total cells sum costs and show both shortfall and spare bed-months; they use a separate colour scale. The totals include opening costs at the start of the plan.

### Result

**SARIMA-24 + LP** was the cheapest forecast-based combination, at **16.11m**. Protection helped Mean-24, Persistence and ExtraTrees-24, but made Seasonal naive and SARIMA-24 more expensive. ExtraTrees-24 was the cheapest Protected plan, at 17.05m.

## Origin 2: March 2026

### What happened

![March 2026 actual planning gap](figures/case_study/mar2026_actual_gap.png)

[SVG](figures/case_study/mar2026_actual_gap.svg)

The April-to-September gap averaged **3,360 beds in 2026**, compared with **2,768** in the same months of 2025. It fell to 2,615 in August, then rose to 3,646 in September.

### What each method forecast

![March 2026 gap forecasts against actual](figures/case_study/mar2026_forecasts.png)

[SVG](figures/case_study/mar2026_forecasts.svg)

MAE is the average absolute gap error, in beds. Signed error is **actual minus forecast**: positive means the forecast was too low on average; negative means too high.

| Method | Gap MAE (beds) | Mean signed error (beds) | Forecast on average |
| --- | --- | --- | --- |
| Mean-24 | 237.0 | -112.5 | Too high |
| Persistence | 513.6 | -513.6 | Too high |
| Seasonal naive | 591.1 | +591.1 | Too low |
| SARIMA-24 | 335.3 | +316.5 | Too low |
| ExtraTrees-24 | 523.8 | -469.5 | Too high |

Source: [saved accuracy](outputs/fair_comparison/O2_mar2026/accuracy.csv).

### The cost chain

Each cell is **total (operating + shortfall)**, in millions. Bold marks the cheapest forecast-based plan; hindsight is a benchmark. Cost components are **computed from saved plans** and checked against saved scores. Rounded components may differ from rounded totals.

| Method | Rule | LP | Protected | Hindsight |
| --- | --- | --- | --- | --- |
| Mean-24 | 14.19<br>(12.33 + 1.87) | 14.19<br>(12.33 + 1.87) | 17.93<br>(17.93 + 0.00) | 12.67<br>(12.67 + 0.00) |
| Persistence | 13.75<br>(13.75 + 0.00) | 13.75<br>(13.75 + 0.00) | 19.49<br>(19.49 + 0.00) | 12.67<br>(12.67 + 0.00) |
| Seasonal naive | 29.09<br>(11.36 + 17.73) | 25.62<br>(11.14 + 14.48) | **13.56**<br>(12.53 + 1.03) | 12.67<br>(12.67 + 0.00) |
| SARIMA-24 | 21.99<br>(12.21 + 9.78) | 19.21<br>(12.00 + 7.22) | 15.32<br>(15.32 + 0.00) | 12.67<br>(12.67 + 0.00) |
| ExtraTrees-24 | 14.89<br>(14.08 + 0.81) | 13.93<br>(13.93 + 0.00) | 17.00<br>(17.00 + 0.00) | 12.67<br>(12.67 + 0.00) |

![March 2026 cost chain with operating and shortfall components](figures/case_study/mar2026_chain.png)

[SVG](figures/case_study/mar2026_chain.svg)

Source: [saved total scores](outputs/fair_comparison/O2_mar2026/totals.csv) and [saved Rule/LP/Protected monthly costs](outputs/optimisation_value/monthly_costs.csv).

### Month by month

![March 2026: actual gap, forecast and all capacity plans](figures/case_study/mar2026_monthly_plans.png)

[SVG](figures/case_study/mar2026_monthly_plans.svg)

Capacity below the actual line is short; capacity above it is spare. Overlapping lines show equal values.

![March 2026: monthly realised cost of all plans](figures/case_study/mar2026_monthly_cost_all_plans.png)

[SVG](figures/case_study/mar2026_monthly_cost_all_plans.svg)

**Computed from saved plans and actuals.** Costs are millions of model units; short/spare values are rounded beds. In Total, short is summed shortfall bed-months; if that rounds to zero, spare is summed spare bed-months. Total has its own colour scale, and the unrounded monthly costs sum to the saved total for every plan.

Actual and forecast gaps, rounded to whole beds:

| Month | Actual gap | Mean-24 | Persistence | Seasonal naive | SARIMA-24 | ExtraTrees-24 |
| --- | --- | --- | --- | --- | --- | --- |
| Apr 2026 | 3,541 | 3,472 | 3,873 | 3,187 | 3,290 | 3,846 |
| May 2026 | 3,579 | 3,472 | 3,873 | 3,128 | 3,337 | 3,416 |
| Jun 2026 | 3,495 | 3,472 | 3,873 | 2,819 | 3,101 | 3,825 |
| Jul 2026 | 3,281 | 3,472 | 3,873 | 2,397 | 2,725 | 3,859 |
| Aug 2026 | 2,615 | 3,472 | 3,873 | 1,746 | 2,103 | 3,982 |
| Sep 2026 | 3,646 | 3,472 | 3,873 | 3,333 | 3,703 | 4,047 |

Sources: [saved actuals](outputs/fair_comparison/O2_mar2026/actuals.csv) and [saved forecasts](outputs/fair_comparison/O2_mar2026/forecasts.csv).

Opening costs raise April's bills, while Seasonal naive's Rule and LP bills peak in July. LP keeps Seasonal naive and SARIMA-24 capacity open through August; Protected reduces their shortfalls while leaving Mean-24, Persistence and ExtraTrees-24 above the actual gap throughout the window.

### Why optimisation helped

The tables show additional beds open (**x**), additions (**+**) and removals (**-**) each month. All values are beds, rounded to one decimal. These are the frozen schedules, not decisions updated after observing demand.

**Seasonal naive**

| Month | Rule x | Rule + | Rule - | LP x | LP + | LP - |
| --- | --- | --- | --- | --- | --- | --- |
| Apr 2026 | 3,187.2 | 3,187.2 | 0.0 | 3,187.2 | 3,187.2 | 0.0 |
| May 2026 | 3,128.1 | 0.0 | 59.1 | 3,128.1 | 0.0 | 59.1 |
| Jun 2026 | 2,819.4 | 0.0 | 308.7 | 2,819.4 | 0.0 | 308.7 |
| Jul 2026 | 2,396.7 | 0.0 | 422.8 | 2,396.7 | 0.0 | 422.8 |
| Aug 2026 | 1,746.4 | 0.0 | 650.2 | 2,396.7 | 0.0 | 0.0 |
| Sep 2026 | 3,332.8 | 1,586.3 | 0.0 | 3,332.8 | 936.1 | 0.0 |

LP kept the July capacity through August, reducing the amount that had to reopen next month. It saved **3.48m**: 0.23m in operating cost and 3.25m in shortfall cost.

**SARIMA-24**

| Month | Rule x | Rule + | Rule - | LP x | LP + | LP - |
| --- | --- | --- | --- | --- | --- | --- |
| Apr 2026 | 3,290.1 | 3,290.1 | 0.0 | 3,290.1 | 3,290.1 | 0.0 |
| May 2026 | 3,336.6 | 46.5 | 0.0 | 3,336.6 | 46.5 | 0.0 |
| Jun 2026 | 3,100.6 | 0.0 | 236.0 | 3,100.6 | 0.0 | 236.0 |
| Jul 2026 | 2,725.2 | 0.0 | 375.4 | 2,725.2 | 0.0 | 375.4 |
| Aug 2026 | 2,102.9 | 0.0 | 622.2 | 2,725.2 | 0.0 | 0.0 |
| Sep 2026 | 3,702.7 | 1,599.8 | 0.0 | 3,702.7 | 977.5 | 0.0 |

LP kept the July capacity through August, reducing the amount that had to reopen next month. It saved **2.78m**: 0.22m in operating cost and 2.56m in shortfall cost.

Mean-24 and Persistence have flat forecasts, so **Rule = LP within solver precision**. Holding beds through a dip can cost more in that month but avoid a larger closing-and-reopening bill; it can also cover demand that the forecast underestimated.

### Why protection helped or hurt

Protection uses past errors, not knowledge of this future. A forecast that is already high can already provide spare capacity; adding protection can then increase cost. The effect below is LP minus Protected, with the saved tie band of 1% of hindsight cost.

| Method | Forecast on average | Point LP cost | Protected cost | Effect |
| --- | --- | --- | --- | --- |
| Mean-24 | Too high | 14.19 | 17.93 | Hurt by 3.74m |
| Persistence | Too high | 13.75 | 19.49 | Hurt by 5.74m |
| Seasonal naive | Too low | 25.62 | 13.56 | Helped by 12.06m |
| SARIMA-24 | Too low | 19.21 | 15.32 | Helped by 3.89m |
| ExtraTrees-24 | Too high | 13.93 | 17.00 | Hurt by 3.07m |

### Where the Protected-plan costs occurred

![March 2026 Protected monthly cost heatmap and hindsight](figures/case_study/mar2026_protected_heatmap.png)

[SVG](figures/case_study/mar2026_protected_heatmap.svg)

**Computed from saved plans.** Each monthly cell shows cost and the shortfall or spare capacity, rounded to whole beds. Balanced means neither is visible after rounding. Total cells sum costs and show both shortfall and spare bed-months; they use a separate colour scale. The totals include opening costs at the start of the plan.

### Result

**Seasonal naive + Protected** was the cheapest forecast-based combination, at **13.56m**. Protection helped the low Seasonal naive and SARIMA-24 forecasts, but hurt the other methods, whose forecasts were high on average.

## The two origins side by side

Costs and benefits are millions. Each cost chain is **Rule / LP / Protected / Hindsight**; each benefit pair is **optimisation / protection**. Positive benefits mean a saving; negative benefits mean an extra cost.

| Method | Sep 2025 cost chain | Mar 2026 cost chain | Sep 2025 benefits | Mar 2026 benefits |
| --- | --- | --- | --- | --- |
| Mean-24 | 27.72 / 27.72 / 18.42 / 15.24 | 14.19 / 14.19 / 17.93 / 12.67 | +0.00 / +9.30 | +0.00 / -3.74 |
| Persistence | 33.21 / 33.21 / 17.55 / 15.24 | 13.75 / 13.75 / 19.49 / 12.67 | +0.00 / +15.66 | +0.00 / -5.74 |
| Seasonal naive | 16.57 / 16.14 / 17.61 / 15.24 | 29.09 / 25.62 / 13.56 / 12.67 | +0.43 / -1.47 | +3.48 / +12.06 |
| SARIMA-24 | 16.54 / 16.11 / 19.46 / 15.24 | 21.99 / 19.21 / 15.32 / 12.67 | +0.44 / -3.35 | +2.78 / +3.89 |
| ExtraTrees-24 | 23.77 / 23.66 / 17.05 / 15.24 | 14.89 / 13.93 / 17.00 / 12.67 | +0.11 / +6.61 | +0.96 / -3.07 |

![Two origins compared as cost above their own hindsight benchmark](figures/case_study/two_origin_chain.png)

[SVG](figures/case_study/two_origin_chain.svg)

Optimisation helped Seasonal naive, SARIMA-24 and ExtraTrees-24 at both origins, while the flat forecasts were unchanged within tolerance. Protection helped Mean-24, Persistence and ExtraTrees-24 in September but hurt them in March, while Seasonal naive and SARIMA-24 switched from hurt to helped. In these two windows, the protection flip coincided with a switch in average forecast direction: the former group went from too low to too high, and the latter group went from too high to too low. This is an observed pattern in these two windows, not a general rule about protection.

## Answer

At both origins, optimisation never materially increased cost compared with following the forecast, and it saved up to 3.48m, mainly by keeping beds open through forecast dips. The cheapest plan was SARIMA-24 with optimisation in September 2025 (16.11m, close to Seasonal naive with optimisation at 16.14m) and Seasonal naive with protection in March 2026 (13.56m). Protection helped the methods that under-forecast in each window and added cost for those whose forecasts ran high; because the methods switched direction between the two windows, protection's effect flipped for every method. These are two six-month windows, so they show how the methods behaved, not a general ranking.

## Limitations

- The two detailed case studies each cover six months. The monthly extension below uses overlapping windows.
- The models are univariate and do not use staffing, admissions, discharge or policy variables.
- Occupied and available beds are forecast separately.
- National monthly totals hide local bottlenecks and daily peaks.
- Model specifications were fixed, not tuned for these windows.
- Costs are illustrative (validated: False); capacity is not a full staffing or clinical feasibility model.
- Results depend on the assumed shortage price and capacity limits.

<details>
<summary>Saved sources, verification and reproduction</summary>

The builder verifies the existing SHA-256 manifests, agreement between the saved two-origin records, Rule agreement with the clean checkpoint, monthly sums, hindsight bounds, numerical provenance, table structure and figure links. All checks passed. Monthly cost arithmetic only reconstructs components of the saved plans; no model or optimisation routine is called.

Checker correction: Rule, LP and Protected are checked against their hashed frozen plans. Hindsight is checked against the saved scored plans and totals from both the fair comparison and clean checkpoint, which agree to 1e-6 relative. A scored-only unit test covers this distinction; no saved data or results changed.

The source files are:

- **September 2025:** [actuals](outputs/fair_comparison/O1_sep2025/actuals.csv), [forecasts](outputs/fair_comparison/O1_sep2025/forecasts.csv), [accuracy](outputs/fair_comparison/O1_sep2025/accuracy.csv), [plans](outputs/fair_comparison/O1_sep2025/plans.csv), [totals](outputs/fair_comparison/O1_sep2025/totals.csv); [clean-checkpoint totals](outputs/clean_checkpoint/O1_sep2025/totals.csv); [clean-checkpoint scored plans](outputs/clean_checkpoint/O1_sep2025/plans.csv).
- **March 2026:** [actuals](outputs/fair_comparison/O2_mar2026/actuals.csv), [forecasts](outputs/fair_comparison/O2_mar2026/forecasts.csv), [accuracy](outputs/fair_comparison/O2_mar2026/accuracy.csv), [plans](outputs/fair_comparison/O2_mar2026/plans.csv), [totals](outputs/fair_comparison/O2_mar2026/totals.csv); [clean-checkpoint totals](outputs/clean_checkpoint/O2_mar2026/totals.csv); [clean-checkpoint scored plans](outputs/clean_checkpoint/O2_mar2026/plans.csv).
- **Rule and common scoring:** [rule_plans](outputs/optimisation_value/rule_plans.csv), [monthly_costs](outputs/optimisation_value/monthly_costs.csv), [costs](outputs/optimisation_value/costs.csv).
- **Prior-year comparison:** Seasonal naive's [saved March forecast](outputs/fair_comparison/O2_mar2026/forecasts.csv) repeats the same calendar months of 2025.
- **Definitions:** [fair-comparison configuration](outputs/fair_comparison/config.json) and [registered optimisation-value configuration](outputs/optimisation_value/config.json).

The original two-origin figure builder is [monthly_views.py](scripts/case_study/monthly_views.py). For the monthly extension, run `python -B scripts/monthly_origins/interpret.py`, then `python -B scripts/monthly_origins/figures.py` and `python -B scripts/monthly_origins/report.py` from the repository root using the verified outputs.

</details>

## All monthly origins: April 2025 to April 2026

September favoured SARIMA-24 with LP. March favoured Seasonal naive with protection. We repeated the same six-month planning exercise at every monthly origin from April 2025 to April 2026 to see which findings repeated.

**How to read the figures.** Each summary heatmap column is the month the plan was made. Hindsight is the cheapest feasible plan if all six future gaps are known. Subtracting its cost shows how much extra each plan cost in its own window, so an expensive winter does not automatically make its forecasts look worse. Savings compare two plans for the same forecast.

**Which origins count?** Ranks and cross-method averages use the 11 origins with all five methods. SARIMA-24 failed to converge for occupied beds in October 2025 and for available beds in January 2026. The other four methods still have results in those columns. Neighbouring plans share five target months, so the counts describe about two to three independent six-month windows.

### Which forecasts missed, and which plans were cheapest?

![B1. Average forecast error in beds by method and origin](figures/monthly_origins/summary_signed_gap_error.png)

[Vector figure](figures/monthly_origins/summary_signed_gap_error.svg)

**B1. Forecast direction.** Positive values mean the forecast gap was too low on average over six months. Every method switched direction at least once. For example, Seasonal naive went from 300 beds too high in September to 591 too low in March.

![B2. Six-month LP cost above hindsight in millions](figures/monthly_origins/summary_lp_excess.png)

[Vector figure](figures/monthly_origins/summary_lp_excess.svg)

**B2. Cost of the optimised forecast.** All five methods had the cheapest LP at least once across the 11 common origins. Seasonal naive and SARIMA-24 had nearly identical mean costs above hindsight (5.51m and 5.52m). SARIMA-24 won most often and never finished last, while Persistence won in March and finished last at 7 origins.

| Method | Forecast low / high | LP saved / tied vs Rule | LP wins (11 origins) | LP rank range | Mean LP excess |
| --- | --- | --- | --- | --- | --- |
| Mean-24 | 7 / 6 | 0 / 13 | 1 | 1–4 | 6.75m |
| Persistence | 5 / 8 | 0 / 13 | 1 | 1–5 | 16.12m |
| Seasonal naive | 5 / 8 | 9 / 4 | 3 | 1–5 | 5.51m |
| SARIMA-24 | 6 / 5 | 9 / 2 | 4 | 1–4 | 5.52m |
| ExtraTrees-24 | 6 / 7 | 12 / 1 | 2 | 1–5 | 6.18m |

The first two count columns use each method's available origins (13 for four methods and 11 for SARIMA-24). LP wins, ranks and mean excess compare all five on the same 11 origins. A win is the lowest realised cost before rounding.

**Does the season pick the winner?** Among the 7 common April–September starts, SARIMA-24 won 4 times, ExtraTrees-24 2 times and Seasonal naive once. Among the 4 common October–March starts, Seasonal naive won twice, and Mean-24 and Persistence once each.

The target months explain more of the winter story. In all 5 common windows that included the January 2026 peak, Seasonal naive (3 LP wins) or SARIMA-24 (2) was cheapest. Together they won 2 of the other 6. Those winter plans share the same peak, so this is weak evidence for choosing a method by season in another year.

### What did optimisation and protection buy?

![B3. Saving from optimising each forecast instead of following the Rule](figures/monthly_origins/summary_value_of_optimisation.png)

[Vector figure](figures/monthly_origins/summary_value_of_optimisation.svg)

**B3. Optimisation.** LP saved money in all 30 forecasts that fell and then rose within the six months, and tied the Rule in the other 33 at the registered tolerance. Keeping a bed open through a one-month dip avoids paying to close and reopen it, and sometimes covers a shortage as well. Mean-24 and Persistence forecast the same gap every month, so the Rule already holds the capacity steady and there is no dip to bridge.

![B4. Saving or extra cost from protecting each optimised forecast](figures/monthly_origins/summary_value_of_protection.png)

[Vector figure](figures/monthly_origins/summary_value_of_protection.svg)

**B4. Protection.** Across 55 method–origin pairs at the 11 common origins, optimisation saved 0.50m on average and protection saved a further 3.89m. Protection cut the bill in 27 of those pairs and raised it in 28. Avoided shortage charges outweighed the extra cost of running the protected schedules.

![B5. Cheapest forecast-based plan and its excess cost above hindsight](figures/monthly_origins/summary_best_plan.png)

[Vector figure](figures/monthly_origins/summary_best_plan.svg)

**B5. The cheapest plan after costs were known.** An ordinary LP won at 6 of the 11 common origins and a Protected plan won at 5. In November and December, Seasonal naive had the cheapest ordinary LP, but ExtraTrees-24 became cheaper once its plan included protection.

| Origin | Lowest LP cost | Lowest cost across all plans | Winner above hindsight |
| --- | --- | --- | --- |
| Apr 2025 | ExtraTrees-24 · 12.95m | ExtraTrees-24 (LP) · 12.95m | 1.23m |
| May 2025 | SARIMA-24 · 13.29m | SARIMA-24 (LP) · 13.29m | 1.43m |
| Jun 2025 | ExtraTrees-24 · 12.91m | ExtraTrees-24 (LP) · 12.91m | 1.18m |
| Jul 2025 | Seasonal naive · 15.21m | Seasonal naive (LP) · 15.21m | 1.92m |
| Aug 2025 | SARIMA-24 · 15.74m | SARIMA-24 (LP) · 15.74m | 0.99m |
| Sep 2025 | SARIMA-24 · 16.11m | SARIMA-24 (LP) · 16.11m | 0.87m |
| Oct 2025 | Unranked | SARIMA-24 unavailable | — |
| Nov 2025 | Seasonal naive · 18.60m | ExtraTrees-24 (Protected) · 16.29m | 2.06m |
| Dec 2025 | Seasonal naive · 21.89m | ExtraTrees-24 (Protected) · 15.72m | 0.88m |
| Jan 2026 | Unranked | SARIMA-24 unavailable | — |
| Feb 2026 | Mean-24 · 15.02m | SARIMA-24 (Protected) · 14.86m | 2.14m |
| Mar 2026 | Persistence · 13.75m | Seasonal naive (Protected) · 13.56m | 0.89m |
| Apr 2026 | SARIMA-24 · 15.78m | Seasonal naive (Protected) · 14.20m | 0.95m |

Reading across a row shows whether protection changed the winner. In March it did, even though optimisation had already saved Seasonal naive money compared with following its forecast.

![C. Value of protection versus the average signed forecast error](figures/monthly_origins/protection_vs_error_direction.png)

[Vector figure](figures/monthly_origins/protection_vs_error_direction.svg)

**C. When protection paid.** Each point pairs one forecast with the saving from protecting it. The Spearman rank correlation of 0.926 across the 63 available pairs means larger under-forecasts tended to go with larger savings. The same calculation using just the 55 common-origin pairs gave 0.911.

| Six-month mean error | Protection reduced cost | Protection added cost |
| --- | --- | --- |
| Forecast too low | 26 | 3 |
| Forecast too high | 8 | 26 |

Protection helped in 26 of 29 under-forecasts and in 8 of 34 over-forecasts. A high six-month average can hide a costly monthly shortage. At the July 2025 origin, Mean-24 averaged 276 beds too high but forecast 3,586 beds for January against an actual gap of 4,680. Protecting it saved 2.60m.

### Where September and March sit

September's 0.43m optimisation saving for Seasonal naive was exactly its median over 13 origins. Protection raised SARIMA-24's September cost by 3.35m, its second-largest increase in 11 origins. March was Seasonal naive's strongest window for both steps, with 3.48m saved by LP and another 12.06m by protection. The two case studies illustrate a mechanism, but March gives an unusually large example.

### What generalises

- **Holds across these windows.** Optimising the same forecast never materially increased realised cost across the 63 comparisons. The shape of the forecast explained where optimisation saved money.
- **Depends on the window.** The cheapest forecasting method changed. Protection usually paid when the forecast was low, but the timing and size of monthly shortages also mattered.

### A planner's rule?

Under these cost assumptions, optimise the forecast when freezing a plan. A planner can see a dip before acting. The direction of the future forecast error only becomes known later, so these windows support using past errors to assess the cost of protection but do not establish a reliable trigger for switching it on. The verified schedules establish what happened in these windows. Their overlap limits confidence in applying the seasonal pattern to another winter.

The underlying results include the forecasts, the monthly accounts for each plan, the common-origin rankings and the verification checks. [Forecasts](outputs/monthly_origins/forecasts.csv) · [Monthly costs](outputs/monthly_origins/monthly_costs.csv) · [Rankings](outputs/monthly_origins/winners.csv) · [Interpretation data](outputs/monthly_origins/interpretation.json) · [Verification](outputs/monthly_origins/verification.md)

<details>

<summary>Per-origin heatmaps</summary>

Each row follows one frozen six-month schedule. The first month includes the cost of opening beds. A monthly cell gives the cost and the beds short or spare. The Total column uses its own colour scale and sums bed-months, showing shortfall when it rounds to at least one bed-month and spare capacity otherwise.

**April 2025**

![Monthly costs and beds short or spare for April 2025](figures/monthly_origins/origin_2025-04_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-04_heatmap.svg)

ExtraTrees-24 kept 2,971 beds open in Aug 2025 with LP versus 2,390 with the Rule, saving 0.20m over the full window.

**May 2025**

![Monthly costs and beds short or spare for May 2025](figures/monthly_origins/origin_2025-05_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-05_heatmap.svg)

SARIMA-24 kept 2,507 beds open in Aug 2025 with LP versus 1,457 with the Rule, saving 1.81m over the full window.

**June 2025**

![Monthly costs and beds short or spare for June 2025](figures/monthly_origins/origin_2025-06_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-06_heatmap.svg)

ExtraTrees-24 kept 2,991 beds open in Aug 2025 with LP versus 2,774 with the Rule, saving 0.08m over the full window.

**July 2025**

![Monthly costs and beds short or spare for July 2025](figures/monthly_origins/origin_2025-07_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-07_heatmap.svg)

Seasonal naive kept 4,419 beds open in Dec 2025 with LP versus 3,181 with the Rule, saving 0.43m over the full window.

**August 2025**

![Monthly costs and beds short or spare for August 2025](figures/monthly_origins/origin_2025-08_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-08_heatmap.svg)

SARIMA-24 kept 4,522 beds open in Dec 2025 with LP versus 3,269 with the Rule, saving 0.44m over the full window.

**September 2025**

![Monthly costs and beds short or spare for September 2025](figures/monthly_origins/origin_2025-09_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-09_heatmap.svg)

SARIMA-24 kept 4,472 beds open in Dec 2025 with LP versus 3,228 with the Rule, saving 0.44m over the full window.

**October 2025**

![Monthly costs and beds short or spare for October 2025](figures/monthly_origins/origin_2025-10_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-10_heatmap.svg)

Seasonal naive cost 17.60m with LP, and protection saved 0.34m over the six months.

**November 2025**

![Monthly costs and beds short or spare for November 2025](figures/monthly_origins/origin_2025-11_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-11_heatmap.svg)

For ExtraTrees-24, protection made its largest reduction in shortfall in May 2026, from 1,147 beds with LP to 0 beds.

**December 2025**

![Monthly costs and beds short or spare for December 2025](figures/monthly_origins/origin_2025-12_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2025-12_heatmap.svg)

For ExtraTrees-24, protection made its largest reduction in shortfall in May 2026, from 899 beds with LP to 0 beds.

**January 2026**

![Monthly costs and beds short or spare for January 2026](figures/monthly_origins/origin_2026-01_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2026-01_heatmap.svg)

Seasonal naive cost 25.13m with LP, and protection saved 8.70m over the six months.

**February 2026**

![Monthly costs and beds short or spare for February 2026](figures/monthly_origins/origin_2026-02_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2026-02_heatmap.svg)

For SARIMA-24, protection made its largest reduction in shortfall in Jul 2026, from 793 beds with LP to 0 beds.

**March 2026**

![Monthly costs and beds short or spare for March 2026](figures/monthly_origins/origin_2026-03_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2026-03_heatmap.svg)

For Seasonal naive, protection made its largest reduction in shortfall in Jul 2026, from 884 beds with LP to 0 beds.

**April 2026**

![Monthly costs and beds short or spare for April 2026](figures/monthly_origins/origin_2026-04_heatmap.png)

[Vector figure](figures/monthly_origins/origin_2026-04_heatmap.svg)

For Seasonal naive, protection made its largest reduction in shortfall in Jul 2026, from 884 beds with LP to 0 beds.

</details>

Neighbouring origins share up to five target months, so these 13 origins are about two to three independent windows; costs are illustrative model units (validated: False).

## Averaging the forecasts

Every individual method had windows when it forecast too high and others when it forecast too low. An equal-weight average could cancel opposing errors before the planner decides how many beds to open. September 2025 and March 2026 suggested the idea, so we excluded them from the primary test.

**Method.** Combo averages the five occupied forecasts and the five available forecasts separately, then calculates the gap. To protect Combo, we average the five errors from the same historical backtest and forecast month, preserving their six-month paths. Every method faces the same prices, capacity limits and number of historical scenarios. The primary test (P1) has 23 origins. P2 includes all 25 rolling origins. P3 compares the 11 complete origins in the 13-origin monthly study. The two four-method averages are shown separately.

Registered config SHA-256: `a04097b8ea7e34bcbde295b4cf5236312922ca27b33c235df84ade43613097d4`. [Design and origin lists](outputs/forecast_combination/config.json)

### Did the combination win?

**On average, yes in the primary test.** Combo Protected cost 3.13m above hindsight per six-month plan, compared with 3.66m for Seasonal naive, the cheapest individual Protected method. Combo had the lowest mean in 5 of the six every-sixth-origin subsets and beat every individual method more often than it lost. That meets the registered clear-winner rule. The overlapping primary origins represent about four independent windows.

| Method | Mean LP excess | Mean Protected excess | Worst excess (LP / Protected) | Mean absolute bias (beds) | Mean signed bias / SD (beds) | Combo Protected wins / ties / losses |
| --- | --- | --- | --- | --- | --- | --- |
| Combo | 6.00m | 3.13m | 20.17m / 5.69m | 288.6 | -18.9 / 379.9 | — |
| Mean-24 | 7.28m | 6.38m | 15.17m / 10.21m | 402.2 | -181.5 / 428.4 | 23 / 0 / 0 |
| Persistence | 14.52m | 7.53m | 64.94m / 20.82m | 889.4 | -61.0 / 1,139.1 | 21 / 0 / 2 |
| Seasonal naive | 4.30m | 3.66m | 14.85m / 5.38m | 260.7 | -54.9 / 301.4 | 13 / 1 / 9 |
| SARIMA-24 | 10.48m | 4.54m | 38.54m / 16.89m | 440.9 | +310.6 / 500.8 | 13 / 2 / 8 |
| ExtraTrees-24 | 6.88m | 3.94m | 23.82m / 9.50m | 450.6 | -107.5 / 514.2 | 19 / 1 / 3 |

Costs are millions above what a planner with perfect foresight would have spent. A win or loss requires a difference greater than 1% of that origin's hindsight cost. Bias is the average actual gap minus forecast gap within a window. The absolute column measures the usual size of that miss. SD shows how much the signed miss varied between windows.

**The other hypotheses had different answers.** In P1, lowest mean Protected cost (H1) held. Lowest worst Protected cost (H2) and smallest mean absolute bias (H3) both went to Seasonal naive. In P3 only H3 held for Combo (218 beds). Seasonal naive had lower mean Protected excess (2.55m vs 2.64m) and narrowly lower worst excess (3.8505m vs 3.8520m). This covers about two to three independent windows.

![Protected excess cost by origin, with the two idea-forming origins marked](figures/forecast_combination/excess_by_origin.png)

[Vector figure](figures/forecast_combination/excess_by_origin.svg)

Combo Protected beat Seasonal naive Protected at 13 primary origins, tied at 1 and cost more at 9. When compared with the cheapest individual Protected plan chosen *after* each window, Combo cost materially more at 16 of 23. That individual winner changed with the window.

![Six-month mean gap error for individual and averaged forecasts](figures/forecast_combination/signed_error_by_origin.png)

[Vector figure](figures/forecast_combination/signed_error_by_origin.svg)

In July 2025, individual methods missed by 374 beds on average in absolute terms, yet their average forecast was only 9 beds high. In December 2025 all five forecast too low, leaving Combo 771 beds short in its forecast. Protection then saved Combo 17.84m, although Protected ExtraTrees-24 still cost 1.45m less.

In the primary test, all five forecast high at 4 origins. Protection added cost at every one. Their only unanimous under-forecast was December 2025. Across all 23 origins, Combo's signed miss averaged 19 beds high, but the average *size* of its miss was 289 beds. Those two numbers answer different questions.

![90th-percentile and worst-origin LP and Protected excess cost in the primary test](figures/forecast_combination/worst_case.png)

[Vector figure](figures/forecast_combination/worst_case.svg)

Combo had the lowest Protected 90th-percentile excess (4.17m). Its worst Protected window cost 5.69m above hindsight in Feb 2025, while Seasonal naive's worst was 5.38m. Combo kept most of the expensive outcomes smaller, but Seasonal naive had the smaller single worst loss.

![Protected excess at all monthly origins including the four-method fallback](figures/forecast_combination/summary_heatmap.png)

[Vector figure](figures/forecast_combination/summary_heatmap.svg)

Seasonal naive had a 0.09m mean advantage over Combo on the 11 comparable monthly origins. Where SARIMA was unavailable, four-method Combo Protected excess was 1.83m in October 2025 and 2.47m in January 2026. Those cells sit outside the rankings.

### What did averaging and protection each contribute?

Start with Seasonal naive LP at 4.30m above hindsight per primary origin. Switching to Combo LP **added 1.70m**, taking the excess to 6.00m. Protecting that same Combo forecast **saved 2.87m**, bringing it to 3.13m. Together the two steps saved 1.17m against Seasonal naive LP and 0.53m against Seasonal naive Protected. The secondary median forecast had mean LP excess of 5.52m.

**Was the protection margin better sized?** Combo's average margin at the 90% quantile was 1,374 beds, smaller than any individual's. Actual demand gaps fell below or at its upper forecast in 137 of 138 origin-month pairs (99.3%), above the 90% target. At horizon one it covered 22 of 23 and at each of horizons two through six it covered all 23. Individual coverage ranged from 92.8% to 100%. A smaller margin still left Combo conservative here.

### What should a planner use?

At the assumed shortage price of 5,000, Combo Protected has the strongest case for minimising average cost on the primary origins. A planner prioritising the worst bill has evidence for Seasonal naive Protected. Price matters too. At 2,500 Seasonal naive had the lowest mean Protected excess (1.94m vs Combo's 2.24m). At 10,000 Combo ranked first (3.89m), an indicative sensitivity result. With about four independent primary windows, confidence in carrying that choice into another year is limited.

### How we checked this

Before scoring, we checked that averaging errors from matched backtests gives exactly the error of the average forecast, using the same information and number of scenarios as each rival. We froze the plans, matched the individual methods' costs exactly to both earlier studies, and reproduced one month's shortfall and bill by hand. Combo LP saved money against the Rule at 19 origins and tied at 8. Combo's historical error range narrowed in 570 of 690 comparisons, yet in February 2025 all five methods forecast high and Combo still missed high by 806 beds. Removing any two primary origins left its average Protected advantage over each individual intact. [Logic and sanity review](outputs/forecast_combination/logic_review.md) · [Verification](outputs/forecast_combination/verification.md)

Putting the two idea-forming origins back in (P2) gives a mean Protected excess of 3.06m for Combo and 3.50m for Seasonal naive. [All endpoints](outputs/forecast_combination/endpoints.csv) · [Coverage](outputs/forecast_combination/coverage.csv) · [Cost decomposition](outputs/forecast_combination/decomposition_summary.csv)

For the forecasting rationale behind equal-weight combinations, see [Bates and Granger](https://doi.org/10.1057/jors.1969.103), [Clemen](https://www.sciencedirect.com/science/article/pii/0169207089900125), [Forecasting: Principles and Practice](https://otexts.com/fpp3/practical.html#forecast-combinations) and [the M4 competition](https://doi.org/10.1016/j.ijforecast.2018.06.001).

The idea was formed at Sep 2025 and Mar 2026, which are excluded from the main test. Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).
