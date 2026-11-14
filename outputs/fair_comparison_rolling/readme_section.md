## Fair comparison across many origins

Two six-month windows were not enough to establish a stable ranking. We repeat the same fair comparison at every eligible monthly origin, using the same forecasting methods and protection rule.

### Method

Each method uses 24 months of training and predicts the next 6 months. An origin is eligible only when its training and target windows are complete and it has at least 9 complete historical error paths. The models, raw paired error paths, SAA capacity model and realized-cost scoring are unchanged. Both SARIMA targets must converge; otherwise the origin is excluded for all methods before scoring. The last month with both bed observations is October 2026; later blank template rows are ignored. Every plan starts with no extra capacity and stays fixed for its whole horizon.

Of 42 training/target candidates, 17 were excluded: 15 from November 2022 through January 2024 had too few error paths, and 2 failed the planning-origin convergence check below. This leaves 25 origins.

| Excluded planning origin | Nonconvergent target | Treatment |
| --- | --- | --- |
| 2025-10 | occupied | Excluded for all five methods |
| 2026-01 | available | Excluded for all five methods |

Retained origins and their error-bank sizes (paths per method):

| Year | Origin month (retained paths) |
| --- | --- |
| 2024 | Feb (9), Mar (10), Apr (11), May (12), Jun (13), Jul (14), Aug (15), Sep (16), Oct (17), Nov (18), Dec (19) |
| 2025 | Jan (20), Feb (21), Mar (22), Apr (23), May (24), Jun (25), Jul (26), Aug (27), Sep (28), Nov (30), Dec (31) |
| 2026 | Feb (33), Mar (34), Apr (34) |

Historical backtests at January 2023 (available beds) and October 2025 (occupied beds) are also dropped for every method, under the original rule. The [complete origin inventory](outputs/fair_comparison_rolling/origin_inventory.csv) includes all exclusions.

The primary comparison uses Protected plans at a shortfall penalty of 5,000. Point plans at 5,000 provide the reference for the value of protection. Protected plans and their own hindsight benchmarks are also computed at 2,500 and 10,000; 10,000 is indicative because the historical tail samples are small. To save computation, this extension omits the original bias-corrected, centered-error, service-level and 1,000-penalty variants, and Point-plan penalty sensitivities.

The rules were registered before new scoring:

- At each origin, Protected plans within 1% of that origin’s hindsight cost of the cheapest plan are joint best.
- Rank methods by mean excess over hindsight, giving each origin equal weight.
- For each reporting scope, sort retained origins and take every sixth origin from each of six starting offsets. Members within a subset have non-overlapping target windows; the subsets overlap each other.
- A clear winner must have the lowest mean excess, the lowest subset mean in at least four of six subsets, and more wins than losses against the overall runner-up. Pairwise ties use the same 1% band.
- Call the result robust only if the same clear winner or top method holds at all three penalties.
- Apply the rules again after excluding the two original origins, and separately before and from January 2025.

Config SHA-256: `8715467371b89f2f10a0124613ce7387bdc9296d9f05026c0a91b6e63ca85e48`.
Frozen plans SHA-256: `f797670d1363ffd9fe3256854b4b6cce98a2a52fd0949c44ff64dd139dae54ff`.

The earlier `1d93530a...` registration was superseded before scoring to add the authorized planning-origin convergence rule. The approved blank-row correction and registration history are recorded in [verification.md](outputs/fair_comparison_rolling/verification.md). The rules were fixed before this run was scored, but the wider research is not blind: earlier results and some outcome months had already been inspected.

### Results

| Method | Mean excess over hindsight (m) | Joint-best origins | Win/tie/loss vs Seasonal naive | Mean pinball loss at 0.9 (beds) | 0.9 coverage | Mean value of protection (m) |
| --- | --- | --- | --- | --- | --- | --- |
| Seasonal naive | 3.50 | 14 | 0/25/0 | 194.3 | 95.3% (143/150) | 1.01 |
| ExtraTrees-24 | 3.87 | 6 | 8/3/14 | 167.7 | 96.7% (145/150) | 2.84 |
| SARIMA-24 | 4.45 | 7 | 8/2/15 | 160.6 | 94.7% (142/150) | 5.49 |
| Mean-24 | 6.21 | 0 | 0/0/25 | 309.2 | 100.0% (150/150) | 1.05 |
| Persistence | 7.30 | 3 | 4/1/20 | 264.5 | 93.3% (140/150) | 6.82 |

