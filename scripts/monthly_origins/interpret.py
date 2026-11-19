"""Describe verified monthly plans. All cross-method rankings use common origins."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


OUT = Path(__file__).resolve().parents[2] / "outputs" / "monthly_origins"
METHODS = ["Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24"]
PLANS = ["Rule", "LP", "Protected"]
REL = 1e-6
FLOW_TOL = 1e-6


def classify(value, band):
    return np.select([value > band, value < -band], ["helped", "hurt"], default="tied")


def forecast_dips(gaps):
    """A fall and a later rise in six forecast months; ignore numerical noise."""
    changes = np.diff(gaps)
    non_flat = changes[np.abs(changes) > FLOW_TOL]
    return bool(np.any((non_flat[:-1] < 0) & (non_flat[1:] > 0)))


def describe_cases(forecasts, costs):
    seasonal_actuals = forecasts[["month", "actual_gap"]].drop_duplicates()
    winter = seasonal_actuals[seasonal_actuals.month.between("2025-12-01", "2026-02-01")]
    peak_month = winter.loc[winter.actual_gap.idxmax(), "month"]
    by_method = []
    for (origin, method), months in forecasts.groupby(["origin", "method"]):
        months = months.sort_values("horizon")
        by_method.append(dict(
            origin=origin, method=method,
            origin_season="Apr-Sep" if 4 <= int(origin[-2:]) <= 9 else "Oct-Mar",
            contains_winter_peak=peak_month in set(months.month),
            signed_error=(months.actual_gap - months.forecast_gap).mean(),
            forecast_dip=forecast_dips(months.forecast_gap),
        ))
    cases = costs.merge(pd.DataFrame(by_method), on=["origin", "method"], validate="one_to_one")
    cases["common"] = cases.groupby("origin").method.transform("nunique") == len(METHODS)
    cases["lp_excess"] = cases.LP - cases.Hindsight
    cases["value_optimisation"] = cases.Rule - cases.LP
    cases["value_protection"] = cases.LP - cases.Protected
    cases["optimisation_effect"] = classify(cases.value_optimisation, REL * cases.Rule.abs())
    cases["protection_effect"] = classify(cases.value_protection, .01 * cases.Hindsight)
    cases["protection_sign"] = classify(cases.value_protection, 0)
    cases["forecast_direction"] = np.select(
        [cases.signed_error > 0, cases.signed_error < 0], ["low", "high"], default="balanced"
    )
    cases["lp_rank"] = cases.loc[cases.common].groupby("origin").LP.rank(method="min")
    return cases, peak_month


def winners(cases):
    records = []
    for origin, group in cases.groupby("origin"):
        record = dict(origin=origin, common=bool(group.common.all()),
                      origin_season=group.origin_season.iloc[0],
                      contains_winter_peak=bool(group.contains_winter_peak.iloc[0]))
        if record["common"]:
            lp = group.nsmallest(1, "LP").iloc[0]
            record.update(lp_method=lp.method, lp_cost=lp.LP, lp_excess=lp.lp_excess)
            plans = group.melt(id_vars=["method"], value_vars=PLANS, var_name="plan", value_name="cost")
            best_cost = plans.cost.min()
            cheapest = plans[plans.cost - best_cost <= REL * abs(best_cost)]
            names = cheapest.groupby("method", sort=False).plan.agg("/".join)
            record.update(best_method=" / ".join(names.index), best_plan=" / ".join(names),
                          best_cost=best_cost, best_excess=best_cost - lp.Hindsight)
        records.append(record)
    return pd.DataFrame(records)


def tallies(series, categories):
    return {name: int(series.eq(name).sum()) for name in categories}


def protection_association(cases):
    """Report the scatter quadrants separately from the saved materiality band."""
    result = {}
    for scope, group in [("available", cases), ("common", cases[cases.common])]:
        counts = {}
        for label in ["protection_sign", "protection_effect"]:
            counts[label] = {
                direction: tallies(group.loc[group.forecast_direction == direction, label],
                                   ["helped", "hurt", "tied"])
                for direction in ["low", "high", "balanced"]
            }
        result[scope] = dict(n=len(group), **counts, spearman_rho=float(
            spearmanr(group.signed_error, group.value_protection).statistic
        ))
    return result


def method_summaries(cases):
    records = {}
    for method, group in cases.groupby("method", sort=False):
        common = group[group.common]
        records[method] = dict(
            available_origins=len(group), common_origins=len(common),
            signed_error_counts=tallies(group.forecast_direction, ["low", "high", "balanced"]),
            optimisation_counts=tallies(group.optimisation_effect, ["helped", "tied", "hurt"]),
            dipped=int(group.forecast_dip.sum()),
            dip_and_helped=int((group.forecast_dip & group.optimisation_effect.eq("helped")).sum()),
            lp_wins_common=int(common.lp_rank.eq(1).sum()),
            lp_last_common=int(common.lp_rank.eq(len(METHODS)).sum()),
            lp_mean_excess_common=float(common.lp_excess.mean()),
            mean_signed_error_common=float(common.signed_error.mean()),
            mean_protection_value_common=float(common.value_protection.mean()),
            lp_rank_min_common=int(common.lp_rank.min()),
            lp_rank_max_common=int(common.lp_rank.max()),
        )
    return records


def anchor_positions(cases):
    """Rank descriptive within-method benefits among that method's available origins."""
    records = []
    for method, group in cases.groupby("method", sort=False):
        group = group.set_index("origin")
        for origin in ["2025-09", "2026-03"]:
            for column in ["value_optimisation", "value_protection"]:
                if column == "value_optimisation" and group.optimisation_effect.eq("tied").all():
                    continue  # Ranking solver noise cannot distinguish equal schedules.
                values = group[column]
                records.append(dict(
                    method=method, origin=origin, metric=column, value=float(values.loc[origin]),
                    comparison_origins=len(values), median=float(values.median()),
                    raw_rank_descending=int(values.rank(ascending=False, method="min").loc[origin]),
                ))
    return records


