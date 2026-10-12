# NHS frozen forecast-to-capacity planning

Read `nhs_frozen_three_month_uncertainty_optimization.ipynb`. It is the only
notebook in this repository; saved execution outputs are included. Cells 45-47
show the three simple total-cost comparisons.

| Origin | Frozen planning months |
| --- | --- |
| August 2025 | September-November 2025 |
| November 2025 | December 2025-February 2026 |
| February 2026 | March-May 2026 |

Three policies: latest-value persistence with deterministic optimization; locked
Random Forest available-bed and Extra Trees occupied-bed points with deterministic
optimization; the same ML points with 13 equally weighted, centered historical
paired error paths and expected-cost stochastic optimization. All three monthly
decisions stay frozen within each block. Each policy carries its own capacity
state into the next block. No CQR or CVaR is used in this experiment.

The series has 66 monthly national all-acute aggregate observations, not daily,
provider-level or patient-level records. G&A means General and Acute beds,
excluding maternity and mental health. Only occupied and available beds enter
planning; the threshold requirement is occupied / 0.90.

## Forecast accuracy further ahead

A separate saved analysis from `nhs_frozen_seven_month_three_origins.ipynb`
(not included here) compares 12-month, 24-month and all-history training.
At each origin, we forecast the next seven months without admitting new actual
observations or refitting. H1-H3 and H4-H7 are portions of **one frozen forecast
path per training window**, not separate runs.

![Early versus later forecast errors for available and occupied beds](figures/forecast_error_early_vs_late.png)

| Origin | Forecast months | Observed pattern |
| --- | --- | --- |
| February 2025 | March-September 2025 | Later errors increased in five of six target/window combinations; one stayed almost unchanged (+0.08%). |
| June 2025 | July 2025-January 2026 | Later errors increased in two combinations and decreased in four. |
| August 2025 | September 2025-March 2026 | Later errors increased in all six combinations. |

Later forecasts were generally less accurate in February and August, while June
was mixed. Errors fluctuated rather than increasing every month. These findings
concern individual bed forecasts; capacity-gap accuracy and planning costs are
evaluated separately.

Limitations: three overlapping historical trials; short-window fits are
exploratory; February/June use retrospectively selected models.

## Planning performance further ahead

Using the same saved seven-month analysis (cells 37-39), we evaluated frozen
capacity plans against actual bed outcomes. We compared monthly-average
shortages and cost disadvantage relative to the **full seven-month hindsight
benchmark** in H1-H3 versus H4-H7. These are portions of one plan, not separate
optimization runs. Worse later forecasts did not consistently produce worse
later planning.

![Early versus later monthly-average cost disadvantage and shortages by origin and policy](figures/planning_performance_early_vs_late.png)

| Origin | Later performance worsened | Improved | Mixed |
| --- | --- | --- | --- |
| February 2025 | 5 | 0 | 2 |
| June 2025 | 2 | 4 | 1 |
| August 2025 | 1 | 5 | 1 |

Counts cover **seven practical policies per origin**: persistence and point /
uncertainty planning for each of three training windows. Classification considers
shortages and disadvantage relative to hindsight, **not total expenditure alone**.
One worsening diagnostic with the other unchanged counts as worsened; opposing
changes are mixed.

- **February:** all ML plans developed later shortages; some scheduled capacity
  reductions went too far despite lower actual requirements.
- **June:** performance varied; several plans improved despite higher later
  requirements.
- **August:** all six ML plans had a smaller later cost disadvantage despite worse
  individual forecast accuracy. Persistence worsened; 12-month point planning
  had mixed results.

Uncertainty reduced later-period average monthly costs in **five of nine**
comparisons: all three windows in February and two in June, but none in August.
It cost more for all three windows in August's later period. This differs from
whole-plan results: early protection still made August's all-history and
24-month uncertainty plans cheaper overall.

These are overlapping historical trials with illustrative modeled costs, not
NHS cash savings. The result concerns **frozen-plan performance, not solver
failure**; an optimal plan for forecast inputs can perform poorly against actuals.

## Uncertainty and capacity optimization explained

The following two presentation cells describe the separate seven-month analysis
in `nhs_frozen_seven_month_three_origins.ipynb` (not included here), discussed
above. Its February, June, and August 2025 plans each start with zero additional
capacity and use 3, 7, and 9 historical error paths respectively. The included
three-month notebook instead uses 13 paths and carries capacity between blocks.

### How we represent uncertainty

We start with the ML forecasts of available and occupied beds.

- Look back at complete **seven-month forecast error paths** (actual minus predicted), using only outcomes known by that origin.
- Keep available/occupied errors paired and retain their month order.
- Subtract the average historical error separately for each bed series at each horizon, then add these centered paths to its ML forecast. The resulting possible futures average back to the original forecasts.
- Give every historical path equal weight. Verified paths per training window: **February 2025: 3; June 2025: 7; August 2025: 9** (weights 1/3, 1/7 and 1/9).

These are empirical scenarios, not guaranteed confidence intervals. February's three overlapping paths provide particularly limited evidence.

**The ML prediction stays the same; uncertainty lets the planner consider how that prediction could be wrong.**

#### Worked SAA example

**Hypothetical one-month example, starting with zero additional capacity.** SAA (sample average approximation) means choosing a plan using the average cost across scenarios.