Costs are millions of illustrative model units per six-month plan. Lower excess and pinball loss are better. Positive value of protection means the Point plan cost more than its matching Protected plan. Joint-best counts can sum to more than the number of origins. Seasonal naive compared with itself is always a tie. Coverage counts are origin–horizon cases, not distinct independent months.

![Excess over hindsight by origin](figures/fair_comparison_rolling/F1_excess_by_origin.png)

![Mean excess and range across six offset subsets](figures/fair_comparison_rolling/F2_mean_and_subset_range.png)

Error bars show the range of the six subset means. They are descriptive ranges, not confidence intervals.

![Coverage of the 0.9 quantile by horizon](figures/fair_comparison_rolling/F3_coverage_by_horizon.png)

Coverage above 90% indicates that the upper forecast met or exceeded the actual gap more often than targeted. It does not prove that every capacity margin was unnecessary, or that the SAA plan equals the monthly quantile.

### Results outside the original two origins and by period

#### Excluding September 2025 and March 2026 — 23 origins

| Method | Mean excess over hindsight (m) | Joint-best origins | Win/tie/loss vs Seasonal naive | Mean pinball loss at 0.9 (beds) | 0.9 coverage | Mean value of protection (m) |
| --- | --- | --- | --- | --- | --- | --- |
| Seasonal naive | 3.66 | 13 | 0/23/0 | 197.4 | 95.7% (132/138) | 0.64 |
| ExtraTrees-24 | 3.94 | 5 | 7/3/13 | 169.6 | 96.4% (133/138) | 2.94 |
| SARIMA-24 | 4.54 | 7 | 8/2/13 | 161.5 | 94.2% (130/138) | 5.94 |
| Mean-24 | 6.38 | 0 | 0/0/23 | 315.1 | 100.0% (138/138) | 0.90 |
| Persistence | 7.53 | 3 | 4/0/19 | 267.9 | 92.8% (128/138) | 6.98 |

Top method: **Seasonal naive**; runner-up: **ExtraTrees-24**. Lowest subset mean in 2 of six subsets; wins/ties/losses against the runner-up: 13/3/7. Registered outcome: **no clear winner**.

#### Origins through December 2024 — 11 origins

| Method | Mean excess over hindsight (m) | Joint-best origins | Win/tie/loss vs Seasonal naive | Mean pinball loss at 0.9 (beds) | 0.9 coverage | Mean value of protection (m) |
| --- | --- | --- | --- | --- | --- | --- |
| Seasonal naive | 4.28 | 3 | 0/11/0 | 224.5 | 98.5% (65/66) | -0.03 |
| ExtraTrees-24 | 4.34 | 2 | 5/1/5 | 185.6 | 93.9% (62/66) | 4.14 |
| SARIMA-24 | 5.36 | 4 | 6/1/4 | 169.3 | 87.9% (58/66) | 11.24 |
| Mean-24 | 6.98 | 0 | 0/0/11 | 342.5 | 100.0% (66/66) | 1.76 |
| Persistence | 7.51 | 3 | 4/0/7 | 278.7 | 92.4% (61/66) | 7.43 |

Top method: **Seasonal naive**; runner-up: **ExtraTrees-24**. Lowest subset mean in 3 of six subsets; wins/ties/losses against the runner-up: 5/1/5. Registered outcome: **no clear winner**.

#### Origins from January 2025 — 14 origins

| Method | Mean excess over hindsight (m) | Joint-best origins | Win/tie/loss vs Seasonal naive | Mean pinball loss at 0.9 (beds) | 0.9 coverage | Mean value of protection (m) |
| --- | --- | --- | --- | --- | --- | --- |
| Seasonal naive | 2.89 | 11 | 0/14/0 | 170.6 | 92.9% (78/84) | 1.83 |
| ExtraTrees-24 | 3.50 | 4 | 3/2/9 | 153.6 | 98.8% (83/84) | 1.82 |
| SARIMA-24 | 3.73 | 3 | 2/1/11 | 153.7 | 100.0% (84/84) | 0.97 |
| Mean-24 | 5.61 | 0 | 0/0/14 | 283.1 | 100.0% (84/84) | 0.48 |
| Persistence | 7.13 | 0 | 0/1/13 | 253.3 | 94.0% (79/84) | 6.34 |

Top method: **Seasonal naive**; runner-up: **ExtraTrees-24**. Lowest subset mean in 5 of six subsets; wins/ties/losses against the runner-up: 9/2/3. Registered outcome: **Seasonal naive**.

<details>
<summary>Coverage by horizon for every reporting scope</summary>

**All retained origins**

