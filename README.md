# NHS bed forecasting and capacity planning

We study how forecast errors affect a six-month capacity plan, and when planning against uncertainty helps. The data are monthly national aggregates of NHS England General and Acute (G&A) beds, excluding maternity and mental health.

**Main finding:** SARIMA point planning was cheaper than centered empirical SAA in two of three 2025 trials. In a later, partially blind retrospective 2026 evaluation, the same SAA comparison was substantially cheaper. Forecast-error direction and the chosen shortfall penalty affected the ranking; structural assumptions also mattered in some cases. This does not establish a generally superior policy.

All costs below are **illustrative model cost units**, not validated sterling expenditure or NHS cash savings. Earlier published tables label these same illustrative numbers as GBP. A shortfall means missing an occupancy-adjusted capacity target, not a measured number of patients denied care.

## The decision

SARIMA forecasts occupied beds $O_t$ and available beds $A_t$ from one observed-month cutoff. The planning gap, in capacity-equivalent beds, is

$$
d_t = O_t / 0.90 - A_t.
$$

The planner chooses active extra capacity $x_t$, additions $a_t$ and removals $r_t$ for all six months together. Each trial starts independently at $x_0=0$. Forecasts and decisions remain frozen; later actuals score the plan.

- **Point planning:** optimize against the single SARIMA forecast path.
- **Empirical SAA** (sample average approximation): optimize one shared schedule against equally weighted scenarios built from complete earlier six-month SARIMA error paths. Available/occupied errors remain paired, and all calibration outcomes must be known by the origin. Errors are centered separately by target and horizon, so scenario means match the point forecasts. The 2025 trials use 4, 8 and 10 paths; the February/March 2026 trials use 16 and 17.

The baseline objective is

$$
\min \sum_t (500x_t + 550a_t + 300r_t)
+ 5000\,\mathbb{E}\!\left[\sum_t (d_t-x_t)_+\right],
$$

where $(v)_+=\max(v,0)$. Constraints impose $x_t=x_{t-1}+a_t-r_t$, nonnegative quantities, at most 15,000 active extra beds, and at most 5,000 additions or removals per month. There is no terminal charge in this baseline, and CVaR is disabled. For point planning, the expectation reduces to one forecast path.

The shortfall penalty is ten times the monthly holding cost. Opening/removal costs and links between months mean these are not six independent monthly quantile decisions. SAA minimizes average scenario cost; it does not guarantee a cheaper realized outcome.

## What happened

Difference = **SAA realized cost minus point realized cost**, in millions of model cost units. Positive means SAA cost more. Rows are separate, overlapping hypothetical trials, not a continuous operating trajectory.

| Observed-month cutoff | Frozen planning months | Cost difference (millions) | Evaluation status |
| --- | --- | ---: | --- |
| February 2025 | March-August 2025 | +1.176 | Exploratory development |
| June 2025 | July-December 2025 | -0.226 | Exploratory development |
| August 2025 | September 2025-February 2026 | +1.631 | Exploratory development |
| February 2026 | March-August 2026 | -10.854 | Locked, partially blind retrospective evaluation |
| March 2026 | April-September 2026 | -11.898 | Locked, partially blind retrospective evaluation |

The 2025 values reconcile with the [published baseline results](results/six_month_sarima_saa_comparison.csv). The 2026 values and robustness findings below were checked against the latest local records; those detailed records are **not yet published in this repository**.

The 2026 rows show a **prespecified secondary comparison** of unchanged point versus centered SAA. The registered primary comparison instead used mean-error-corrected point forecasts versus uncentered historical-error scenarios; its differences were -11.644 and -14.593 million model cost units. The comparisons should not be conflated.

## Why the ranking changed

Let $\widehat d_t$ be the forecast gap. Define signed forecast error $e_t=d_t-\widehat d_t$ and planned buffer $b_t=x_t-\widehat d_t$. Before numerical rounding, realized planning shortfall is exactly

$$
s_t=(e_t-b_t)_+.
$$

In the 2025 trials, SARIMA generally overforecast the gap. Realized gaps fell below the scenario median in **14 of 18 origin-months**. Planning against a high forecast already provided protection; SAA often added capacity whose operating cost exceeded the penalties it avoided. This protection was accidental, but it was not free: point planning also paid to operate its capacity.

In the February and March 2026 evaluations, realized gaps averaged **544 and 559 beds above the forecasts**, respectively. The point schedules provided too little protection, and SAA reduced modeled cost under the same baseline assumptions.

