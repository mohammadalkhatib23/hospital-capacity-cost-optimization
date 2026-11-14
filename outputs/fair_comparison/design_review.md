# Design review and final proposed design (Phase 2)

Written on 14 November 2026, before any backtest, forecast, scenario or score of this experiment. Source numbers
refer to `research_notes.md`. Nothing here has been run. The experiment starts only after approval.

## Verdict

The proposed design is sound and matches how the literature handles this problem: give every method the same
protective policy, then compare outcomes (source 4), and score the quantile the plan needs, not the median (sources
7, 8). I recommend **keeping all four parts, with six changes**:

1. Use each backtest's six errors as one path, with occupied and available paired (already implied; now explicit).
2. Use raw errors, not centred errors, so that protection also removes each method's own bias. Centred errors become
   a sensitivity check.
3. Add one reference plan, the bias-corrected point plan, to separate "removing bias" from "adding a margin".
4. Score a defined 0.9 quantile forecast with pinball loss, and add coverage and CRPS. Qualify the claim that pinball
   loss "equals" the cost.
5. Treat the 0.95 level (penalty 10,000 and alpha 0.05) as indicative only: with 29 or 35 paths it is the worst past
   error.
6. Make the service-level version descriptive, with shortfall size reported next to shortfall frequency.

One thing the design cannot fix: **two windows still leave room for luck.** Equal protection removes the reward for
accidental bias on average. It does not remove chance in a single six-month window.

## Point-by-point critique of section 4

### (a) Equal protection: SAA on each method's own backtest errors - KEEP, with changes

**Is it a fair "equal protection" rule?** Yes, in the sense that matters. Every method gets the same rule, the same
number of backtests, the same information and the same costs. The amount of protection differs, and it should: a
method with larger past errors needs a larger margin and pays for it in spare beds. This is the standard construction
(sources 10, 15, 16, 17, 19).

Why a method can no longer win by leaning high: the per-month cost is a pinball loss (see (b)), and pinball loss is a
consistent score for the quantile (source 7). On average, a method minimises it only by reporting its true 0.9
quantile. Inflating the forecast buys shortfall protection but costs more in spare beds than it saves.

What "fair" does not mean:

- It is a test of the forecast **plus** its error history, not of the point forecast alone. That is the right object:
  the plan needs a distribution.
- A method whose past errors were large gets a wide margin. If the test window happens to have a large shortfall, that
  method is rewarded. Over many windows this evens out; over two it may not.
- The rule is equal; the evidence behind it is thin (see the count below).

**Raw or centred errors?** The project's earlier SAA centred the errors per series and horizon, so scenarios kept the
point forecast's bias. I recommend raw errors as the primary rule. Reasons: (i) it is what the empirical and conformal
methods do (sources 15, 16); (ii) centring would leave a biased method biased, which is the very effect this
experiment is meant to remove; (iii) the planner would know the method's past bias at the origin. Centred errors are
kept as a sensitivity check so the result can be compared with the earlier SAA work.

**How many backtest paths?** A backtest at month b needs a 24-month window ending at b and six target months at or
before the origin. The data start in December 2020, so the first usable b is November 2022.

| Origin | Backtest origins | Paths per method | Non-overlapping six-month blocks | Rank of the 0.9 quantile | Rank of the 0.95 quantile |
| --- | --- | ---: | ---: | ---: | ---: |
| September 2025 | November 2022 - March 2025 | 29 | about 5 | 27th of 29 | 29th of 29 (the largest) |
| March 2026 | November 2022 - September 2025 | 35 | about 6 | 33rd of 35 | 35th of 35 (the largest) |

Ranks use ceil((n + 1) q) (source 16). **Is that enough?** For the 0.9 level, just: the rule needs at least 9 paths
and there are 29 and 35. For 0.95 it needs 19, and here it lands on the single worst past error at both origins. And
the paths are not independent: consecutive backtests share five of six target months, so the real information is
closer to 5 or 6 blocks than to 29 or 35 (sources 17, 18). Conclusions at 0.9 are usable with caution; conclusions at
0.95 are indicative only.

Other details to fix in advance:

- **Same bank size for every method.** If SARIMA-24 fails to converge at a backtest origin, that origin is dropped
  for all five methods at that origin and logged. The run stops if fewer than 20 paths remain.
- **Early windows include the pandemic recovery.** Errors from 2023 may be larger than today's. This affects all
  methods but not equally (it hits the two seasonal methods hardest). Disclosed, not corrected.

### Joint draws - CHANGE (make explicit)

Occupied and available errors must be paired by backtest origin, and the six horizons kept together as one path.

