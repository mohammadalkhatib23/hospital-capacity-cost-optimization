# Methodology review (Phase 1)

Written before any forecast, plan or score of this checkpoint was computed. It reviews the design as specified; it
does not change it.

Ratings: **OK**, **acceptable with disclosure**, **must change**.

**Outcome: nothing is rated "must change".** Nine points are "acceptable with disclosure" (listed at the end). Three
wording gaps in the decision rules are closed by recording the literal reading in the config before the run; no rule
is altered.

## 1. Is the comparison fair?

| Point | Rating | Why |
| --- | --- | --- |
| Same information | OK | Every method sees the same 24 months of the same two series and nothing else. |
| Same task | OK | Same six target months, same two series, same gap formula. |
| Same decision problem | OK | Every forecast goes through the same Rule and the same LP, with the same costs, limits and zero starting capacity, and is scored with one formula against the same actual gaps. Hindsight uses the same LP. |
| Different use of the window | OK | Mean-24 uses 24 values, Persistence 1, Seasonal naive 6, SARIMA-24 about 12 seasonal differences, ExtraTrees-24 12 training rows. The information offered is equal; how much each method can use is a property of the method. |
| Asymmetric costs | Acceptable with disclosure | A missing bed costs 5,000, a spare bed 500. Neither decision method adds a safety margin, so a forecast that is biased high is protected and a forecast that is biased low is punished. The cost ranking therefore reflects the direction of the error as well as its size. The README must show the mean signed gap error next to the MAE and must not read "cheapest" as "most accurate". |
| Start and end of each window | Acceptable with disclosure | Each window starts from zero extra beds and has no closing charge at the end. Every plan, including hindsight, pays to open from zero, and the second window does not inherit the first window's beds. This affects cost levels more than ranks. |
| Data vintage and timing | Acceptable with disclosure | Training values come from the October 2026 publication, not from the files available at each origin, and a month's figures are published several weeks after the month ends. The test is a retrospective one on final data, the same for every method. |

## 2. Known departures

| Departure | Rating | Comment |
| --- | --- | --- |
| SARIMA-24 fits all 24 months as labels | Acceptable with disclosure | The project's original procedure dropped the first 12 months and needed at least 30 labels, so it cannot run on a 24-month window at all. With one seasonal difference, 24 months leave about 12 observations to estimate one AR coefficient and a variance. The forecast is close to "Seasonal naive plus a fading share of the latest year-on-year change". It is a legitimate small-sample model, but it is not the project's full-history SARIMA and should not be described as such. |
| ExtraTrees-24 settings and sample size | Acceptable with disclosure | The settings were chosen earlier for occupied beds and are reused for available beds without a new selection. Each fit has about 12 training rows for 16 features, with a minimum leaf of 2. A tree model cannot forecast outside the range of its training labels, so with 12 rows the forecast is confined to the last year's range. The result describes ExtraTrees starved of data, not ExtraTrees in general. |
| Fixed specifications, earlier choices | Acceptable with disclosure | Nothing is tuned in this run. But the SARIMA order, the ExtraTrees settings, the 24-month window and both origins were chosen in earlier project work that had already seen the outcome months. Code cannot remove that. |

How the README should describe them: name the models "SARIMA-24" and "ExtraTrees-24", state the label and row counts,
say that both are small-sample variants with fixed settings, and say that the comparison is fair in information but
not a test of either model family at its best.

## 3. Is the gap the right quantity to judge forecasts by?

**OK**, with caveats that are **acceptable with disclosure**.

- The planner uses only the gap, so gap accuracy is the accuracy that matters. Occupied and available MAE are context.
- The gap is the difference of two large numbers (about 104,000 and 100,000). A 1% error in occupied beds moves the gap
  by about 1,000 beds. Errors in the two series cancel when they go the same way, so a method can be best on both
  series and still worse on the gap.
- Gap MAE treats over- and under-forecasting alike; the cost does not (see point 1). The mean signed gap error must be
  reported with it.
- The "actual gap" is itself a model quantity. Occupied beds cannot exceed available beds, so true demand is partly
  hidden, and the actual series do not react to the plan being scored.

## 4. Are the decision rules sensible?

| Rule | Rating | Comment |
| --- | --- | --- |
| Tie bands (1% of hindsight cost; 5 beds) | OK | Arbitrary but fixed in advance and applied the same way everywhere. They are a guard against over-reading small differences, not a statistical test. |
| Joint best per origin | OK | Clear and computable. |
| Overall ranking by combined excess over hindsight | OK | Identical to ranking by combined cost. The origin with the larger cost differences weighs more. |
| Clear winner | Acceptable with disclosure | The rule is sensible. Two wording gaps are closed by the literal reading (below). |
| Best forecast | OK | "Clear" is read as: lowest gap MAE at both origins, each time by more than 5 beds over every other method. |
| Improvement measures | OK, with notes | See below. |
| "Pre-registered" | Acceptable with disclosure | The rules are fixed and hashed before any forecast of this run. They are not blind: the Origin 1 results for these methods are already published in this repository, and the Origin 2 outcome months were used in earlier tests of other models. The hash shows that the rules were not changed after this run; it cannot show that they were written without knowledge of the data. The README must not call this a blind test. |

