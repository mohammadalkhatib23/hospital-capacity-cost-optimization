"""Publish the monthly comparison from verified results; preserve the earlier case study."""
import json
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/monthly_origins"
FIG = "figures/monthly_origins"
HEADING = "## All monthly origins: April 2025 to April 2026"
ENDING = ("Neighbouring origins share up to five target months, so these 13 origins are about two "
          "to three independent windows; costs are illustrative model units (validated: False).")
METHODS = ["Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24"]


def millions(value):
    return f"{value / 1e6:.2f}m"


def date_label(origin, full=False):
    return pd.Timestamp(origin).strftime("%B %Y" if full else "%b %Y")


def figure(stem, alt):
    return f"![{alt}]({FIG}/{stem}.png)\n\n[Vector figure]({FIG}/{stem}.svg)"


def table(head, rows):
    return "\n".join("| " + " | ".join(map(str, line)) + " |"
                     for line in [head, ["---"] * len(head), *rows])


def comparison_table(winners):
    rows = []
    for r in winners.itertuples():
        if r.common:
            best = f"{r.best_method} ({r.best_plan})"
            rows.append([date_label(r.origin), f"{r.lp_method} · {millions(r.lp_cost)}",
                         f"{best} · {millions(r.best_cost)}", millions(r.best_excess)])
        else:
            rows.append([date_label(r.origin), "Unranked", "SARIMA-24 unavailable", "—"])
    return table(["Origin", "Lowest LP cost", "Lowest cost across all plans", "Winner above hindsight"], rows)


def method_table(summary):
    common_count = summary["common_origins"]
    rows = []
    for method in METHODS:
        s = summary["per_method"][method]
        direction = s["signed_error_counts"]
        effects = s["optimisation_counts"]
        rows.append([method, f'{direction["low"]} / {direction["high"]}',
                     f'{effects["helped"]} / {effects["tied"]}', s["lp_wins_common"],
                     f'{s["lp_rank_min_common"]}–{s["lp_rank_max_common"]}',
                     millions(s["lp_mean_excess_common"])])
    return table(["Method", "Forecast low / high", "LP saved / tied vs Rule",
                  f"LP wins ({common_count} origins)", "LP rank range", "Mean LP excess"], rows)


def effect_table(association):
    rows = []
    for direction in ["low", "high"]:
        counts = association["protection_sign"][direction]
        rows.append([f"Forecast too {direction}", counts["helped"], counts["hurt"]])
    return table(["Six-month mean error", "Protection reduced cost", "Protection added cost"], rows)


def gallery(winners, monthly, cases):
    blocks = ["<details>", "<summary>Per-origin heatmaps</summary>",
              "Each row follows one frozen six-month schedule. The first month includes the cost of opening beds. "
              "A monthly cell gives the cost and the beds short or spare. The Total column uses its own colour scale "
              "and sums bed-months, showing shortfall when it rounds to at least one bed-month and spare capacity otherwise."]
    for r in winners.itertuples():
        title = date_label(r.origin, full=True)
        if r.common:
            rows = monthly[(monthly.origin == r.origin) & (monthly.method == r.best_method)]
            point = rows[rows.plan == "LP"].set_index("month")
            if r.best_plan == "Protected":
                safe = rows[rows.plan == "Protected"].set_index("month")
                when = (point.shortfall - safe.shortfall).idxmax()
                caption = (f"For {r.best_method}, protection made its largest reduction in shortfall in "
                           f"{date_label(when)}, from {point.loc[when, 'shortfall']:,.0f} beds with LP to "
                           f"{safe.loc[when, 'shortfall']:,.0f} beds.")
            else:
                rule = rows[rows.plan == "Rule"].set_index("month")
                when = (point.x - rule.x).idxmax()
                gap = point.loc[when, "x"] - rule.loc[when, "x"]
                if gap >= 1:
                    caption = (f"{r.best_method} kept {point.loc[when, 'x']:,.0f} beds open in {date_label(when)} "
                               f"with LP versus {rule.loc[when, 'x']:,.0f} with the Rule, saving "
                               f"{millions(rule.monthly_cost.sum() - point.monthly_cost.sum())} over the full window.")
                else:
                    peak = point.monthly_cost.idxmax()
                    caption = (f"For {r.best_method} with LP, the largest monthly bill was "
                               f"{millions(point.loc[peak, 'monthly_cost'])} in {date_label(peak)}.")
        else:
            # A named method can still be compared with itself at a partial origin.
            season = cases[(cases.origin == r.origin) & (cases.method == "Seasonal naive")].iloc[0]
            effect = "saved" if season.value_protection >= 0 else "added"
            caption = (f"Seasonal naive cost {millions(season.LP)} with LP, and protection {effect} "
                       f"{millions(abs(season.value_protection))} over the six months.")
        blocks += [f"**{title}**", figure(f"origin_{r.origin}_heatmap", f"Monthly costs and beds short or spare for {title}"), caption]
    return "\n\n".join([*blocks, "</details>"])