def seasonal_comparisons(cases):
    common = cases[cases.common]
    results = {}
    for dimension in ["origin_season", "contains_winter_peak"]:
        results[dimension] = {}
        for name, group in common.groupby(dimension):
            wins = group.loc[group.lp_rank == 1, "method"]
            results[dimension][str(name)] = dict(
                origin_count=group.origin.nunique(), wins=tallies(wins, METHODS),
                mean_lp_excess={m: float(v) for m, v in group.groupby("method").lp_excess.mean().items()},
            )
    return results


def main():
    verification = (OUT / "verification.md").read_text(encoding="utf-8")
    if "All Phase 1 checks passed." not in verification or "**FAIL**" in verification:
        raise RuntimeError("Assembly must pass verification before interpretation.")
    forecasts = pd.read_csv(OUT / "forecasts.csv", float_precision="round_trip")
    costs = pd.read_csv(OUT / "costs.csv", float_precision="round_trip")
    cases, peak_month = describe_cases(forecasts, costs)
    per_origin = winners(cases)
    common = cases[cases.common]
    assert len(cases) == 63 and cases.common.sum() == 55
    assert len(per_origin) == 13 and per_origin.common.sum() == 11
    assert per_origin.loc[~per_origin.common, "best_method"].isna().all()
    assert per_origin.loc[~per_origin.common, "lp_method"].isna().all()
    summary = dict(
        origins=len(per_origin), common_origins=int(per_origin.common.sum()),
        available_cases=len(cases), common_cases=int(cases.common.sum()),
        winter_peak_month=peak_month,
        definitions=dict(
            signed_error="Six-month mean of actual gap minus forecast gap, in beds.",
            costs="Raw illustrative units. LP excess = LP minus Hindsight.",
            optimisation="Rule minus LP, with equality band 1e-6 times Rule cost.",
            protection="LP minus Protected. Scatter quadrants use raw signs; material effect uses 1% of Hindsight.",
            dip="A fall followed by a rise inside the six forecast months, ignoring changes of at most 1e-6 beds.",
            rank="Minimum realised LP cost. Overall winners include Rule/LP ties within relative 1e-6.",
            comparisons="Cross-method ranks and means use only the 11 common origins. Other counts use available pairs.",
            overlap="Adjacent six-month windows share five target months. These counts are descriptive.",
        ),
        per_method=method_summaries(cases),
        common_pooled=dict(
            method_origin_count=len(common),
            mean_value_optimisation=float(common.value_optimisation.mean()),
            mean_value_protection=float(common.value_protection.mean()),
            protection_effect=tallies(common.protection_effect, ["helped", "tied", "hurt"]),
        ),
        season_comparisons_common=seasonal_comparisons(cases),
        optimisation_by_dip={
            str(dip): tallies(group.optimisation_effect, ["helped", "tied", "hurt"])
            for dip, group in cases.groupby("forecast_dip")
        },
        protection_vs_error=protection_association(cases),
        anchor_positions=anchor_positions(cases),
    )
    cases.to_csv(OUT / "method_origin.csv", index=False, float_format="%.17g", lineterminator="\n")
    per_origin.to_csv(OUT / "winners.csv", index=False, float_format="%.17g", lineterminator="\n")
    (OUT / "interpretation.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n",
                                             encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
