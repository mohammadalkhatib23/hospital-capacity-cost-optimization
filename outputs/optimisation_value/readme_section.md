## Does optimisation help? Forecast alone vs optimisation vs protection

Does turning a forecast into an optimised capacity plan reduce the cost once real demand is known? We separate the value of optimisation from the additional value of protection against forecast errors. Earlier evidence compared forecast-following and optimisation at only two origins; this extends that comparison to the full retained set.

### Method

We reuse the saved forecasts at all 25 origins, with no model refitting. **Rule** follows the forecast gap, subject to the capacity and monthly ramp limits. **LP** chooses the cheapest schedule for that same point forecast. **Protected** optimises across that method’s saved historical-error scenarios. The LP, Protected and hindsight plans and scores are reused unchanged at a shortfall penalty of 5,000. Every plan starts from zero additional capacity and stays fixed for six months; hindsight knows the actual gaps.

Config SHA-256: `b95a791442544a9a93ccfa9f0904220c4571b85f2b51eb21a278d318edba7c90`.
Rule-plan SHA-256: `1dea041a3fdf0828c7bc3e25b48b8accf7c953f881ec9ead511865e8f1b7cc03`.

All new Rule plans were written and hashed before full-run scoring. Phase 0 first reproduced the two already evaluated anchors. The expectations and metrics were fixed before new scoring, but this is not a blind experiment: the forecasts and other plans had already been evaluated.

### Table 1. Mean costs and value added

| Method | Rule | LP | Protected | Hindsight | Optimisation value (C/E/D) | Protection value (help/tie/hurt) | Gap closed: optimisation / protection |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | 20.71 | 20.71 | 19.67 | 13.45 | 0.00 (0/25/0) | 1.05 (12/0/13) | 0.0% / 14.4% |
| Persistence | 27.57 | 27.57 | 20.75 | 13.45 | 0.00 (0/25/0) | 6.82 (13/0/12) | 0.0% / 48.3% |
| Seasonal naive | 18.86 | 17.97 | 16.96 | 13.45 | 0.90 (17/8/0) | 1.01 (10/0/15) | 16.6% / 18.7% |
| SARIMA-24 | 24.88 | 23.39 | 17.90 | 13.45 | 1.49 (19/6/0) | 5.49 (14/0/11) | 13.0% / 48.0% |
| ExtraTrees-24 | 20.49 | 20.17 | 17.32 | 13.45 | 0.33 (20/5/0) | 2.84 (11/0/14) | 4.6% / 40.4% |
| Pooled | 22.50 | 21.96 | 18.52 | 13.45 | 0.54 (56/69/0) | 3.44 (60/0/65) | 6.0% / 38.0% |

All costs and benefits are **millions of illustrative model units per six-month plan**. Optimisation value is Rule minus LP; C/E/D counts origins where LP is cheaper/equal/dearer, using a relative tolerance of 1e-6 against Rule cost. Protection value is LP minus Protected; help/tie/hurt uses 1% of that origin’s hindsight cost as the tie band. Gap-closed shares divide each summed benefit by the summed Rule-minus-hindsight gap; they are not averages of individual percentages. Pooled gives equal weight to each method-origin case and repeats each origin’s hindsight once per method. Tiny negative optimisation values for the two flat methods are solver rounding; rounded zeros above do not change the saved raw values.

Optimisation never cost more beyond the registered tolerance: LP was cheaper in **56 of 125** cases and equal in **69**. Protection cost more in **65 of 125** cases, but helped on average.

<details>
<summary>Which cases were equal?</summary>

| Method | Equal cases | Origins (YYYY-MM) |
| --- | --- | --- |
| Mean-24 | 25 | All retained origins |
| Persistence | 25 | All retained origins |
| Seasonal naive | 8 | 2024-06, 2024-11, 2024-12, 2025-01, 2025-02, 2025-11, 2025-12, 2026-02 |
| SARIMA-24 | 6 | 2024-11, 2024-12, 2025-01, 2025-02, 2025-11, 2026-02 |
| ExtraTrees-24 | 5 | 2024-11, 2024-12, 2025-01, 2025-02, 2025-11 |

Equal means within the registered cost tolerance, not necessarily bit-for-bit equality. The [full equal-case list](outputs/optimisation_value/equal_cases.json) records every origin.

</details>

**Figure 1. Mean cost at each planning step.** [SVG](figures/optimisation_value/chain_by_method.svg)

![Mean cost from forecast alone to optimisation and protection](figures/optimisation_value/chain_by_method.png)