- The gap is occupied / 0.90 - available. Errors in the two series are strongly related (the clean checkpoint showed
  them cancelling in the gap). Drawing them independently would overstate the spread of the gap and over-protect.
- Capacity carries over between months and ramps are limited, so the plan depends on the shape of the path, not only
  on each month's quantile. Errors at different horizons are serially dependent (source 18).

Because the gap is linear, pairing reduces to one gap-error path per backtest: e_gap = e_occupied / 0.90 - e_available.

### (b) Pinball loss at 0.9 - KEEP, with changes

Pinball loss at the cost-optimal quantile is the right accuracy metric (sources 7, 8, 9). Three corrections:

1. **It must score a quantile forecast, and that forecast must be defined.** The 0.9 quantile forecast for month t is
   the point forecast plus the ceil((n + 1) 0.9)-th smallest past gap error at that horizon. Scoring the point
   forecast itself at 0.9 is reported only as a reference (it shows the cost of having no margin).
2. **"Pinball loss equals the business cost" is true only month by month and without opening, closing and ramps.**
   For a month with gap d and capacity x: 500 x + 5000 max(d - x, 0) = 500 d + 5000 x pinball_0.9(d, x). The 500 d
   term is the same for every plan. The LP plan differs from the monthly quantile because it also weighs opening and
   closing costs and ramps, so pinball loss and realised cost can still disagree. Both are reported.
3. **Report coverage and CRPS as well.** Coverage (share of months with the actual gap at or below the 0.9 quantile)
   shows calibration directly. With 6 months per origin it moves in steps of 17 points, so it is reported as a count
   (for example 5 of 6) and pooled over both origins (12 months), with no decision rule attached. CRPS of the scenario
   set scores the whole distribution and does not depend on the chosen penalty (sources 8, 9).

Gap MAE and mean signed error of the point forecast stay in the table for continuity with the clean checkpoint.

### (c) Robustness - KEEP both, with changes

**Penalty sweep.** Keep. No source gives a measured cost for a missing bed; studies assume it and vary it, or infer it
(sources 23, 24, 25). The sweep p = 1,000, 2,500, 5,000, 10,000 covers q* = 0.5, 0.8, 0.9, 0.95. Notes:

- Each level is planned and scored at the same p, with hindsight recomputed for that p.
- The four levels use the same forecasts and the same windows. They test sensitivity to the cost assumption; they
  are not four independent confirmations and must not be counted as "wins".
- p = 10,000 is the 0.95 level: indicative only (see the table above).
- At p = 1,000 a shortfall can be cheaper than opening and closing a bed for one month (1,350). Plans may then accept
  shortfalls on purpose. This is correct behaviour and will be noted.

**Service-level version.** Keep, as a descriptive check. It is the form hospital studies and NHS guidance use
(sources 20, 22). Changes:

- Capacity must be at least the per-month quantile of the method's scenario gaps, using the same rank rule. If the
  ramp or the cap makes that unreachable, the requirement is lowered to what is reachable; this is logged.
- It is per-month protection, not protection of the whole six-month path. Joint protection would need the alpha / 6
  quantile (source 17), which 29-35 paths cannot give.
- Report shortfall bed-months as well as the number of short months, because frequency alone hides size (source 21).
- With six months, an expected 0.3 or 0.6 short months cannot be verified. No pass or fail is declared. For the
  ranking table, these plans are also costed at p = 5,000.
- alpha = 0.10 is close to the SAA plan at p = 5,000 and alpha = 0.05 to p = 10,000. The check is partly redundant
  with the sweep; its value is that it uses no cost for a missing bed at all.

### (d) Point-forecast LP as reference - KEEP, and add one plan

Keep the unprotected point LP plans. Add the **bias-corrected point plan**: the LP on the point forecast plus the
mean past error path, with no spread. It splits each method's gain into two parts:

- point cost - bias-corrected cost = what removing the method's known bias was worth;
- bias-corrected cost - protected cost = what the margin for uncertainty was worth.

Without it, "protection helped" could mean either.

### Leakage risks and prevention

