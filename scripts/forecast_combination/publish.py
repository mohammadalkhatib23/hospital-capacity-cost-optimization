"""Publish the registered study from saved scores and the completed logic review."""
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/forecast_combination"
HEADING = "## Averaging the forecasts"
ENDING = ("The idea was formed at Sep 2025 and Mar 2026, which are excluded from the main test. "
          "Neighbouring origins share up to five target months, so origins are not independent; "
          "costs are illustrative model units (validated: False).")


def read(name):
    return pd.read_csv(OUT / f"{name}.csv", float_precision="round_trip")


def million(units):
    return f"{units / 1e6:,.2f}m"


def month(origin):
    return pd.Timestamp(origin).strftime("%b %Y")


def figure(stem, alt):
    prefix = "figures/forecast_combination/" + stem
    return f"![{alt}]({prefix}.png)\n\n[Vector figure]({prefix}.svg)"


def table(header, rows):
    return "\n".join("| " + " | ".join(map(str, row)) + " |" for row in [header, ["---"]*len(header), *rows])


def primary_table(lp, protected, centring, methods):
    rows = []
    for method in methods:
        p = protected.loc[method]
        counts = " / ".join(str(int(p[c])) for c in ["combo_wins", "combo_ties", "combo_losses"]) if method != "Combo" else "—"
        rows.append([method, million(lp.loc[method, "mean_excess"]), million(p.mean_excess),
                     f"{million(lp.loc[method, 'worst_excess'])} / {million(p.worst_excess)}",
                     f"{centring.loc[method, 'mean_abs_signed']:,.1f}",
                     f"{centring.loc[method, 'mean_signed']:+,.1f} / {centring.loc[method, 'sd_signed']:,.1f}", counts])
    return table(["Method", "Mean LP excess", "Mean Protected excess", "Worst excess (LP / Protected)",
                  "Mean absolute bias (beds)", "Mean signed bias / SD (beds)", "Combo Protected wins / ties / losses"], rows)


def publish(text):
    assert text.startswith(HEADING + "\n") and text.rstrip().endswith(ENDING)
    assert len(re.findall(r"!\[", text)) == 4
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if not link.startswith("https://"):
            assert (ROOT / link).is_file(), link
    readme = ROOT / "README.md"
    base = readme.read_text(encoding="utf-8").partition(HEADING)[0].rstrip()
    assert "## All monthly origins: April 2025 to April 2026" in base
    readme.write_bytes((base + "\n\n" + text.rstrip() + "\n").encode("utf-8"))