Literal readings recorded in the config (no change to the rules):

1. **More than one plan may meet the "clear winner" definition.** Flat forecasts give identical Rule and LP plans, and
   two close forecasts can both be joint best at both origins. Every plan that meets the definition is reported; if
   there is more than one, they are reported as joint winners and the README says that the rule does not separate
   them. No plan is singled out by a tie-break. (The two clauses cannot name different plans: if the overall first
   plan beats the runner-up by more than the band at each origin, any other plan that is joint best at both origins
   would rank above the runner-up, which is a contradiction.)
2. **"Runner-up"** is the second plan in the overall ranking. Exact ties in combined excess are ordered by the fixed
   plan order; an exact tie cannot be a win by more than the band, so this has no effect on the outcome.
3. **Combined versions of ratio measures** are ratios of sums: combined share of avoidable cost = (sum of baseline
   costs - sum of plan costs) / (sum of baseline costs - sum of hindsight costs); combined skill = 1 - (mean MAE of the
   method / mean MAE of Seasonal naive); combined excess as a percentage = sum of excess / sum of hindsight costs.

Notes on the improvement measures:

- **Value of optimisation (Rule cost - LP cost) is measured on realised cost.** The LP is optimal for the forecast, not
  for what happened, so a non-negative value is guaranteed only for planned cost (cost against the forecast gap). The
  Phase 4 check "LP cost <= Rule cost" is therefore run twice: on planned cost, where it must hold, and on realised
  cost, as written in the brief. With these costs the LP never plans fewer beds than the Rule unless a ramp binds, so
  the realised check is expected to hold too. If it fails, the run stops as instructed and the failure is reported as
  a finding, not patched.
- **Share of avoidable cost removed** is unstable when the baseline is already close to hindsight (small denominator)
  and can be a large negative number for a poor plan. It should always be read next to the absolute saving.
- **Savings** compare like with like (Rule against Rule, LP against LP), as specified. For LP plans this equals
  "saving vs Persistence LP / Seasonal naive LP"; the README table header must say "same decision method".

Proposed changes: **none required.** Two options for a later version, not applied here: require a single plan for
"clear winner", and add a decision method that plans above the point forecast (a quantile or scenario plan), since the
cost asymmetry rewards it.

## 5. Leakage risks and how the code prevents them

| Risk | Prevention |
| --- | --- |
| Outcome months reach a forecast | The loader takes a cut-off and discards later rows before any value is converted. Each origin is loaded separately with its own cut-off, and the forecasting code only receives that frame. The run asserts that the frame ends at the cut-off. |
| Scaling or features use later data | The StandardScaler is fitted on the 24 window values only. ExtraTrees features are built inside the window; rows that would need earlier data are dropped, not filled; recursive steps use only window values and the model's own earlier predictions. The dates used by every fit and feature are logged and asserted. |
| Seasonal naive reads a target month | The source months are asserted to lie inside the window. |
| Plans adjusted after seeing costs | Forecasts and plans for both origins are written and hashed before any actual value after a cut-off is loaded. Scoring verifies the hash and reads the plans back from the frozen file. |
| Rules adjusted after seeing results | The config with the rules is written and hashed before any forecast; every stage verifies the hash. |
| Earlier results steer this run | The pipeline imports no project module and reads no saved output. Saved outputs are read only by the Phase 4 cross-check. |
| Selection leakage | Not preventable by code (see points 2 and 4); disclosed. |
| Later origin sees the earlier origin's outcomes | Origin 2 trains on months that are Origin 1's targets. This is legitimate (they precede Origin 2's cut-off), but it means the two tests share data and are not independent. |

## 6. What two origins can and cannot show

**Acceptable with disclosure.**

Can show:

- Which plans were cheapest in each of two non-overlapping six-month windows, one winter (October - March) and one
  summer (April - September).
- Whether the Origin 1 ranking repeats once.
- Whether the LP's advantage over the Rule appears in both windows, and how large it is next to forecast differences.

Cannot show:

- A general ranking of methods. Two windows give no estimate of how much rankings vary from origin to origin.
- Statistical significance. The tie bands are conventions.
- Why a ranking differs between windows. Season, year and level changes are confounded, and the training windows
  share 18 of 24 months.
- How SARIMA or ExtraTrees perform with more data, or how any method performs at trust or regional level.
- Anything about real money. Costs are illustrative model units (validated: False).

## Points rated "acceptable with disclosure"

1. Asymmetric costs reward forecasts that are biased high.
2. Zero starting capacity and no terminal charge in each window.
3. Final-vintage data and publication timing.
4. SARIMA-24 fitted on all 24 months as labels.
5. ExtraTrees-24 settings reused for available beds, about 12 training rows.
6. Specifications, window length and origins chosen in earlier work that had seen the outcome months; the rules are
   fixed in advance but not blind.
7. Gap MAE is symmetric and the actual gap is a model quantity.
8. "Clear winner" may be met by more than one plan (literal reading recorded).
9. Two origins show one repetition, not a general ranking.