Historical mean-error correction did not anticipate this change. Pre-origin corrections lowered the forecast gaps by an average of 108 and 123 beds, respectively, when the subsequent actual gaps were higher. The insurance effect of overforecasting is understandable; using past bias to predict its future direction was unreliable in these cases.

## What the checks established

The latest local robustness records supersede the initial stopped audit: the specification was reconciled with the existing coupled optimizer, rather than treating its transition costs as a solver defect. All 24 existing plans reproduced with identical capacity decisions and floating-point-level cost differences. Later synthetic and nesting checks passed under documented tolerances. Configurations were hashed before the corresponding sensitivity rounds; one recorded amendment addressed numerical tolerance in the budget-constrained hindsight check. The 2025 analysis remains exploratory because outcomes had already been examined.

- **Costs and protection preferences:** tested shortfall-cost ratios and occupancy thresholds changed rankings; no universal preference survived the grid.
- **Operational structure:** several tested changes preserved rankings, but structure was not irrelevant. Removing lead time reversed June's block-planning comparison, and a terminal charge reduced its baseline advantage to a practical tie. These tests are not a formal ranking of which assumptions matter most.
- **Equal budgets:** SAA produced less or equal realized planning shortfall than the tested point-plus-uniform-buffer policy at all 21 tested 2025 budget/origin cells, within numerical tolerance. This supports that comparison, not all possible buffer rules.
- **Alternative scenarios:** Gaussian and innovation-bootstrap generators produced much wider distributions and cost more than point planning and empirical SAA at all three 2025 origins. Their gap CRPS was worse on the common June/August validation cases; high coverage alone did not establish better calibration.
- **Nonlinear penalties:** the additional 95%-occupancy shortfall tier was inactive in the tested 2025 actuals and scenarios, so those cases could not assess its intended effect.

Reordering paired scenarios independently within each calendar month leaves this additive expected-cost objective unchanged when each month's gap distribution and weights stay fixed. Moving demand between calendar months can affect decisions.

## What the later evaluation establishes

The recorded execution order and verified hashes show that configurations and plans were frozen before scoring June-September 2026 outcomes. March-May 2026 had already been inspected. This is a **partially blind retrospective exercise conducted in October 2026**, not evidence that the plans were issued in real time at the stated origins. Hashes support file integrity; they do not independently establish what an analyst had previously seen.

The two new windows share five target months (April-August). They therefore do not provide two independent six-month confirmations, and contain only **four distinct previously unread outcome months** (June-September). The later workbook's history matches the earlier snapshot through both origins; subsequent revisions and historical release-time availability remain limitations.

The ranking reversal shows that the observed 2025 point advantage did not persist in these later cases. It does not establish SAA's long-run expected benefit, an optimal protection level, or a guaranteed downside cap. The scale of the differences depends on the illustrative penalty ratio.

## Limits and next step

National monthly averages assume substantial pooling and hide local peaks. Occupied beds reflect accommodated demand and may be capacity-constrained. Holding published available and occupied bed counts fixed while adding modeled capacity is an unvalidated counterfactual. Publication timing, historical revisions, and the boundary between published capacity and modeled additions also need operational validation.

The next step is to define an actionable decision with one trust or bed pool and evaluate it using suitable local data. Trust-level analysis reduces national pooling but still needs staffing, timing and access measures; monthly averages alone may miss short periods of congestion.

These repeatedly inspected, overlapping trials support a methodological case study: forecast-error direction and protection preferences can reverse planning rankings, so forecast accuracy alone cannot select a capacity policy. A ranking change does not identify the correct formulation, and an unchanged ranking does not prove absence of bias. The results do not demonstrate NHS cash savings or improved patient access.

## Reproduction status

The latest formulation notebook, helper, robustness tables, locked plans, 2026 scoring records and newer workbook have **not been published here**. This checkout contains earlier research artifacts and the 2025 baseline tables; it does not reproduce the entire study summarized above. Earlier artifacts are retained for traceability, rather than presented as the current experiment. Fresh end-to-end reproduction from a clean public checkout has not been verified.

See [data provenance](DATA_PROVENANCE.md) and the [existing reproduction review](REPRODUCTION_REVIEW.md) for the included materials. Both cover the earlier bundled experiment and May 2026 workbook, not the latest local study or September 2026 release. The workbook is not committed; data-reuse and code-licensing questions documented there remain unresolved.
