# Verification: value of optimisation and protection

Scientific verification completed before publication. On resumption, saved results were reused: no model, plan generation, scoring, scientific verification suite, tests or figures were rerun.

| Check | Status | Evidence |
| --- | --- | --- |
| Phase 0: reproduce the two anchors | PASS | 10 method-origin totals; maximum relative Rule-cost difference 7.052152644556709e-15; maximum forecast difference 2.91038304567337e-11 beds; 5 saved rolling README rows agree. |
| Hashes, cutoff provenance and deterministic Rule regeneration | PASS | All 125 Rule schedules reproduce bit-for-bit; source forecast and plan hashes verified; target dates and fitting cutoffs agree |
| Rule feasibility | PASS | All caps, up/down ramps, positive flows, month order and zero starting capacity checked |
| Original files unchanged | PASS | 555 hashes match, including old scripts, outputs and pre-existing untracked files |
| Independent cost reconstruction and hindsight bounds | PASS | 375 schedules; maximum relative cost difference 3.46e-16; every monthly sum reconciles; reused scores unchanged |
| M1-M7, including pooled and excluded-anchor analyses | PASS | Independent means, medians, extremes, counts, ratios of sums, event counter, and all six subset values/ranges agree |
| Expectations evaluated without gating results | PASS | E1=True, E2=True, E3=True; adverse cases retained |
| Original files unchanged | PASS | 555 hashes match, including old scripts, outputs and pre-existing untracked files |
| Existing and new pytest suites | PASS | 68 passed, 3278 warnings in 30.61s |

## Frozen inputs

Config SHA-256: `b95a791442544a9a93ccfa9f0904220c4571b85f2b51eb21a278d318edba7c90`.
Rule-plan SHA-256: `1dea041a3fdf0828c7bc3e25b48b8accf7c953f881ec9ead511865e8f1b7cc03`.
Reused rolling-plan SHA-256: `f797670d1363ffd9fe3256854b4b6cce98a2a52fd0949c44ff64dd139dae54ff`.

The config and all 125 Rule schedules were frozen before new rolling Rule scoring. The required two-anchor reproduction was the only pre-registration Rule scoring. Forecasts, Point LP, Protected and hindsight results were reused unchanged.

The common data end remains October 2026; trailing blank placeholders were already excluded in the prior study. The 25-origin list is unchanged: October 2025 (occupied SARIMA) and January 2026 (available SARIMA) were excluded for nonconvergence, and 15 earlier origins had insufficient calibration paths. No exclusions were chosen from these new results.

## Interpretation and precision

E1, E2 and E3 held under the registered definitions; expectations were not stop conditions. E1 uses a 0.001-bed schedule tolerance for saved CBC precision. E2 uses 1e-6 relative cost tolerance against Rule cost. Tiny negative raw optimisation benefits remain in the CSV files; none exceeds that tolerance. Protection uses a 1%-of-hindsight tie band.

Equal cases: 69, comprising Mean-24: 25, Persistence: 25, Seasonal naive: 8, SARIMA-24: 6, ExtraTrees-24: 5.
LP cheaper/equal/dearer: 56/69/0. Protection helped/tie/hurt: 60/0/65.

Close-then-reopen counts each removal month once if an addition occurs within the next two months inside the same plan. Flows below 1e-6 beds are ignored. Gap shares use ratios of sums, with no clipping. Pooled results weight every method-origin equally. Six-offset ranges are descriptive, not confidence intervals.

Detailed evidence: [checks](verification_checks.json), [phase 0](phase0.json), [saved test log](pytest.txt), [equal origins](equal_cases.json), [publication audit](publication_audit.json).

## Publication audit

- **PASS:** Frozen config, Rule plans, source forecasts/plans/scores, all saved scientific outputs and figures match their hashes; all prior files are unchanged except the exact authorised README edit and addition.
- **PASS:** Every numeric entry in all seven new README tables matches saved CSVs: 196 data cells, including counts, dates, distributions, subset ranges and excluded-anchor results.
- **PASS:** All narrative numerical claims, method constants, 69 equal cases, E1-E3, five-sentence Answer, pooled shares and best combination agree with the saved outputs.
- **PASS:** All new README links resolve; both unchanged PNGs are 300 dpi, both SVGs parse, and figure-data tables match the saved costs and means. Both figures were also visually inspected.
