## Forecast vs decision method: September 2025 origin

All forecasting methods use the same 24 months before the origin (October 2023 - September 2025) and forecast the next six months (October 2025 - March 2026). "Rule" opens exactly the extra capacity the forecast says is needed each month, ignoring costs. "LP" feeds the same forecast into a linear program that chooses monthly extra capacity to minimise running (500 per bed-month), opening (550), closing (300) and shortfall (5,000 per bed-month) costs, with capacity carrying over between months, at most 5,000 beds opened or closed per month, at most 15,000 in total, and zero starting capacity. SARIMA-full (trained on all history) is shown only as a reference.

### Forecasts by series

Seasonal naive and SARIMA-24 are shown against the actual values; all methods are in the table below.

![Occupied beds: forecast vs actual](figures/sep2025_forecast_occupied.png)

**Occupied beds:** Seasonal naive was closer in 6 of 6 months (MAE 1,028 vs 1,039). Best of all methods: ExtraTrees-24 (MAE 677).

![Available beds: forecast vs actual](figures/sep2025_forecast_available.png)

**Available beds:** SARIMA-24 was closer in 6 of 6 months (MAE 808 vs 842). Best of all methods: ExtraTrees-24 (MAE 764).

![Planning gap: forecast vs actual](figures/sep2025_forecast_gap.png)

**Planning gap:** Seasonal naive was closer in 5 of 6 months; SARIMA-24 in 1 (January). Best of all methods: Seasonal naive (MAE 313).

### Why the gap is the forecast that matters

The planner never uses occupied or available beds on their own; it uses the gap, occupied / 0.90 - available. Errors in the two forecasts can cancel or add up: overestimating occupied beds raises the gap, while overestimating available beds lowers it. Seasonal naive and SARIMA-24 both overestimated occupied and available beds by similar amounts, so their errors largely cancelled and their gaps were close to the actual values. ExtraTrees-24 was the most accurate on both bed series, but it overestimated available beds by more than occupied beds in most months, so its gap came out too low and its plans ran short.

| Forecast | Training data | Decision | Gap MAE (beds) | Mean signed gap error (beds) | Operating cost | Shortfall cost | Total realised cost | Excess over hindsight |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Mean-24 | 24 months | Rule | 709 | 289 | 12.74 | 14.98 | 27.72 | 12.48 |
| Mean-24 | 24 months | LP | 709 | 289 | 12.74 | 14.98 | 27.72 | 12.48 |
| Persistence | 24 months | Rule | 880 | 545 | 11.83 | 21.38 | 33.21 | 17.97 |
| Persistence | 24 months | LP | 880 | 545 | 11.83 | 21.38 | 33.21 | 17.97 |
| Seasonal naive | 24 months | Rule | 313 | -300 | 16.38 | 0.19 | 16.57 | 1.33 |
| Seasonal naive | 24 months | LP | 313 | -300 | 15.95 | 0.19 | 16.14 | 0.90 |
| SARIMA-24 | 24 months | Rule | 346 | -346 | 16.54 | 0.00 | 16.54 | 1.31 |
| SARIMA-24 | 24 months | LP | 346 | -346 | 16.11 | 0.00 | 16.11 | 0.87 |
| ExtraTrees-24 | 24 months | Rule | 454 | 205 | 13.88 | 9.89 | 23.77 | 8.54 |
| ExtraTrees-24 | 24 months | LP | 454 | 205 | 13.77 | 9.89 | 23.66 | 8.42 |
| SARIMA-full | full history (reference) | LP | 356 | -356 | 16.15 | 0.00 | 16.15 | 0.91 |
| Hindsight | - | LP | - | - | 15.24 | 0.00 | 15.24 | 0.00 |

Costs in millions of model cost units. Mean signed gap error = actual - forecast.

![September 2025 origin: realised cost by method](figures/sep2025_realised_cost_by_plan.png)

### Month by month: cost

![September 2025 origin: what each plan cost, month by month](figures/sep2025_monthly_cost_heatmap.png)

Monthly cost includes running, opening and closing beds plus the 5,000-per-bed shortfall penalty; 'short' and 'spare' show how far each plan was below or above the actual gap that month.