| Risk | Prevention |
| --- | --- |
| A backtest sees a month after its own origin | Each backtest calls the verified `forecast_origin` on a frame cut at its own origin; that function refuses a frame that holds later months. |
| A backtest target falls after the planning origin | Backtest origins stop six months before the planning origin; every target month and training month is asserted to be at or before the origin and logged. |
| Scaling or features fitted across backtests | Every backtest refits from scratch inside its own 24-month window (sources 5, 19). |
| Target-month actuals read before plans are fixed | The plan stage loads data up to the origin only. Scenarios and plans for both origins are written and hashed before any target month is loaded; scoring verifies the hash. |
| Rules changed after results | Config with the design and rules is hashed before any backtest. |
| Later origin uses earlier origin's outcomes | The March 2026 bank contains October 2025 - March 2026 errors. This is legitimate (they precede that origin) but the two tests share data. Disclosed. |
| **Design chosen with knowledge of the results** | Not preventable. We already know which methods were biased in each window, that Persistence won at March 2026, and that SAA beat point planning for SARIMA in 2026. The rules are fixed in advance but the test is not blind. Disclosed in the config and README. |

### Winner and improvement measures

One primary endpoint, fixed in advance, so that the many variants cannot be searched for a preferred answer: **the
realised cost of the protected plan (SAA, raw own errors, p = 5,000).** Everything else is secondary. The rules are
in the final design below. They reuse the clean-checkpoint rules (1% tie band, joint best, combined excess, clear
winner) so the two experiments read the same way.

### What the proposed design missed

1. **Luck remains.** Two windows, about 5-6 independent error blocks. The README must say that equal protection makes
   the comparison fair on average, not that it identifies the best method.
2. **The gap is already a protected quantity.** It is measured against 90% occupancy, itself a safety ceiling (source
   22). A "shortfall" here means occupancy above 90%, not patients without beds. The 0.9 quantile sits on top of that.
3. **No re-planning.** All six months are fixed at the origin. A real planner would revise monthly, which would
   reduce the value of protection at long horizons.
4. **Point plans cannot match the checkpoint "exactly" for ExtraTrees.** Its fit differs between runs by about 1e-11
   beds (parallel summation; measured at 4.4e-11 in the checkpoint rerun). I propose: exact equality for the other
   four methods, 1e-6 beds for ExtraTrees-24. The same noise means the plan hash is not bit-reproducible across runs.
5. **Solver precision.** CBC returns 8 significant digits. Plan comparisons use 1e-3 beds and cost comparisons 1e-6
   relative, as in the checkpoint.
6. **Different bank sizes at the two origins** (29 and 35). Equal across methods at each origin, which is what
   fairness needs.
7. **Considered and not included:** giving every method the same margin in beds (for example Seasonal naive's errors
   for all). It would isolate the point forecast, but it would over-protect accurate methods and under-protect
   inaccurate ones, which is not how any of them would be used.

## Final proposed design

### Setup (unchanged from the clean checkpoint)

- Data: `data/raw_v6/Beds-publication-Timeseries-March-2020-October-2026.xlsx`, SHA-256 checked.
- Origins: September 2025 (targets October 2025 - March 2026) and March 2026 (targets April - September 2026).
- Methods: Mean-24, Persistence, Seasonal naive, SARIMA-24, ExtraTrees-24, by import from
  `scripts/clean_checkpoint/pipeline.py`, unedited. Each forecasts occupied and available from 24 months.
- Gap d = occupied / 0.90 - available. Costs 500 / 550 / 300 per bed; shortfall p per bed-month (base 5,000); cap
  15,000; ramps 5,000; x_0 = 0; no terminal charge. Plans are fixed at the origin and scored on actual gaps.

### Error bank (per origin, per method)

- Backtest origins: every month b from November 2022 to six months before the origin (29 and 35 expected).
- At each b: refit on the 24 months ending at b, forecast six months, record actual - forecast for both series.
- Gap error path: e_gap(b, h) = e_occupied(b, h) / 0.90 - e_available(b, h), h = 1..6. One path per backtest.
- If SARIMA-24 does not converge at b, drop b for all methods at that origin and log it. Stop below 20 paths.

### Scenarios and quantiles

- Scenario s for method m: gap forecast path of m plus error path s of m. Equal weights. Raw errors.
- Quantile at level q for month t: point forecast plus the ceil((n + 1) q)-th smallest error at that horizon (the
  largest if the rank exceeds n).

### Plans (per method and origin)

| Plan | Definition | Role |
| --- | --- | --- |
| Point | LP on the point forecast | Reference: no protection |
| Bias-corrected point | LP on the point forecast plus the mean error path | Reference: bias removed, no margin |
| **Protected** | SAA: one shared capacity plan minimising average cost over the scenarios, p = 5,000 | **Primary** |
| Protected, centred errors | SAA with errors centred per horizon | Sensitivity |
| Penalty sweep | Point and Protected at p = 1,000, 2,500, 10,000 | Robustness |
| Service level | Minimise operating cost with x_t at least the (1 - alpha) quantile, alpha = 0.10 and 0.05, lowered to the reachable level if needed | Robustness, descriptive |
| Hindsight | LP on the actual gaps, for each p | Floor |