def build_section():
    summary = json.loads((OUT / "interpretation.json").read_text(encoding="utf-8"))
    read = lambda name: pd.read_csv(OUT / name, float_precision="round_trip")
    cases, winners, monthly, forecasts = map(
        read, ["method_origin.csv", "winners.csv", "monthly_costs.csv", "forecasts.csv"]
    )
    common = cases[cases.common]
    n, ranked, pairs = summary["origins"], summary["common_origins"], len(cases)
    methods = summary["per_method"]
    season = summary["season_comparisons_common"]["origin_season"]
    summer, winter = season["Apr-Sep"], season["Oct-Mar"]
    peak = summary["season_comparisons_common"]["contains_winter_peak"]
    with_peak, without_peak = peak["True"], peak["False"]
    peak_name = date_label(summary["winter_peak_month"], full=True)
    dip = summary["optimisation_by_dip"]
    saved, tied = dip["True"]["helped"], dip["False"]["tied"]
    assert saved + tied == pairs
    assoc = summary["protection_vs_error"]["available"]
    counts = assoc["protection_sign"]
    assert counts == assoc["protection_effect"] and sum(v["tied"] for v in counts.values()) == 0
    low, high = counts["low"], counts["high"]
    common_winners = winners[winners.common]
    plan_wins = common_winners.best_plan.value_counts()
    assert plan_wins.sum() == ranked and set(plan_wins.index) == {"LP", "Protected"}
    by_origin = cases.set_index(["origin", "method"])
    seasonal = cases[cases.method == "Seasonal naive"]
    sept, march = (by_origin.loc[o, "Seasonal naive"] for o in ["2025-09", "2026-03"])
    assert sept.value_optimisation == seasonal.value_optimisation.median()
    assert march.value_optimisation == seasonal.value_optimisation.max()
    assert march.value_protection == seasonal.value_protection.max()
    sarima = cases[cases.method == "SARIMA-24"].set_index("origin")
    assert sarima.value_protection.rank().loc["2025-09"] == 2
    exception = by_origin.loc["2025-07", "Mean-24"]
    january = forecasts[(forecasts.origin == "2025-07") & (forecasts.method == "Mean-24") &
                        (forecasts.month == summary["winter_peak_month"])].iloc[0]
    assert exception.signed_error < 0 < exception.value_protection and january.actual_gap > january.forecast_gap
    assert all(min(s["signed_error_counts"]["low"], s["signed_error_counts"]["high"]) > 0 for s in methods.values())
    assert all(s["lp_wins_common"] > 0 for s in methods.values())
    assert methods["SARIMA-24"]["lp_last_common"] == 0

    blocks = [
        HEADING,
        "September favoured SARIMA-24 with LP. March favoured Seasonal naive with protection. "
        "We repeated the same six-month planning exercise at every monthly origin from April 2025 to "
        "April 2026 to see which findings repeated.",
        "**How to read the figures.** Each summary heatmap column is the month the plan was made. "
        "Hindsight is the cheapest feasible plan if all six future gaps are known. Subtracting its cost "
        "shows how much extra each plan cost in its own window, so an expensive winter does not "
        "automatically make its forecasts look worse. Savings compare two plans for the same forecast.",
        f"**Which origins count?** Ranks and cross-method averages use the {ranked} origins with all five methods. "
        "SARIMA-24 failed to converge for occupied beds in October 2025 and for available beds in January 2026. "
        "The other four methods still have results in those columns. Neighbouring plans share five target "
        "months, so the counts describe about two to three independent six-month windows.",
        "### Which forecasts missed, and which plans were cheapest?",
        figure("summary_signed_gap_error", "B1. Average forecast error in beds by method and origin"),
        "**B1. Forecast direction.** Positive values mean the forecast gap was too low on average over six months. "
        "Every method switched direction at least once. For example, Seasonal naive went from "
        f"{abs(sept.signed_error):,.0f} beds too high in September to {march.signed_error:,.0f} too low in March.",
        figure("summary_lp_excess", "B2. Six-month LP cost above hindsight in millions"),
        f"**B2. Cost of the optimised forecast.** All five methods had the cheapest LP at least once across "
        f"the {ranked} common origins. Seasonal naive and SARIMA-24 had nearly identical mean costs above "
        f"hindsight ({millions(methods['Seasonal naive']['lp_mean_excess_common'])} and "
        f"{millions(methods['SARIMA-24']['lp_mean_excess_common'])}). SARIMA-24 won most often and never "
        f"finished last, while Persistence won in March and finished last at {methods['Persistence']['lp_last_common']} origins.",
        method_table(summary),
        f"The first two count columns use each method's available origins ({n} for four methods and "
        f"{ranked} for SARIMA-24). LP wins, ranks and mean excess compare all five on the same {ranked} origins. "
        "A win is the lowest realised cost before rounding.",
        "**Does the season pick the winner?** Among the "
        f"{summer['origin_count']} common April–September starts, SARIMA-24 won {summer['wins']['SARIMA-24']} "
        f"times, ExtraTrees-24 {summer['wins']['ExtraTrees-24']} times and Seasonal naive once. Among the "
        f"{winter['origin_count']} common October–March starts, Seasonal naive won twice, and Mean-24 and "
        "Persistence once each.",
        f"The target months explain more of the winter story. In all {with_peak['origin_count']} common windows "
        f"that included the {peak_name} peak, Seasonal naive ({with_peak['wins']['Seasonal naive']} LP wins) or "
        f"SARIMA-24 ({with_peak['wins']['SARIMA-24']}) was cheapest. Together they won "
        f"{without_peak['wins']['Seasonal naive'] + without_peak['wins']['SARIMA-24']} of the other "
        f"{without_peak['origin_count']}. Those winter plans share the same peak, so this is weak evidence "
        "for choosing a method by season in another year.",
        "### What did optimisation and protection buy?",
        figure("summary_value_of_optimisation", "B3. Saving from optimising each forecast instead of following the Rule"),
        f"**B3. Optimisation.** LP saved money in all {saved} forecasts that fell and then rose within the six "
        f"months, and tied the Rule in the other {tied} at the registered tolerance. Keeping a bed open through "
        "a one-month dip avoids paying to close and reopen it, and sometimes covers a shortage as well. "
        "Mean-24 and Persistence forecast the same gap every month, so the Rule already holds the capacity "
        "steady and there is no dip to bridge.",
        figure("summary_value_of_protection", "B4. Saving or extra cost from protecting each optimised forecast"),
        f"**B4. Protection.** Across {len(common)} method–origin pairs at the {ranked} common origins, "
        f"optimisation saved {millions(common.value_optimisation.mean())} on average and protection saved a further "
        f"{millions(common.value_protection.mean())}. Protection cut the bill in "
        f"{int((common.value_protection > 0).sum())} of those pairs and raised it in "
        f"{int((common.value_protection < 0).sum())}. Avoided shortage charges outweighed the extra cost of running the protected schedules.",
        figure("summary_best_plan", "B5. Cheapest forecast-based plan and its excess cost above hindsight"),
        f"**B5. The cheapest plan after costs were known.** An ordinary LP won at {plan_wins['LP']} of the "
        f"{ranked} common origins and a Protected plan won at {plan_wins['Protected']}. "
        "In November and December, Seasonal naive had the cheapest ordinary LP, but ExtraTrees-24 became "
        "cheaper once its plan included protection.",
        comparison_table(winners),
        "Reading across a row shows whether protection changed the winner. In March it did, even though "
        "optimisation had already saved Seasonal naive money compared with following its forecast.",
        figure("protection_vs_error_direction", "C. Value of protection versus the average signed forecast error"),
        f"**C. When protection paid.** Each point pairs one forecast with the saving from protecting it. "
        f"The Spearman rank correlation of {assoc['spearman_rho']:.3f} across the {pairs} available pairs means "
        "larger under-forecasts tended to go with larger savings. The same calculation using just the "
        f"{len(common)} common-origin pairs gave {summary['protection_vs_error']['common']['spearman_rho']:.3f}.",
        effect_table(assoc),
        f"Protection helped in {low['helped']} of {low['helped'] + low['hurt']} under-forecasts and in "
        f"{high['helped']} of {high['helped'] + high['hurt']} over-forecasts. A high six-month average can hide "
        f"a costly monthly shortage. At the July 2025 origin, Mean-24 averaged {abs(exception.signed_error):,.0f} "
        f"beds too high but forecast {january.forecast_gap:,.0f} beds for January against an actual gap of "
        f"{january.actual_gap:,.0f}. Protecting it saved {millions(exception.value_protection)}.",
        "### Where September and March sit",
        f"September's {millions(sept.value_optimisation)} optimisation saving for Seasonal naive was exactly "
        f"its median over {len(seasonal)} origins. Protection raised SARIMA-24's September cost by "
        f"{millions(-sarima.loc['2025-09', 'value_protection'])}, its second-largest increase in "
        f"{len(sarima)} origins. March was Seasonal naive's strongest window for both steps, with "
        f"{millions(march.value_optimisation)} saved by LP and another {millions(march.value_protection)} by "
        "protection. The two case studies illustrate a mechanism, but March gives an unusually large example.",
        "### What generalises",
        f"- **Holds across these windows.** Optimising the same forecast never materially increased realised "
        f"cost across the {pairs} comparisons. The shape of the forecast explained where optimisation saved money.\n"
        "- **Depends on the window.** The cheapest forecasting method changed. Protection usually paid when "
        "the forecast was low, but the timing and size of monthly shortages also mattered.",
        "### A planner's rule?",
        "Under these cost assumptions, optimise the forecast when freezing a plan. A planner can see a dip "
        "before acting. The direction of the future forecast error only becomes known later, so these windows "
        "support using past errors to assess the cost of protection but do not establish a reliable trigger "
        "for switching it on. The verified schedules establish what happened in these windows. Their overlap "
        "limits confidence in applying the seasonal pattern to another winter.",
        "The underlying results include the forecasts, the monthly accounts for each plan, the common-origin "
        "rankings and the verification checks. [Forecasts](outputs/monthly_origins/forecasts.csv) · "
        "[Monthly costs](outputs/monthly_origins/monthly_costs.csv) · [Rankings](outputs/monthly_origins/winners.csv) · "
        "[Interpretation data](outputs/monthly_origins/interpretation.json) · [Verification](outputs/monthly_origins/verification.md)",
        gallery(winners, monthly, cases), ENDING,
    ]
    return "\n\n".join(blocks) + "\n"