def build():
    config = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    pop = config["populations"]
    methods = ["Combo", *config["methods"]]
    config_hash = hashlib.sha256((OUT / "config.json").read_bytes()).hexdigest()
    assert config_hash == (OUT / "config.sha256").read_text().split()[0]
    verify = (OUT / "verification.md").read_text(encoding="utf-8")
    review = (OUT / "logic_review.md").read_text(encoding="utf-8")
    assert "All Phase 2 checks passed." in verify and "All Part B checks passed." in review
    assert hashlib.sha256(review.split("\n## Part B", 1)[0].encode()).hexdigest() == config["part_a_sha256"]
    results = read("endpoints")
    p1 = results[(results.population == "P1") & (results.penalty == 5000)]
    lp, safe = (p1[p1.plan == k].set_index("method") for k in ("LP", "Protected"))
    p3 = results.query("population == 'P3' and plan == 'Protected' and penalty == 5000").set_index("method")
    centre = read("centring")
    c = centre[centre.population == "P1"].set_index("method")
    c3 = centre[centre.population == "P3"].set_index("method")
    q = read("coverage").query("population == 'P1'")
    overall = q[q.horizon == 0].set_index("method")
    horizons = q[(q.horizon > 0) & (q.method == "Combo")].sort_values("horizon")
    h = read("hypotheses").set_index("population")
    chain = read("decomposition_summary").set_index("population")
    pattern = read("direction_by_origin").set_index("origin")
    primary = pattern.loc[pop["P1"]]
    high = primary[primary.components_high == 5]
    low = primary[primary.components_low == 5]
    july, december, february = (primary.loc[o] for o in ["2025-07", "2025-12", "2025-02"])
    assert july.combo_signed < 0 and december.components_low == february.components_high == 5
    assert high.protection_saving.lt(0).all() and low.protection_saving.gt(0).all()
    assert h.loc["P1", ["H1","H2","H3","clear_winner"]].tolist() == [True,False,False,True]
    assert h.loc["P3", ["H1","H2","H3","clear_winner"]].tolist() == [False,False,True,False]
    seasonal = "Seasonal naive"
    assert lp.mean_excess.idxmin() == safe.worst_excess.idxmin() == c.mean_abs_signed.idxmin() == seasonal
    assert safe.loc[config["methods"]].mean_excess.idxmin() == seasonal
    assert safe.p90_excess.idxmin() == overall.mean_margin.idxmin() == "Combo"
    assert overall.loc["Combo", "coverage"] > .9
    assert (horizons.iloc[1:].covered == horizons.iloc[1:].months).all()
    assert p3.mean_excess.idxmin() == p3.worst_excess.idxmin() == seasonal
    sens = results[(results.population == "P1") & (results.plan == "Protected")]
    low_p = sens[sens.penalty == 2500].set_index("method")
    high_p = sens[sens.penalty == 10000].set_index("method")
    assert low_p.mean_excess.idxmin() == seasonal and high_p.mean_excess.idxmin() == "Combo"
    costs = read("costs")
    mean_plans = costs[costs.method.isin(["Combo", "Combo (4 methods)"]) & costs.penalty.eq(5000)]
    own = mean_plans.pivot(index="origin", columns="plan", values="total")
    saved = int(((own.Rule - own.LP) > 1e-6 * own.Rule).sum())
    tied = int(((own.Rule - own.LP).abs() <= 1e-6 * own.Rule).sum())
    assert saved + tied == len(own)
    point, protected, sn = lp.loc["Combo"], safe.loc["Combo"], safe.loc[seasonal]
    d = chain.loc["P1"]
    n, n3 = len(primary), len(pop["P3"])
    boundaries = read("banks")
    widths = boundaries[boundaries.origin.isin(pop["P1"])].groupby(["origin","horizon","method"]).error_gap.quantile([.1,.9]).unstack()
    widths = (widths[.9]-widths[.1]).unstack("method")
    narrower = widths[config["methods"]].gt(widths.Combo, axis=0)
    assert len(widths) == n * 6

    paragraphs = [
        HEADING,
        "Every individual method had windows when it forecast too high and others when it forecast too low. "
        "An equal-weight average could cancel opposing errors before the planner decides how many beds to open. "
        "September 2025 and March 2026 suggested the idea, so we excluded them from the primary test.",
        "**Method.** Combo averages the five occupied forecasts and the five available forecasts separately, "
        "then calculates the gap. To protect Combo, we average the five errors from the same historical "
        "backtest and forecast month, preserving their six-month paths. Every method faces the same prices, "
        f"capacity limits and number of historical scenarios. The primary test (P1) has {n} origins. P2 includes "
        f"all {len(pop['P2'])} rolling origins. P3 compares the {n3} complete origins in the "
        f"{len(pop['P3_all'])}-origin monthly study. The two four-method averages are shown separately.",
        f"Registered config SHA-256: `{config_hash}`. [Design and origin lists](outputs/forecast_combination/config.json)",
        "### Did the combination win?",
        f"**On average, yes in the primary test.** Combo Protected cost {million(protected.mean_excess)} above "
        f"hindsight per six-month plan, compared with {million(sn.mean_excess)} for Seasonal naive, the cheapest "
        f"individual Protected method. Combo had the lowest mean in {int(h.loc['P1','lowest_subsets'])} of the six "
        "every-sixth-origin subsets and beat every individual method more often than it lost. That meets the "
        "registered clear-winner rule. The overlapping primary origins represent about four independent windows.",
        primary_table(lp, safe, c, methods),
        f"Costs are millions above what a planner with perfect foresight would have spent. A win or loss requires a "
        "difference greater than 1% of that origin's hindsight cost. Bias is the average actual gap minus forecast "
        "gap within a window. The absolute column measures the usual size of that miss. SD shows how much the signed "
        "miss varied between windows.",
        f"**The other hypotheses had different answers.** In P1, lowest mean Protected cost (H1) held. Lowest worst "
        "Protected cost (H2) and smallest mean absolute bias (H3) both went to Seasonal naive. "
        f"In P3 only H3 held for Combo ({c3.loc['Combo','mean_abs_signed']:,.0f} beds). Seasonal naive had lower mean "
        f"Protected excess ({million(p3.loc[seasonal,'mean_excess'])} vs {million(p3.loc['Combo','mean_excess'])}) "
        f"and narrowly lower worst excess ({p3.loc[seasonal,'worst_excess']/1e6:.4f}m vs "
        f"{p3.loc['Combo','worst_excess']/1e6:.4f}m). This covers about two to three independent windows.",
        figure("excess_by_origin", "Protected excess cost by origin, with the two idea-forming origins marked"),
        f"Combo Protected beat Seasonal naive Protected at {int(sn.combo_wins)} primary origins, tied at "
        f"{int(sn.combo_ties)} and cost more at {int(sn.combo_losses)}. When compared with the cheapest individual "
        f"Protected plan chosen *after* each window, Combo cost materially more at "
        f"{int(primary.lost_to_best_outside_band.sum())} of {n}. That individual winner changed with the window.",
        figure("signed_error_by_origin", "Six-month mean gap error for individual and averaged forecasts"),
        f"In July 2025, individual methods missed by {july.mean_component_abs:,.0f} beds on average in absolute "
        f"terms, yet their average forecast was only {abs(july.combo_signed):,.0f} beds high. In December 2025 all "
        f"five forecast too low, leaving Combo {december.combo_signed:,.0f} beds short in its forecast. "
        f"Protection then saved Combo {million(december.protection_saving)}, although Protected ExtraTrees-24 still "
        f"cost {million(december.combo_minus_best_protected)} less.",
        f"In the primary test, all five forecast high at {len(high)} origins. Protection added cost at every one. "
        f"Their only unanimous under-forecast was December 2025. Across all {n} origins, Combo's signed miss averaged "
        f"{abs(c.loc['Combo','mean_signed']):,.0f} beds high, but the average *size* of its miss was "
        f"{c.loc['Combo','mean_abs_signed']:,.0f} beds. Those two numbers answer different questions.",
        figure("worst_case", "90th-percentile and worst-origin LP and Protected excess cost in the primary test"),
        f"Combo had the lowest Protected 90th-percentile excess ({million(protected.p90_excess)}). Its worst "
        f"Protected window cost {million(protected.worst_excess)} above hindsight in {month(protected.worst_origin)}, "
        f"while Seasonal naive's worst was {million(sn.worst_excess)}. Combo kept most of the expensive outcomes "
        "smaller, but Seasonal naive had the smaller single worst loss.",
        figure("summary_heatmap", "Protected excess at all monthly origins including the four-method fallback"),
        f"Seasonal naive had a {million(p3.loc['Combo','mean_excess'] - p3.loc[seasonal,'mean_excess'])} mean "
        f"advantage over Combo on the {n3} comparable monthly origins. Where SARIMA was unavailable, four-method "
        f"Combo Protected excess was {million(pattern.loc['2025-10','protected_excess'])} in October 2025 and "
        f"{million(pattern.loc['2026-01','protected_excess'])} in January 2026. Those cells sit outside the rankings.",
        "### What did averaging and protection each contribute?",
        f"Start with Seasonal naive LP at {million(d.sn_lp_excess)} above hindsight per primary origin. "
        f"Switching to Combo LP **added {million(-d.averaging_saving)}**, taking the excess to "
        f"{million(point.mean_excess)}. Protecting that same Combo forecast **saved {million(d.protection_saving)}**, "
        f"bringing it to {million(d.combo_protected_excess)}. Together the two steps saved {million(d.total_saving)} "
        f"against Seasonal naive LP and {million(d.saving_vs_protected_sn)} against Seasonal naive Protected. "
        f"The secondary median forecast had mean LP excess of {million(lp.loc['Combo-median','mean_excess'])}.",
        f"**Was the protection margin better sized?** Combo's average margin at the 90% quantile was "
        f"{overall.loc['Combo','mean_margin']:,.0f} beds, smaller than any individual's. Actual demand gaps fell "
        f"below or at its upper forecast in {int(overall.loc['Combo','covered'])} of {int(overall.loc['Combo','months'])} "
        f"origin-month pairs ({overall.loc['Combo','coverage']:.1%}), above the 90% target. At horizon one it covered "
        f"{int(horizons.iloc[0].covered)} of {n} and at each of horizons two through six it covered all {n}. "
        f"Individual coverage ranged from {overall.loc[config['methods'],'coverage'].min():.1%} to "
        f"{overall.loc[config['methods'],'coverage'].max():.0%}. A smaller margin still left Combo conservative here.",
        "### What should a planner use?",
        f"At the assumed shortage price of 5,000, Combo Protected has the strongest case for minimising average cost "
        f"on the primary origins. A planner prioritising the worst bill has evidence for Seasonal naive Protected. "
        f"Price matters too. At 2,500 Seasonal naive had the lowest mean Protected excess "
        f"({million(low_p.loc[seasonal,'mean_excess'])} vs Combo's {million(low_p.loc['Combo','mean_excess'])}). "
        f"At 10,000 Combo ranked first ({million(high_p.loc['Combo','mean_excess'])}), an indicative sensitivity "
        "result. With about four independent primary windows, confidence in carrying that choice into another year is limited.",
        "### How we checked this",
        "Before scoring, we checked that averaging errors from matched backtests gives exactly the error of the "
        "average forecast, using the same information and number of scenarios as each rival. We froze the plans, "
        "matched the individual methods' costs exactly to both earlier studies, and reproduced one month's "
        f"shortfall and bill by hand. Combo LP saved money against the Rule at {saved} origins and tied at {tied}. "
        f"Combo's historical error range narrowed in {int(narrower.sum().sum())} of {narrower.size} comparisons, "
        f"yet in February 2025 all five methods forecast high and Combo still missed high by "
        f"{abs(february.combo_signed):,.0f} beds. Removing any two primary origins left its average Protected "
        "advantage over each individual intact. [Logic and sanity review](outputs/forecast_combination/logic_review.md) · "
        "[Verification](outputs/forecast_combination/verification.md)",
        f"Putting the two idea-forming origins back in (P2) gives a mean Protected excess of "
        f"{million(chain.loc['P2','combo_protected_excess'])} for Combo and "
        f"{million(chain.loc['P2','sn_protected_excess'])} for Seasonal naive. [All endpoints](outputs/forecast_combination/endpoints.csv) · "
        "[Coverage](outputs/forecast_combination/coverage.csv) · "
        "[Cost decomposition](outputs/forecast_combination/decomposition_summary.csv)",
        "For the forecasting rationale behind equal-weight combinations, see "
        "[Bates and Granger](https://doi.org/10.1057/jors.1969.103), "
        "[Clemen](https://www.sciencedirect.com/science/article/pii/0169207089900125), "
        "[Forecasting: Principles and Practice](https://otexts.com/fpp3/practical.html#forecast-combinations) and "
        "[the M4 competition](https://doi.org/10.1016/j.ijforecast.2018.06.001).",
        ENDING,
    ]
    return "\n\n".join(paragraphs)


if __name__ == "__main__":
    publish(build())
