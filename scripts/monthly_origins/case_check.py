"""Compare the monthly extension with the two published frozen case studies."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/case_study"))
import monthly_views as original


def compare_rows(candidate, reference, keys, columns):
    """Match by the full key before comparing the published measurements."""
    left = candidate.set_index(keys, verify_integrity=True).sort_index()
    right = reference.set_index(keys, verify_integrity=True).sort_index()
    pd.testing.assert_index_equal(left.index, right.index)
    np.testing.assert_allclose(left[columns], right[columns], rtol=1e-6, atol=1e-8)


def verify_cases(forecasts, monthly, costs):
    cases = original.load_cases(original.verify_hashes())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    case_study = readme.split("## All monthly origins:", 1)[0]
    original.verify_readme_numbers(case_study, cases)
    for case in cases:
        origin = case["origin"]
        predictions = forecasts[forecasts.origin == origin].copy()
        predictions["month"] = pd.to_datetime(predictions.month).dt.strftime("%Y-%m-%d")
        fields = ["forecast_occupied", "forecast_available", "forecast_gap"]
        compare_rows(predictions, case["forecasts"], ["month", "method"], fields)
        actuals = case["actual"].rename(columns={
            "occupied": "actual_occupied", "available": "actual_available", "gap": "actual_gap"
        })
        for _, group in predictions.groupby("method"):
            compare_rows(group, actuals, ["month"], ["actual_occupied", "actual_available", "actual_gap"])

        plans = monthly[monthly.origin == origin].copy()
        plans["month"] = pd.to_datetime(plans.month).dt.strftime("%Y-%m-%d")
        plans["operating"] = plans[["running", "opening", "closing"]].sum(axis=1)
        saved = pd.concat([rows.assign(method=m, plan=p)
                           for (m, p), rows in case["schedules"].items()])
        compare_rows(plans, saved, ["method", "plan", "month"],
                     ["x", "a", "r", "shortfall", "spare", "operating", "shortfall_cost", "monthly_cost"])
        compare_rows(costs[costs.origin == origin], case["costs"].reset_index(),
                     ["method"], ["Rule", "LP", "Protected", "Hindsight"])
    return ("September 2025 and March 2026 match the published forecasts, actuals, "
            "32 schedules and six-month scores at 1e-6 relative tolerance. "
            "All 13 original README tables and the case-study Answer also match.")