def main():
    verification = (OUT / "verification.md").read_text(encoding="utf-8")
    if "All Phase 1 checks passed." not in verification or "**FAIL**" in verification:
        raise ValueError("Publication requires completed Phase 1 verification.")
    section = build_section()
    assert section.endswith(ENDING + "\n")
    images = re.findall(r"!\[[^\]]+\]\(([^)]+)\)", section)
    assert len(set(images)) == len(images) == 19
    for path in re.findall(r"\]\(([^)]+)\)", section):
        assert (ROOT / path).is_file(), path

    path = ROOT / "README.md"
    before = path.read_text(encoding="utf-8").split(HEADING, 1)[0].rstrip()
    before = before.replace("Two frozen origins\n", "Two frozen origins and a monthly comparison\n", 1)
    before = before.replace("All forecasts, plans and total scores below were already saved; nothing was refitted, re-solved or rescored.",
                            "The September and March case studies use previously saved forecasts, plans and scores.")
    before = before.replace("- These are two six-month windows, so the results describe these cases.",
                            "- The two detailed case studies each cover six months. The monthly extension below uses overlapping windows.")
    before = re.sub(
        r"^Refresh the monthly figures and this README with .*?$",
        "The original two-origin figure builder is [monthly_views.py](scripts/case_study/monthly_views.py). "
        "For the monthly extension, run `python -B scripts/monthly_origins/interpret.py`, then "
        "`python -B scripts/monthly_origins/figures.py` and `python -B scripts/monthly_origins/report.py` "
        "from the repository root using the verified outputs.",
        before, flags=re.MULTILINE,
    )
    path.write_bytes((before + "\n\n" + section).encode("utf-8"))


if __name__ == "__main__":
    main()