The ML capacity-gap prediction is 10 beds. Centered historical errors of $-2$ and $+2$ give equally weighted gaps of 8 and 12; their average is still 10.
Opening and operating one extra bed costs **£1,050**: £500 active + £500 addition + £50 change. Each remaining shortage bed costs **£5,000**.

| Extra beds chosen | Opening + operating cost | Shortage penalty: gap 8 | Shortage penalty: gap 12 | Average total cost |
| --- | --- | --- | --- | --- |
| 8 | £8,400 | £0 | £20,000 | £18,400 |
| 10 | £10,500 | £0 | £10,000 | £15,500 |
| 12 | £12,600 | £0 | £0 | £12,600 |

$$\text{Average cost of choosing 10}=10{,}500+\frac{0+10{,}000}{2}=15{,}500.$$

**The point forecast chooses 10 beds. SAA chooses 12 here because £2,100 of additional capacity avoids £5,000 of average shortage penalties. The forecast average remains 10; the cost of being wrong changes the decision.**

**If the actual gap is only 8, the SAA plan costs £2,100 more than the point plan. This explains why uncertainty can help on average across scenarios but hurt in a particular outcome.**

Our real optimizer makes one shared seven-month schedule using complete historical error paths and the actual capacity constraints.

### How uncertainty enters the optimization

**We choose how much extra capacity to keep, add, or remove each month, balancing its cost against the penalty for being short.**

- $x_h$: active extra capacity.
- $a_h,r_h$: capacity added and removed.
- $s_{j,h},u_{j,h}$: shortage and unused capacity in scenario $j$.
- $p_j$: the scenario's assigned weight.
- $h$: planning month; $j$: possible future path.

$A_{j,h}$ and $O_{j,h}$ are available and occupied beds in that scenario. With costs in GBP:

$$\min \sum_h(500x_h+550a_h+300r_h)+5000\sum_jp_j\sum_hs_{j,h}.$$

$$A_{j,h}+x_h+s_{j,h}=O_{j,h}/0.90+u_{j,h},\qquad x_h=x_{h-1}+a_h-r_h.$$

**We choose one seven-month capacity schedule shared across all scenarios. Each scenario can produce a different shortage. The objective includes their weighted average shortage penalty. Extra protection is worthwhile only when the expected penalty reduction outweighs its capacity cost.**

All quantities are nonnegative; active extra capacity is capped at 15,000, additions/removals at 5,000 each per month, and each origin starts at $x_0=0$.
The 550 and 300 include the 50-per-bed change cost. Unused capacity has no separate penalty, but maintaining extra capacity still costs money.

**Future actual bed numbers are used afterward to score the frozen plan. All monetary costs and the 90% threshold are illustrative assumptions.**

## View or run

Viewing saved outputs requires no execution. For a fresh run, first obtain the
workbook as described in `DATA_PROVENANCE.md` and place it in the repository root.
Use Python 3.10 in an isolated environment:

```sh
python -m venv .venv
# Activate .venv using the command appropriate to your operating system.
python -m pip install -r requirements.txt
python -m ipykernel install --user --name nhs-capacity --display-name "NHS capacity"
```

Open the notebook in VS Code or another Jupyter client using that kernel, with
the repository root as working directory. Run all cells. Alternatively run
`python nhs_frozen_three_month_uncertainty_optimization.py` from the root to
regenerate files (this does not update notebook inline outputs).
PuLP requires a working CBC solver; the import check reports local availability.
Do not run broad CLI workflows in the imported legacy modules.

## Reproduction boundary

This is a frozen-input experiment, not a rerun of the original model-selection
study. Evaluation forecasts are LOADED from the included 54-row forecast table.
The notebook refits 26 development estimators, constructs 13 residual paths,
then solves nine optimization problems and evaluates actual outcomes. It checks
against the included deterministic reference tables and 42 archived development
predictions. The two-row model-selection table records earlier locked choices;
it does not reproduce their selection. No prior trial notebooks are included.

`forecasting_evaluation.py` defines data cleaning, lag/calendar features, tree
estimators and recursive feature rows. `frozen_uncertainty_experiment.py` defines
development recursion, scenarios and policy wrappers. `optimization_evaluation.py`
defines the solvers and realized evaluator. `optimization_revision.py`,
`uncertainty_evaluation.py` and `deep_forecasting_evaluation.py` are retained because
the unchanged import chain needs them, not because deep learning or conformal
methods run here. Optional Darts/Torch imports are guarded; do not install them
for this notebook. Uncalled legacy entry points require artifacts not included.

The included regression JSON is HISTORICAL evidence read by cell 42. Packaging
does not rerun those tests or supply the old trial test fixtures. Any test-pass
text in saved output is historical, not proof of a new execution.
See `REPRODUCTION_REVIEW.md` for exactly what was checked and what remains blocked.

## Interpretation

These costs and cost reductions are illustrative modeled quantities, NOT NHS
cash savings. Cost coefficients, physical limits and the 90% threshold are
unvalidated. Uncertainty improves on ML point planning in August and February
but loses in November; it does not win every comparison. Thirteen overlapping
historical paths and nine already-inspected evaluation months support only an
exploratory comparison. Publication-time availability, data revisions and the
boundary between extra and published baseline capacity remain unresolved.

The workbook is not committed. The owner has authorized this baseline public
release. That authorization does not resolve the documented data-reuse and code
licensing questions; see `DATA_PROVENANCE.md`. Fresh end-to-end reproduction
has not been verified.