### Measures

- Point forecast: gap MAE, mean signed gap error (actual - forecast).
- Quantile forecast at 0.9: mean pinball loss (beds), coverage (months at or below the quantile, of 6 and of 12).
- Reference: pinball loss at 0.9 of the point forecast. CRPS of the scenario set. Pinball loss at 0.5, 0.8 and 0.95
  for the sweep.
- Error bank summary per method: number of paths, mean and standard deviation of the gap error, average margin
  (0.9 quantile minus point forecast).
- Plans: operating cost, shortfall bed-months, shortfall cost, total, excess over hindsight; for service-level plans
  also the number of short months.

### Decision rules (fixed before any scoring)

Primary endpoint: realised cost of the Protected plan at p = 5,000. Five plans per origin.

Per origin:

- Tie band for cost: 1% of that origin's hindsight cost. Tie band for pinball loss and MAE: 5 beds. (Five beds of
  pinball loss over six months at 5,000 is about 1% of hindsight cost, so the two bands agree.)
- Rank the five Protected plans by realised cost. Plans within the tie band of the cheapest are "joint best".

Across both origins:

- Overall ranking: combined excess over hindsight.
- **Clear winner:** a method whose Protected plan is joint best at both origins, or that beats the overall runner-up
  by more than the tie band at each origin. Otherwise "no clear winner", listing the joint-best methods. If more than
  one method meets the definition, all are reported as joint winners.
- **Does the previous winner still win?** At each origin the previous winner is the method with the cheapest Point
  plan. It "still wins" if its Protected plan is joint best at that origin, and "no longer wins" otherwise; its
  Protected rank is reported.
- **Did protection help?** Per method and origin: Point cost - Protected cost. "Helped" if above the tie band, "hurt"
  if below minus the tie band, otherwise "no material difference".
- **Best-calibrated forecast:** lowest mean pinball loss at 0.9 across both origins; "clear" only if lowest at both
  origins by more than 5 beds. Coverage is reported without a rule.
- **Robust to the penalty:** apply the clear-winner rule at each p. The base result is "robust" if the outcome (the
  same winner, or "no clear winner") is the same at all four levels. The ranking at every level is shown either way.
  p = 10,000 and alpha = 0.05 are labelled indicative.
- **Service level:** no winner is declared. Plans are listed with operating cost, short months, shortfall bed-months
  and cost at p = 5,000.

Improvement measures (per origin and combined; combined ratios are ratios of sums):

- Value of protection: Point cost - Protected cost. Split into bias (Point - Bias-corrected) and margin
  (Bias-corrected - Protected).
- Saving against the Protected plans of Seasonal naive and of Persistence: baseline cost - plan cost.
- Share of avoidable cost removed: (baseline cost - plan cost) / (baseline cost - hindsight cost), same baselines.
- Skill against Seasonal naive: 1 - loss / Seasonal naive loss, for pinball loss at 0.9 and for gap MAE.
- Excess over hindsight, in model units and as a percentage of hindsight cost.

### Disclosures to record in the config

- The outcome months, the bias of each method in each window, the Persistence result at March 2026 and the earlier
  SARIMA SAA result were all known before this design was written. The rules are fixed in advance but not blind.
- Error paths overlap; 29 and 35 paths carry the information of about 5 and 6 independent blocks. The 0.95 level is
  the largest past error.
- The October 2026 publication is used throughout; NHS revised September 2026 between the September and October 2026
  publications (available -87, occupied -2).
- SARIMA-24 and ExtraTrees-24 are the small-sample variants of the clean checkpoint.
- Costs are illustrative model units (validated: False).

### Verification additions to the Phase 4 list

- Point plans equal the clean-checkpoint frozen LP plans: exactly for four methods, within 1e-6 beds for
  ExtraTrees-24.
- SAA with every scenario equal to the point forecast gives the point plan within 1e-3 beds.
- SAA realised cost against its own scenarios is not above the Point plan's average cost against the same scenarios
  (the Point plan is feasible for the SAA problem).
- Service-level plans satisfy the quantile constraint or the logged reachable level.
- Second solver (HiGHS) agrees with CBC on every SAA and service-level plan.

## Decisions needed before the run

1. Raw errors as the primary rule, centred as sensitivity (recommended), or the reverse?
2. Add the bias-corrected point plan (recommended)?
3. Accept the 1e-6 bed tolerance for ExtraTrees-24 where the brief says "exactly"?
4. Accept that the 0.95 level is reported as indicative only?
