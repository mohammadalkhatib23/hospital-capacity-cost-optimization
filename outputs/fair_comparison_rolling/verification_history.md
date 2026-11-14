# Rolling comparison verification ? stopped in Phase 1

Phase 0: PASS. The unchanged Fable register/plan/score stages were run in `phase0/`.
All ten primary Protected totals matched the originals exactly. Maximum forecast
absolute difference: 4.3655745685100555e-11 beds (allowed 1e-6).
Original configuration, plan hashes and workbook SHA-256 passed.
Existing pytest suite: 48 passed, 0 failed (20.74 seconds).

Phase 1 eligibility precheck: FAIL at `scripts/fair_comparison_rolling/rolling.py:94`.
The new check compared the latest parsed date label with October 2026. The workbook
has blank template rows dated November 2026?March 2027, so the latest label is
March 2027. These rows are not published observations. The unchanged loader
returns 71 complete observations from December 2020 through October 2026.
This is an error in the new date-label precheck, not a failed workbook hash or a
failure to reproduce Fable's results. No correction was applied after the failure,
in accordance with the instruction to stop rather than work around failed checks.

No rolling config/hash, plans/hash, scores, summary, figures, or README update were
produced. No existing source, data or outputs were modified. Nothing was committed
or pushed. The only new files are in the authorized rolling script/output folders.

To resume, the eligibility precheck needs to identify the final month with both
occupied and available observations rather than the final formatted date label.
The expected final planning origin remains April 2026.

## Approved correction before rolling scoring

The user approved correction of the Phase 1 date-boundary precheck. The data end
now means the last month where BOTH G&A beds available and G&A beds occupied
are non-blank. Trailing date-only rows are ignored. The code asserts October 2026
and rejects any missing/blank month inside the observed span or any required
training, backtest, or evaluation window. No forecasting/model/scenario/LP
specifications were changed. This correction was made before rolling registration,
planning, or scoring.

Six new tests passed (0.79 seconds), including trailing placeholder rows, a blank
in either series inside the span, a partial final observation, whitespace blanks,
and shared-cache cutoff/completeness/reuse checks. Phase 0 remains passed.
The earlier stopped attempt is preserved in boundary_failure_history.json.

## Current status: stopped at planning-origin convergence check

Approved boundary correction: PASS. The data end is October 2026, with 71 complete monthly observations from December 2020. Blank placeholders from November 2026 through March 2027 are excluded. No internal blanks are allowed.

New unit tests: 11 passed (6 boundary/cache tests and 5 decision-rule tests). Existing tests previously passed 48/48; their source files remain unchanged. Phase 0 reproduction remains PASS.

Phase 1 registration: PASS. There are 27 date/bank-eligible origins, February 2024 through April 2026. Fifteen earlier candidates (November 2022 through January 2024) have fewer than 9 complete retained paths. The retained banks contain 9?34 paths. January 2023 and October 2025 backtests are dropped for all methods under the original SARIMA nonconvergence rule. The shared banks match the original builder at September 2025 and March 2026 within 6.321e-11 beds.

Config SHA-256: `1d93530aa10a4a110c3f60f979617d50a7c1f0fc50775657ccecacdf08f2f15d`.

Planning-origin precheck: **FAIL**. A direct call to the unchanged `pipeline.forecast_origin`, with data cut at October 2025, raised `SARIMA-24 did not converge for occupied at 2025-10; stopping`. Training window: November 2023?October 2025. Targets: November 2025?April 2026. This confirms the same convergence failure found during historical-bank construction.

The authorized drop rule covers historical backtests. It does not define exclusion of a requested planning origin. No model rescue, tolerance change, or planning-origin exclusion has been applied. No rolling plans, plan hash, or scores were generated; Phase 3/4 and publication were not attempted. No files were committed or pushed. All 212 files in the prior-file manifest remain unchanged.

Continuing with the unchanged model would require a decision on excluding October 2025 as a planning origin for all five methods, then a newly registered origin list. The current registration and failure are retained for review.

### Registered origin inventory

| Origin | Retained paths | Status |
| --- | ---: | --- |

| 2022-11 | 0 | excluded |

| 2022-12 | 0 | excluded |

| 2023-01 | 0 | excluded |

| 2023-02 | 0 | excluded |

| 2023-03 | 0 | excluded |

| 2023-04 | 0 | excluded |

| 2023-05 | 1 | excluded |

| 2023-06 | 2 | excluded |

| 2023-07 | 2 | excluded |

| 2023-08 | 3 | excluded |

| 2023-09 | 4 | excluded |

| 2023-10 | 5 | excluded |

| 2023-11 | 6 | excluded |

| 2023-12 | 7 | excluded |

| 2024-01 | 8 | excluded |

| 2024-02 | 9 | included |

| 2024-03 | 10 | included |

| 2024-04 | 11 | included |

| 2024-05 | 12 | included |

| 2024-06 | 13 | included |

| 2024-07 | 14 | included |

| 2024-08 | 15 | included |

| 2024-09 | 16 | included |

| 2024-10 | 17 | included |

| 2024-11 | 18 | included |

| 2024-12 | 19 | included |

| 2025-01 | 20 | included |

| 2025-02 | 21 | included |

| 2025-03 | 22 | included |

| 2025-04 | 23 | included |

| 2025-05 | 24 | included |

| 2025-06 | 25 | included |

| 2025-07 | 26 | included |

| 2025-08 | 27 | included |

| 2025-09 | 28 | included |

| 2025-10 | 29 | included |

| 2025-11 | 30 | included |

| 2025-12 | 31 | included |

| 2026-01 | 32 | included |

| 2026-02 | 33 | included |

| 2026-03 | 34 | included |

| 2026-04 | 34 | included |

## Authorized planning-origin convergence rule, before scoring

Registration `1d93530aa10a4a110c3f60f979617d50a7c1f0fc50775657ccecacdf08f2f15d` was superseded before any rolling scoring. The unchanged SARIMA was checked on both targets at every candidate origin. An origin with either nonconvergent target is excluded for all five methods. No model specifications changed. Final origins: 25. Convergence exclusions: [{"origin": "2025-10", "failed_targets": ["occupied"]}, {"origin": "2026-01", "failed_targets": ["available"]}]. New config SHA-256: `86142628266cd7e35476d3a6f372911ab6a2abdd634d72588d4322c93f6bd976`.

Before planning and scoring, result-export review made the monthly shortfall_cost column use its actual penalty, consistent with the inherited score_plan total and monthly_cost. The implementation digest was finalized in config `8715467371b89f2f10a0124613ce7387bdc9296d9f05026c0a91b6e63ca85e48`; `86142628266cd7e35476d3a6f372911ab6a2abdd634d72588d4322c93f6bd976` was an interim registration before this export review. No rule or model specification changed.