**Result.** Seasonal naive had the lowest gap MAE (313 beds), and SARIMA-24 LP had the lowest realised cost (16.11m, 0.87m above hindsight). SARIMA-24 LP, Seasonal naive LP and SARIMA-full LP were within 0.04m of one another. Rule and LP plans were identical for Mean-24 and Persistence, and LP was cheaper than Rule by 0.43m for Seasonal naive, 0.44m for SARIMA-24 and 0.11m for ExtraTrees-24 (differences computed before rounding). This is a single origin (one six-month window), SARIMA-24 and ExtraTrees-24 are fitted on only 24 months, and costs are illustrative model units (validated: False).

## Does more training data help? 24 vs 36 months vs full history (September 2025 origin)

SARIMA and ExtraTrees are retrained on 24 months, 36 months and all history before the origin, with unchanged specifications; Seasonal naive is the same in every case and serves as the benchmark.

| Model | Training window | Training rows / labels | Occupied MAE | Available MAE | Gap MAE | Gap months closer than SN | LP total cost | Shortfall cost | Excess over hindsight |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Seasonal naive | same month one year earlier | - | 1,028 | 842 | 313 | - | 16.14 | 0.19 | 0.90 |
| SARIMA-24 | Oct 2023 - Sep 2025 | 24 labels | 1,039 | 808 | 346 | 1 of 6 | 16.11 | 0.00 | 0.87 |
| SARIMA-36 | Oct 2022 - Sep 2025 | 36 labels | 1,037 | 815 | 339 | 1 of 6 | 16.11 | 0.03 | 0.87 |
| SARIMA-full | Dec 2020 - Sep 2025 | 58 labels | 1,050 | 805 | 362 | 1 of 6 | 16.17 | 0.00 | 0.93 |
| ExtraTrees-24 | Oct 2023 - Sep 2025 | 12 rows | 677 | 764 | 454 | 3 of 6 | 23.66 | 9.89 | 8.42 |
| ExtraTrees-36 | Oct 2022 - Sep 2025 | 24 rows | 834 | 688 | 272 | 3 of 6 | 16.00 | 0.50 | 0.76 |
| ExtraTrees-full | Dec 2020 - Sep 2025 | 46 rows | 889 | 792 | 272 | 3 of 6 | 16.73 | 1.49 | 1.49 |
| Hindsight | - | - | - | - | - | - | 15.24 | 0.00 | 0.00 |

Costs in millions of model cost units. SARIMA-full here fits all 58 months as labels, so it differs from the SARIMA-full reference in the section above (46 labels).

![Gap MAE by training window](figures/sep2025_windows_gap_mae.png)

ExtraTrees-full (271.8) and ExtraTrees-36 (272.5) had the lowest gap MAEs; all three SARIMA variants (339-362) were above Seasonal naive (313).

![Occupied and available bed MAE by training window](figures/sep2025_windows_bed_mae.png)

ExtraTrees-24 had the lowest occupied-bed MAE (677) and ExtraTrees-36 the lowest available-bed MAE (688); every SARIMA variant was above Seasonal naive on occupied beds and below it on available beds.

![LP total realised cost by training window](figures/sep2025_windows_total_cost.png)

ExtraTrees-36 LP had the lowest total realised cost (16.00m), followed by SARIMA-36 LP and SARIMA-24 LP (both 16.11m); Seasonal naive LP cost 16.14m and Hindsight 15.24m.

![Planning gap by month and training window](figures/sep2025_windows_gap_forecasts.png)

ExtraTrees-24 gave the lowest gap forecast in every month, and every forecast was above the actual gap in December 2025.

![Monthly realised cost by training window](figures/sep2025_windows_monthly_cost_heatmap.png)

ExtraTrees-24 LP was short in 5 of 6 months, ExtraTrees-full LP in 3, ExtraTrees-36 LP, SARIMA-36 LP and Seasonal naive LP in 1 (January 2026), and SARIMA-24 LP and SARIMA-full LP in none.

**Result.** For SARIMA, more data did not consistently lower gap MAE or cost (gap MAE 346, 339, 362 and LP cost 16.11m, 16.11m, 16.17m for 24 months, 36 months and full history); for ExtraTrees, moving from 24 to 36 months lowered gap MAE from 454 to 272 and LP cost from 23.66m to 16.00m, while full history gave a gap MAE of 272 and an LP cost of 16.73m. ExtraTrees-36 and ExtraTrees-full beat Seasonal naive on gap MAE (313), and ExtraTrees-36, SARIMA-36 and SARIMA-24 beat it on LP cost (16.14m), by at most 0.14m. This is a single origin (one six-month window); costs are illustrative model units (validated: False).