**Figure 2. Optimisation value at each origin.** [SVG](figures/optimisation_value/value_of_optimisation_by_origin.svg)

![Value of optimisation at each origin, grouped by method](figures/optimisation_value/value_of_optimisation_by_origin.png)

Positive values mean LP was cheaper. Each marker is one origin; small vertical offsets only separate overlapping markers.

### Table 2. Why the LP helps

The table shows **Rule -> LP**. Opening, closing and running costs are mean millions per plan; events and shortfall bed-months are also averages per plan. A close-then-reopen event counts a removal month once when an addition follows within the next two months.

| Method | Opening cost | Closing cost | Running cost | Close-then-reopen events | Shortfall bed-months |
| --- | --- | --- | --- | --- | --- |
| Mean-24 | 1.98 -> 1.98 | 0.00 -> 0.00 | 10.79 -> 10.79 | 0.00 -> 0.00 | 1588.6 -> 1588.6 |
| Persistence | 1.92 -> 1.92 | 0.00 -> 0.00 | 10.48 -> 10.48 | 0.00 -> 0.00 | 3035.1 -> 3035.1 |
| Seasonal naive | 3.01 -> 2.64 | 0.57 -> 0.37 | 10.43 -> 10.76 | 1.04 -> 0.44 | 972.6 -> 839.8 |
| SARIMA-24 | 2.85 -> 2.50 | 0.59 -> 0.40 | 9.46 -> 9.78 | 1.20 -> 0.40 | 2395.0 -> 2141.6 |
| ExtraTrees-24 | 2.45 -> 2.33 | 0.26 -> 0.19 | 10.64 -> 10.75 | 1.24 -> 0.44 | 1427.7 -> 1378.4 |
| Pooled | 2.44 -> 2.28 | 0.28 -> 0.19 | 10.36 -> 10.51 | 0.70 -> 0.26 | 1883.8 -> 1796.7 |

For the three changing forecasts, LP makes fewer close-then-reopen moves and spends less on opening and closing. It keeps more capacity running through dips, which raises running cost but also lowers realised shortfalls in these trials. The flat forecasts give the same schedules within solver precision, so there is no material optimisation benefit to add. This is observed performance, not a guarantee: LP minimises forecast cost and could be worse on different realised outcomes.

Protection helped in **60** cases and hurt in **65** cases, yet its pooled mean benefit was **3.44m**. Its median benefit was **-0.38m**. Larger gains in some windows outweighed more frequent losses; a positive average does not mean protection helped every method at every origin.

### Distribution and robustness

Medians and extremes show how uneven the benefits were. Each entry below is median [minimum, maximum], in millions of model units.

| Method | Optimisation value | Protection value | Total Rule-minus-Protected value |
| --- | --- | --- | --- |
| Mean-24 | 0.00 [0.00, 0.00] | -0.31 [-8.01, 10.72] | -0.31 [-8.01, 10.72] |
| Persistence | 0.00 [0.00, 0.00] | 0.77 [-8.44, 44.12] | 0.77 [-8.44, 44.12] |
| Seasonal naive | 0.43 [0.00, 4.17] | -1.71 [-3.59, 12.06] | -0.82 [-3.33, 15.54] |
| SARIMA-24 | 0.64 [0.00, 4.52] | 2.99 [-3.89, 21.65] | 6.67 [-3.46, 24.25] |
| ExtraTrees-24 | 0.11 [0.00, 3.49] | -0.59 [-3.76, 18.04] | -0.44 [-3.69, 18.17] |
| Pooled | 0.00 [0.00, 4.52] | -0.38 [-8.44, 44.12] | 0.13 [-8.44, 44.12] |

Each range below is the smallest to largest mean across the six offset subsets. Within a subset, origins have non-overlapping target windows; subsets overlap each other. These are descriptive ranges, not confidence intervals.

| Method | All origins: optimisation | All origins: protection | Excluding original two: optimisation | Excluding original two: protection |
| --- | --- | --- | --- | --- |
| Mean-24 | 0.00 to 0.00 | -0.50 to 2.00 | 0.00 to 0.00 | -0.89 to 1.95 |
| Persistence | 0.00 to 0.00 | -1.73 to 12.91 | 0.00 to 0.00 | -1.25 to 16.68 |
| Seasonal naive | 0.31 to 1.81 | 0.17 to 1.78 | 0.41 to 1.39 | -2.32 to 1.73 |
| SARIMA-24 | 0.59 to 2.02 | 0.64 to 10.66 | 0.59 to 2.03 | -0.44 to 11.48 |
| ExtraTrees-24 | 0.06 to 0.98 | 0.98 to 4.98 | 0.07 to 0.93 | -0.78 to 5.35 |
| Pooled | 0.21 to 0.93 | 1.93 to 5.12 | 0.21 to 0.85 | 1.42 to 5.82 |