| Method | H1 | H2 | H3 | H4 | H5 | H6 |
| --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Persistence | 84.0% | 92.0% | 100.0% | 100.0% | 92.0% | 92.0% |
| Seasonal naive | 100.0% | 100.0% | 100.0% | 100.0% | 84.0% | 88.0% |
| SARIMA-24 | 100.0% | 92.0% | 96.0% | 96.0% | 88.0% | 96.0% |
| ExtraTrees-24 | 100.0% | 96.0% | 92.0% | 96.0% | 100.0% | 96.0% |

**Excluding September 2025 and March 2026**

| Method | H1 | H2 | H3 | H4 | H5 | H6 |
| --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Persistence | 82.6% | 91.3% | 100.0% | 100.0% | 91.3% | 91.3% |
| Seasonal naive | 100.0% | 100.0% | 100.0% | 100.0% | 87.0% | 87.0% |
| SARIMA-24 | 100.0% | 91.3% | 95.7% | 95.7% | 87.0% | 95.7% |
| ExtraTrees-24 | 100.0% | 95.7% | 91.3% | 95.7% | 100.0% | 95.7% |

**Origins through December 2024**

| Method | H1 | H2 | H3 | H4 | H5 | H6 |
| --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Persistence | 81.8% | 90.9% | 100.0% | 100.0% | 90.9% | 90.9% |
| Seasonal naive | 100.0% | 100.0% | 100.0% | 100.0% | 90.9% | 100.0% |
| SARIMA-24 | 100.0% | 81.8% | 90.9% | 90.9% | 72.7% | 90.9% |
| ExtraTrees-24 | 100.0% | 90.9% | 81.8% | 90.9% | 100.0% | 100.0% |

**Origins from January 2025**

| Method | H1 | H2 | H3 | H4 | H5 | H6 |
| --- | --- | --- | --- | --- | --- | --- |
| Mean-24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Persistence | 85.7% | 92.9% | 100.0% | 100.0% | 92.9% | 92.9% |
| Seasonal naive | 100.0% | 100.0% | 100.0% | 100.0% | 78.6% | 78.6% |
| SARIMA-24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| ExtraTrees-24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 92.9% |

</details>

Full [subset means](outputs/fair_comparison_rolling/subset_means.csv), [per-origin ranks](outputs/fair_comparison_rolling/per_origin_ranks.csv), [pairwise wins and cost differences](outputs/fair_comparison_rolling/pairwise.csv), [coverage by horizon](outputs/fair_comparison_rolling/coverage_by_horizon.csv), and [all summaries](outputs/fair_comparison_rolling/summary.csv) cover every reporting scope and penalty.

### Robustness

| Scope | Shortfall penalty | Top method | Clear winner |
| --- | --- | --- | --- |
| All retained origins | 2,500 | Seasonal naive | Seasonal naive |
| All retained origins | 5,000 | Seasonal naive | Seasonal naive |
| All retained origins | 10,000 (indicative) | ExtraTrees-24 | No clear winner |
| Excluding September 2025 and March 2026 | 2,500 | Seasonal naive | Seasonal naive |
| Excluding September 2025 and March 2026 | 5,000 | Seasonal naive | No clear winner |
| Excluding September 2025 and March 2026 | 10,000 (indicative) | ExtraTrees-24 | No clear winner |
| Origins through December 2024 | 2,500 | Seasonal naive | Seasonal naive |
| Origins through December 2024 | 5,000 | Seasonal naive | No clear winner |
| Origins through December 2024 | 10,000 (indicative) | ExtraTrees-24 | No clear winner |
| Origins from January 2025 | 2,500 | Seasonal naive | Seasonal naive |
| Origins from January 2025 | 5,000 | Seasonal naive | Seasonal naive |
| Origins from January 2025 | 10,000 (indicative) | Seasonal naive | No clear winner |

Across all origins, the top method changes at the indicative high penalty, so the primary result is **not robust across all penalties**. For origins from January 2025, Seasonal naive remains top at all penalties, but the clear-winner condition is not met at 10,000.

### Answer

Seasonal naive is the clear winner under the registered primary rule: mean excess 3.50m versus 3.87m for ExtraTrees-24, lowest mean in 4 of six subsets, and 14 wins, 3 ties and 8 losses against that runner-up. The original Seasonal-naive-first ranking holds after removing the two original origins, but the clear-winner label does not; it holds in the later-period split and not in the earlier split. The ranking is not robust across penalties, because ExtraTrees-24 is top at the indicative 10,000 penalty, with no clear winner. The upper forecasts generally over-cover overall, but Seasonal naive under-covers at H5 and H6, Persistence at H1, and SARIMA-24 at H5, so calibration is uneven by horizon. Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).
