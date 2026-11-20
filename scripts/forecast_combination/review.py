"""Part B independent checks on frozen files. Stop at the first failed check."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/forecast_combination"
METHODS = ["Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24"]
COMBOS = ["Combo", "Combo (4 methods)"]
BASE = 5000
HEADING = "## Part B — results sanity check"


def read(name):
    return pd.read_csv(OUT / name, float_precision="round_trip")


def table(headers, rows):
    return "\n".join("| " + " | ".join(map(str, row)) + " |"
                     for row in [headers, ["---"] * len(headers), *rows])


def money(value):
    return f"{value / 1e6:+.3f}"


def individual_and_combo():
    f = read("forecasts.csv")
    names = METHODS + COMBOS
    f = f[f.method.isin(names)]
    keys = ["origin", "month", "horizon"]
    individual = f[f.method.isin(METHODS)].pivot(index=keys, columns="method", values="forecast_gap")
    combo = f[f.method.isin(COMBOS)].set_index(keys, verify_integrity=True).sort_index()
    pd.testing.assert_index_equal(individual.index, combo.index)
    expected_sizes = combo.method.map({"Combo": 5, "Combo (4 methods)": 4})
    assert individual.count(axis=1).eq(expected_sizes).all()
    assert combo.forecast_gap.notna().all()
    return individual, combo


def convex_range():
    individual, combo = individual_and_combo()
    eligible = combo.forecast_gap.between(individual.min(axis=1), individual.max(axis=1))
    assert eligible.all(), f"Combo outside the individual forecast range at {eligible[~eligible].index.tolist()}"
    counts = combo.method.value_counts()
    return (f"The Combo gap fell inside the individual forecasts' range in all {len(combo)} monthly predictions "
            f"({counts['Combo']} five-method and {counts['Combo (4 methods)']} four-method forecasts).")


def signed_errors():
    individual, combo = individual_and_combo()
    actual = read("actuals.csv").set_index(individual.index.names, verify_integrity=True).actual_gap.sort_index()
    pd.testing.assert_index_equal(actual.index, individual.index)
    errors = (actual - combo.forecast_gap) - individual.rsub(actual, axis=0).mean(axis=1)
    largest = errors.abs().max()
    origin_largest = errors.groupby(level="origin").mean().abs().max()
    assert np.isfinite(errors).all() and largest <= 1e-6 and origin_largest <= 1e-6, (
        f"Disagreement {largest:.12g} beds monthly, {origin_largest:.12g} beds by origin"
    )
    return (f"With signed error defined as actual minus forecast, the maximum discrepancy was "
            f"{largest:.3g} beds over individual months and {origin_largest:.3g} over "
            "six-month means. Both are within 1e-6 beds.")


def rule_vs_lp():
    totals = read("costs.csv")
    same = totals[totals.method.isin(COMBOS) & totals.penalty.eq(BASE)]
    costs = same.pivot(index="origin", columns="plan", values="total")
    assert len(costs) == 27 and np.isfinite(costs[["Rule", "LP"]]).all().all()
    change = costs.LP - costs.Rule
    tolerance = 1e-6 * costs.Rule
    dearer = change[change > tolerance]
    if not dearer.empty:
        raise AssertionError("Combo LP materially exceeded its Rule at " + ", ".join(
            f"{origin} (LP {costs.loc[origin, 'LP']:,.3f}, Rule {costs.loc[origin, 'Rule']:,.3f}, "
            f"difference {delta:,.3f}, tolerance {tolerance.loc[origin]:,.3f})"
            for origin, delta in dearer.items()
        ))
    better = (change < -tolerance).sum()
    return (f"At the base shortage price of {BASE:,}, LP saved money at {better} origins and "
            f"tied the Rule at {len(costs) - better} using a tolerance of 1e-6 times the Rule cost. "
            "Both four-method origins are included.")


def hand_check():
    p1 = set(json.loads((OUT / "config.json").read_text(encoding="utf-8"))["populations"]["P1"])
    monthly = read("monthly_costs.csv")
    m = monthly[monthly.origin.isin(p1) & monthly.method.eq("Combo") &
                monthly.plan.eq("LP") & monthly.penalty.eq(BASE)].sort_values(["origin", "horizon"])
    short_rows = m[m.shortfall > 1]
    example = short_rows.iloc[0] if len(short_rows) else m.iloc[0]
    origin, month, h = example.origin, example.month, example.horizon
    forecasts = read("forecasts.csv")
    group = forecasts[(forecasts.origin == origin) & (forecasts.month == month)].set_index("method")
    individuals = group.loc[METHODS]
    occ, avail = (math.fsum(individuals[c]) / len(METHODS) for c in ["forecast_occupied", "forecast_available"])
    forecast_gap = occ / .9 - avail
    np.testing.assert_allclose([occ, avail, forecast_gap], group.loc["Combo", [
        "forecast_occupied", "forecast_available", "forecast_gap"]].to_numpy(dtype=float), rtol=0, atol=1e-6)
    actuals = read("actuals.csv")
    actual = actuals[(actuals.origin == origin) & (actuals.month == month)].iloc[0]
    gap = actual.actual_occupied / .9 - actual.actual_available
    plans = read("plans.csv")
    frozen = plans[(plans.origin == origin) & (plans.method == "Combo") &
                   (plans.plan == "LP") & (plans.penalty == BASE)].set_index("horizon")
    x = frozen.loc[h, "x"]
    previous = 0 if h == 1 else frozen.loc[h - 1, "x"]
    opening, closing, short = max(x - previous, 0), max(previous - x, 0), max(gap - x, 0)
    cost = 500 * x + 550 * opening + 300 * closing + BASE * short
    np.testing.assert_allclose([gap, x, opening, closing, short], [actual.actual_gap, example.x,
                               example.a, example.r, example.shortfall], rtol=0, atol=1e-6)
    assert math.isclose(cost, example.monthly_cost, rel_tol=1e-12, abs_tol=1e-6)
    show = lambda series: " + ".join(f"{v:,.6f}" for v in series)
    return (
        f"At the **{origin} origin, target month {month[:7]}**, the five forecasts are listed in "
        "the order Mean-24, Persistence, Seasonal naive, SARIMA-24 and ExtraTrees-24. "
        "Displayed inputs are rounded to six decimals. The calculation used the saved precision.\n\n"
        f"- Mean occupied = ({show(individuals.forecast_occupied)}) / 5 = {occ:,.6f}.\n"
        f"- Mean available = ({show(individuals.forecast_available)}) / 5 = {avail:,.6f}.\n"
        f"- Combo gap = {occ:,.6f} / 0.90 − {avail:,.6f} = **{forecast_gap:,.6f} beds**.\n"
        f"- Actual gap = {actual.actual_occupied:,.0f} / 0.90 − {actual.actual_available:,.0f} "
        f"= {gap:,.6f} beds. Frozen capacity moved from {previous:,.6f} to {x:,.6f}, "
        f"opening {opening:,.6f} and closing {closing:,.6f} beds.\n"
        f"- Shortfall = max(0, {gap:,.6f} − {x:,.6f}) = **{short:,.6f} beds**.\n"
        f"- LP monthly cost = 500 × {x:,.6f} + 550 × {opening:,.6f} + 300 × {closing:,.6f} "
        f"+ {BASE:,} × {short:,.6f} = **{cost:,.2f} model units**, matching the saved bill."
    )


def exact_comparators():
    keys = ["origin", "method", "plan"]
    costs = read("costs.csv")
    costs = costs[costs.method.isin(METHODS) & costs.plan.isin(["LP", "Protected"]) & costs.penalty.eq(BASE)]
    observed = costs.set_index(keys, verify_integrity=True).total.sort_index()
    monthly = read("../monthly_origins/costs.csv").melt(
        id_vars=["origin", "method"], value_vars=["LP", "Protected"], var_name="plan", value_name="total")
    rolling = read("../fair_comparison_rolling/totals.csv")
    rolling = rolling[rolling.penalty.eq(BASE) & rolling.kind.isin(["point", "protected"])].copy()
    rolling["plan"] = rolling.kind.map({"point": "LP", "protected": "Protected"})
    for label, source in [("monthly", monthly), ("rolling", rolling)]:
        expected = source.set_index(keys, verify_integrity=True).total.sort_index()
        subset = observed[observed.index.get_level_values("origin").isin(source.origin)]
        pd.testing.assert_index_equal(subset.index, expected.index)
        mismatches = subset[subset.ne(expected)]
        if len(mismatches):
            i = mismatches.index[0]
            raise AssertionError(f"{len(mismatches)} unequal {label} scores. First {i}: "
                                 f"output {subset.loc[i]:.17g}, source {expected.loc[i]:.17g}.")
    return (f"All {len(rolling)} individual LP and Protected costs in the rolling study and all "
            f"{len(monthly)} in the monthly study agree exactly as saved. The two partial origins "
            "have monthly records only. Overlapping records were checked against both sources.")


def concentration():
    p1 = json.loads((OUT / "config.json").read_text(encoding="utf-8"))["populations"]["P1"]
    costs = read("costs.csv")
    protected = costs[(costs.plan == "Protected") & (costs.penalty == BASE)].pivot(
        index="origin", columns="method", values="total").loc[p1, ["Combo", *METHODS]]
    assert np.isfinite(protected).all().all()
    summary, ranks = [], []
    reversed_by_one, reversed_by_two, large = [], [], []
    for method in METHODS:
        diff = (protected.Combo - protected[method]).sort_values(kind="stable")
        net = math.fsum(diff)
        supporting = diff if net < 0 else diff.iloc[::-1]
        remaining_one = net - supporting.iloc[0]
        remaining_two = remaining_one - supporting.iloc[1]
        share = abs(supporting.iloc[:2].sum() / net) if net else math.nan
        if net and np.sign(remaining_one) != np.sign(net):
            reversed_by_one.append(method)
        if net and np.sign(remaining_two) != np.sign(net):
            reversed_by_two.append(method)
        if share >= .5:
            large.append(method)
        summary.append([method, money(net), supporting.index[0], money(remaining_one),
                        supporting.index[1], money(remaining_two), f"{100 * share:.1f}%"])
        ranks.append([(o, money(v), money(c)) for (o, v), c in zip(diff.items(), diff.cumsum())])
    headers = ["Comparator", "Net Δ", "Drop first origin", "Net after one",
               "Also drop", "Net after two", "Top two / net"]
    details = [table(["Rank", *METHODS],
                     [[i + 1, *[" · ".join(method[i]) for method in ranks]] for i in range(len(p1))])]
    def deletion_result(items, k):
        if items:
            return f"Removing {k} erased or reversed the net result against " + ", ".join(items) + "."
        return f"Every net advantage survived removing {k}."
    return "\n\n".join([
        f"For each comparator at the {len(p1)} primary origins, Δ = Combo Protected cost minus its "
        "Protected cost, in millions. A negative amount favours Combo. The hindsight benchmark "
        "cancels because both schedules face the same origin.",
        table(headers, summary),
        "For each comparison we removed the origin, then the pair, contributing most in the "
        "direction of the original net difference. These removals have the largest possible effect "
        "against its sign. Percentages use the absolute sum of those two contributions divided by "
        "the absolute net difference, so opposing months can take the percentage over 100%.",
        deletion_result(reversed_by_one, "the largest contributor") + " " +
        deletion_result(reversed_by_two, "the two largest contributors") + " " +
        (f"Two origins account for at least half the net magnitude against **{', '.join(large)}**. "
         if large else "Each pair contributes under half the net magnitude for every comparator. ") +
        "The shared target months also limit the evidence to about four independent windows.",
        "<details>\n<summary>Sorted differences and running totals at all primary origins</summary>",
        "Each column reads down independently, from the largest Combo saving to its largest extra "
        "cost. Each cell gives the origin, Δ and cumulative Δ in millions. Rounding is for display.",
        *details, "</details>",
    ])


def bank_widths():
    p1 = json.loads((OUT / "config.json").read_text(encoding="utf-8"))["populations"]["P1"]
    bank = read("banks.csv")
    primary = bank[bank.origin.isin(p1) & bank.method.isin([*METHODS, "Combo"])]
    groups = primary.groupby(["origin", "horizon", "method"]).error_gap
    quantiles = groups.quantile([.1, .9], interpolation="linear").unstack()
    widths = (quantiles[.9] - quantiles[.1]).unstack("method")[["Combo", *METHODS]]
    assert len(widths) == len(p1) * 6 and np.isfinite(widths).all().all()
    mean_width = widths.groupby(level="horizon").mean()
    narrower = widths[METHODS].gt(widths.Combo, axis=0)
    consistently_narrower = widths["Seasonal naive"].lt(widths.Combo).groupby(level="horizon").all()
    horizons = ", ".join(map(str, consistently_narrower[consistently_narrower].index))
    table_width = table(["Horizon", "Combo (beds)", *METHODS],
                        [[h, *[f"{v:,.1f}" for v in row]] for h, row in mean_width.iterrows()])
    table_counts = table(["Horizon", *METHODS],
                         [[h, *map(int, row)] for h, row in narrower.groupby(level="horizon").sum().iterrows()])
    # Paired errors can share their direction, preventing complete cancellation.
    paths = primary[primary.method.isin(METHODS)].pivot(
        index=["origin", "backtest_origin", "horizon"], columns="method", values="error_gap")[METHODS]
    assert np.isfinite(paths).all().all()
    shared = (paths.min(axis=1) > 0) | (paths.max(axis=1) < 0)
    narrower_count = int(narrower.sum().sum())
    lines = [
        "For each primary origin, method and horizon we measured the raw historical gap-error "
        "width from the 10th to the 90th percentile using linear interpolation. The first table "
        "gives the mean width in beds across origins, with each origin weighted equally.",
        table_width,
        f"The second table counts how often Combo's width was smaller than each method's at that "
        f"horizon, out of {len(p1)} origins. Each comparison uses the same historical backtest paths.",
        table_counts,
        f"Averaging produced a narrower central range in {narrower_count} of {narrower.size} "
        f"paired comparisons. " + (f"Seasonal naive already had a narrower range at every primary origin "
        f"for horizons {horizons}. " if horizons else "") +
        f"All five errors had the same sign in {int(shared.sum())} of "
        f"{len(shared)} historical origin, path and horizon records. The same past path can "
        "appear in several banks. Shared errors survive averaging, and a method with tight "
        "errors on its own can have a narrower range than the combination.",
    ]
    return "\n\n".join(lines)


def interpretation():
    p1 = json.loads((OUT / "config.json").read_text(encoding="utf-8"))["populations"]["P1"]
    totals = read("costs.csv")
    costs = totals[(totals.origin.isin(p1)) & (totals.penalty == BASE) &
                   (totals.plan == "Protected")].pivot(index="origin", columns="method", values="excess")
    means = costs[["Combo", *METHODS]].mean()
    individual = means[METHODS].idxmin()
    cost_diff = means.Combo - means[individual]
    lower = "higher" if cost_diff > 0 else "lower"
    # Explain an observed counterexample to perfect error cancellation.
    individual_gaps, combo = individual_and_combo()
    individual_gaps = individual_gaps.loc[p1]
    actual = read("actuals.csv").set_index(individual_gaps.index.names).actual_gap
    errors = individual_gaps.rsub(actual.loc[individual_gaps.index], axis=0).groupby(level="origin").mean()
    avg = (actual - combo.forecast_gap).groupby(level="origin").mean().loc[p1]
    worst = avg.abs().idxmax()
    direction = "low" if avg.loc[worst] > 0 else "high"
    agreeing = int((np.sign(errors.loc[worst]) == np.sign(avg.loc[worst])).sum())
    number = "all five methods" if agreeing == 5 else f"{agreeing} of the five methods"
    return (f"At the {pd.Timestamp(worst).strftime('%B %Y')} origin, {number} forecast {direction} on average. "
            f"Even after averaging, the forecast missed by {abs(avg.loc[worst]):,.1f} beds in "
            f"that direction, its largest absolute mean miss in the primary population. "
            f"Across the primary origins, Combo's mean Protected excess cost was "
            f"{abs(cost_diff) / 1e6:.3f}m {lower} than {individual}'s, the cheapest individual "
            "Protected method. The schedules establish the cost difference. A plausible explanation "
            "is that opposing errors can cancel, while a shared miss can leave beds unused when "
            "all forecasts run high or create a shortage when all run low. Confidence in that mechanism's size in another year "
            "is limited because 23 overlapping origins represent about four independent windows.")


CHECKS = [
    ("Forecast lies inside the individual range", convex_range),
    ("Signed-error identity", signed_errors),
    ("Combo LP versus Combo Rule", rule_vs_lp),
    ("Monthly hand calculation", hand_check),
    ("Exact reproduction of individual costs", exact_comparators),
    ("Concentration by origin", concentration),
    ("Historical error-bank spread by horizon", bank_widths),
    ("Does this make sense?", interpretation),
]


def main():
    phase_two = (OUT / "verification.md").read_text(encoding="utf-8")
    if "All Phase 2 checks passed." not in phase_two or "**FAIL**" in phase_two:
        raise RuntimeError("Part B requires completed Phase 2 verification.")
    path = OUT / "logic_review.md"
    original = path.read_bytes()
    prefix, _, previous = original.partition(b"\n" + HEADING.encode("utf-8"))
    if b"**FAIL**" in previous:
        raise RuntimeError("An existing Part B check failed. Review the recorded failure before proceeding.")
    if not prefix.endswith(b"\n") or b"## Part A" not in prefix:
        raise RuntimeError("Part A must already be present and end with a newline.")
    config = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    if hashlib.sha256(prefix).hexdigest() != config["part_a_sha256"]:
        raise RuntimeError("The design review differs from the registered Part A.")
    blocks = [HEADING]
    for i, (name, check) in enumerate(CHECKS, 1):
        try:
            detail = check()
        except (AssertionError, KeyError, ValueError, IndexError, OSError) as exc:
            blocks.append(f"### B{i}. {name}\n\n**FAIL**. {type(exc).__name__}: {exc}")
            for j, (pending, _) in enumerate(CHECKS[i:], i + 1):
                blocks.append(f"### B{j}. {pending}\n\n**NOT RUN** after the B{i} failure.")
            path.write_bytes(prefix + b"\n" + ("\n\n".join(blocks) + "\n").encode("utf-8"))
            raise
        label = "**PASS**" if i <= 5 else "**Result**"
        blocks.append(f"### B{i}. {name}\n\n{label}. {detail}")
    blocks.append("All Part B checks passed.")
    path.write_bytes(prefix + b"\n" + ("\n\n".join(blocks) + "\n").encode("utf-8"))
    print("All Part B checks passed.")


if __name__ == "__main__":
    main()