#### Excluding September 2025 and March 2026: 23 origins

| Method | Rule | LP | Protected | Hindsight | Optimisation value (C/E/D) | Protection value (help/tie/hurt) | Gap closed: optimisation / protection |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | 20.69 | 20.69 | 19.80 | 13.41 | 0.00 (0/23/0) | 0.90 (11/0/12) | 0.0% / 12.3% |
| Persistence | 27.93 | 27.93 | 20.95 | 13.41 | 0.00 (0/23/0) | 6.98 (12/0/11) | 0.0% / 48.1% |
| Seasonal naive | 18.52 | 17.71 | 17.08 | 13.41 | 0.80 (15/8/0) | 0.64 (9/0/14) | 15.7% / 12.5% |
| SARIMA-24 | 25.37 | 23.89 | 17.95 | 13.41 | 1.48 (17/6/0) | 5.94 (13/0/10) | 12.4% / 49.7% |
| ExtraTrees-24 | 20.59 | 20.29 | 17.35 | 13.41 | 0.31 (18/5/0) | 2.94 (10/0/13) | 4.3% / 40.9% |
| Pooled | 22.62 | 22.10 | 18.62 | 13.41 | 0.52 (50/65/0) | 3.48 (55/0/60) | 5.6% / 37.8% |

<details>
<summary>Rule versus LP diagnostics without the original two origins</summary>

| Method | Opening cost | Closing cost | Running cost | Close-then-reopen events | Shortfall bed-months |
| --- | --- | --- | --- | --- | --- |
| Mean-24 | 1.98 -> 1.98 | 0.00 -> 0.00 | 10.81 -> 10.81 | 0.00 -> 0.00 | 1580.3 -> 1580.3 |
| Persistence | 1.92 -> 1.92 | 0.00 -> 0.00 | 10.45 -> 10.45 | 0.00 -> 0.00 | 3113.1 -> 3113.1 |
| Seasonal naive | 3.01 -> 2.66 | 0.57 -> 0.38 | 10.43 -> 10.74 | 1.00 -> 0.43 | 901.3 -> 785.2 |
| SARIMA-24 | 2.84 -> 2.50 | 0.60 -> 0.42 | 9.33 -> 9.64 | 1.17 -> 0.39 | 2518.2 -> 2265.0 |
| ExtraTrees-24 | 2.45 -> 2.33 | 0.26 -> 0.20 | 10.59 -> 10.69 | 1.22 -> 0.39 | 1458.8 -> 1412.2 |
| Pooled | 2.44 -> 2.28 | 0.29 -> 0.20 | 10.32 -> 10.47 | 0.68 -> 0.24 | 1914.4 -> 1831.2 |

</details>

The [full metric table](outputs/optimisation_value/summary.csv) includes every mean, median, minimum, maximum and gap-closed share for both scopes. [Subset results](outputs/optimisation_value/subset_metrics.csv), [per-origin costs](outputs/optimisation_value/costs.csv), [diagnostics](outputs/optimisation_value/diagnostics_by_origin.csv) and [verification](outputs/optimisation_value/verification.md) provide the supporting detail.

### Pre-stated expectations

- **E1 held to solver precision:** Rule and LP agree for Mean-24 and Persistence at every origin; the largest capacity difference is below 0.001 beds.
- **E2 held in this sample:** LP was cheaper in 56 cases, equal within tolerance in 69, and materially dearer in 0.
- **E3 held:** mean optimisation value was positive for Seasonal naive, SARIMA-24 and ExtraTrees-24.

### Answer

Optimisation lowered mean cost by 0.90m for Seasonal naive, 1.49m for SARIMA-24 and 0.33m for ExtraTrees-24, with no material benefit for the flat Mean-24 and Persistence forecasts. The LP reduced opening and closing costs and realised shortfalls by keeping capacity through dips, despite higher running costs. Protection added a further 3.44m per method-origin case on average; pooled over all cases, optimisation closed 6.0% of the Rule-to-hindsight gap, protection closed 38.0%, and 56.0% remained. The cheapest observed combination was Seasonal naive + Protected, at 16.96m per plan, though protection hurt more cases than it helped. Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).
