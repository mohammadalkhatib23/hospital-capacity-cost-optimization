"""Build README.md from outputs/clean_checkpoint/. Every number is read from the saved results; nothing is recomputed.

Each statement made in the prose is asserted against the data before the file is written, so the script stops if a
sentence no longer matches the results. `--check` compares README.md with the generated text instead of writing it.
"""

import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "outputs/clean_checkpoint"
FIG = "figures/clean_checkpoint"
ORIGINS = {"O1_sep2025": ("o1", "Origin 1", "September 2025"), "O2_mar2026": ("o2", "Origin 2", "March 2026")}
METHODS = ("Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24")
PLANS = [f"{m} {d}" for m in METHODS for d in ("Rule", "LP")]
FLAT = ("Mean-24", "Persistence")
SERIES = (("occupied", "forecast_occupied", "Occupied beds"), ("available", "forecast_available", "Available beds"), ("gap", "forecast_gap", "Planning gap"))
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th", 9: "9th", 10: "10th"}


def read(path):
    return pd.read_csv(path, parse_dates=["month"], float_precision="round_trip") if "month" in pd.read_csv(path, nrows=0).columns \
        else pd.read_csv(path, float_precision="round_trip")


def clean(text):
    """No negative zero."""
    return text[1:] if text.startswith("-") and not any(c in "123456789" for c in text) else text


def money(v):
    return clean(f"{v / 1e6:.2f}")


def beds(v):
    return clean(f"{v:,.0f}")


def pct(v):
    return clean(f"{v:,.0f}") + "%"


def skill(v):
    return clean(f"{v:.2f}")


def shown_ranks(cost, scale):
    """Rank by cost. Plans whose costs agree to 1e-6 of `scale` (the same plan, solver rounding) share a rank, marked '='."""
    out = {}
    for p in cost.index:
        better = int((cost < cost[p] - 1e-6 * scale).sum())
        tied = int(((cost - cost[p]).abs() <= 1e-6 * scale).sum())
        out[p] = f"{better + 1}=" if tied > 1 else f"{better + 1}"
    return out


def load():
    d = dict(decision=json.loads((DATA / "decision.json").read_text(encoding="utf-8")), log=json.loads((DATA / "fit_log.json").read_text(encoding="utf-8")),
             config=json.loads((DATA / "config.json").read_text(encoding="utf-8")), combined=read(DATA / "combined.csv").set_index("plan"),
             imp=read(DATA / "improvements.csv").set_index(["scope", "plan"]), origins={})
    d["config_sha"] = (DATA / "config.sha256").read_text(encoding="utf-8").split()[0]
    d["plans_sha"] = (DATA / "plans.sha256").read_text(encoding="utf-8").split()[0]
    assert d["decision"]["hashes"] == dict(config=d["config_sha"], plans_frozen=d["plans_sha"])
    for key in ORIGINS:
        b = DATA / key
        d["origins"][key] = dict(fc=read(b / "forecasts.csv"), act=read(b / "actuals.csv"), totals=read(b / "totals.csv").set_index("plan"),
                                 acc=read(b / "accuracy.csv").set_index("method"))
    return d


def series_lines(o):
    """Per series: in how many months each of Seasonal naive and SARIMA-24 was closer, and the best MAE of all five methods."""
    out = {}
    for s, col, title in SERIES:
        err = {m: np.abs(o["act"][s].to_numpy() - o["fc"][o["fc"].method == m].sort_values("month")[col].to_numpy()) for m in ("Seasonal naive", "SARIMA-24")}
        sn, s24 = int((err["Seasonal naive"] < err["SARIMA-24"]).sum()), int((err["SARIMA-24"] < err["Seasonal naive"]).sum())
        assert sn + s24 == 6
        closer = ("Seasonal naive was closer in 6 of 6 months." if sn == 6 else "SARIMA-24 was closer in 6 of 6 months." if s24 == 6
                  else f"Seasonal naive was closer in {sn} of 6 months and SARIMA-24 in {s24}.")
        mae = o["acc"][f"{s}_mae"]
        out[s] = f"**{title}:** {closer} Best of all five methods: {mae.idxmin()} (MAE {beds(mae.min())} beds)."
    return out


def results_table(o):
    lines = ["| Forecast | Decision | Gap MAE (beds) | Mean signed gap error (beds) | Operating cost | Shortfall cost | Total | Excess over hindsight |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for plan in PLANS:
        t, method = o["totals"].loc[plan], plan.rsplit(" ", 1)[0]
        a = o["acc"].loc[method]
        lines.append(f"| {method} | {t.decision} | {beds(a.gap_mae)} | {beds(a.gap_mean_signed_error)} | {money(t.operating)} | {money(t.shortfall_cost)} | "
                     f"{money(t.total)} | {money(t.excess_over_hindsight)} |")
    t = o["totals"].loc["Hindsight"]
    lines.append(f"| Hindsight | LP | - | - | {money(t.operating)} | {money(t.shortfall_cost)} | {money(t.total)} | {money(0)} |")
    return "\n".join(lines)


def origin_section(d, key):
    stem, name, month = ORIGINS[key]
    o, dec = d["origins"][key], d["decision"]["per_origin"][key]
    s = series_lines(o)
    months = o["act"].month
    text = [f"## {name}: {month}", "",
            f"Forecast months: {months.iloc[0]:%B %Y} - {months.iloc[-1]:%B %Y}. The figures show Seasonal naive and SARIMA-24 against the actual values; "
            "all five methods are in the table."]
    for series, _, title in SERIES:
        text += ["", f"![{title}: forecast vs actual, {name}]({FIG}/{stem}_forecast_{series}.png)", "", s[series]]
    text += ["", results_table(o), "",
             "Costs in millions of model units. Mean signed gap error = actual - forecast: above zero means the forecast was too low.", "",
             f"![Realised cost by plan, {name}]({FIG}/{stem}_cost_bars.png)", "",
             f"![Monthly cost by plan, {name}]({FIG}/{stem}_cost_heatmap.png)", ""]
    return text, o, dec


def origin_1(d):
    text, o, dec = origin_section(d, "O1_sep2025")
    t, voo = o["totals"], dec["value_of_optimisation"]
    assert dec["cheapest"] == "SARIMA-24 LP" and dec["joint_best"] == ["Seasonal naive LP", "SARIMA-24 LP"] and dec["lowest_gap_mae"] == "Seasonal naive"
    assert all(abs(voo[m]) < 1 for m in FLAT) and all(voo[m] > 1000 for m in METHODS if m not in FLAT)
    text.append(f"**Result.** SARIMA-24 LP was the cheapest plan ({money(t.loc['SARIMA-24 LP', 'total'])}m, "
                f"{money(t.loc['SARIMA-24 LP', 'excess_over_hindsight'])}m above hindsight), and Seasonal naive LP ({money(t.loc['Seasonal naive LP', 'total'])}m) "
                f"was joint best with it. Seasonal naive had the most accurate gap forecast (MAE {beds(o['acc'].loc['Seasonal naive', 'gap_mae'])} beds). "
                f"The LP was cheaper than the Rule by {money(voo['Seasonal naive'])}m for Seasonal naive, {money(voo['SARIMA-24'])}m for SARIMA-24 and "
                f"{money(voo['ExtraTrees-24'])}m for ExtraTrees-24; for the two flat forecasts (Mean-24, Persistence) the Rule and the LP give the same plan.")
    return text


def origin_2(d):
    text, o, dec = origin_section(d, "O2_mar2026")
    t = o["totals"]
    low = {m: int((o["act"].gap.to_numpy() > o["fc"][o["fc"].method == m].sort_values("month").forecast_gap.to_numpy()).sum()) for m in ("Seasonal naive", "SARIMA-24")}
    assert dec["cheapest"] in ("Persistence LP", "Persistence Rule") and dec["joint_best"] == ["Persistence Rule", "Persistence LP"]
    assert abs(dec["value_of_optimisation"]["Persistence"]) < 1 and dec["lowest_gap_mae"] == "Mean-24"
    assert d["decision"]["per_origin"]["O1_sep2025"]["joint_best"] == ["Seasonal naive LP", "SARIMA-24 LP"]
    ranks = {p: int(t.loc[p, "rank"]) for p in ("Seasonal naive LP", "SARIMA-24 LP")}
    text.append(f"**Result.** Persistence was the cheapest plan ({money(t.loc['Persistence LP', 'total'])}m, "
                f"{money(t.loc['Persistence LP', 'excess_over_hindsight'])}m above hindsight; its Rule and LP plans are the same), and no other plan was "
                f"within the tie band. Mean-24 had the most accurate gap forecast (MAE {beds(o['acc'].loc['Mean-24', 'gap_mae'])} beds). "
                f"Seasonal naive LP and SARIMA-24 LP, joint best at Origin 1, ranked {ORDINAL[ranks['Seasonal naive LP']]} and {ORDINAL[ranks['SARIMA-24 LP']]}: "
                f"their gap forecasts were below the actual gap in {low['Seasonal naive']} and {low['SARIMA-24']} of 6 months.")
    return text


def combined_table(d):
    c, keys = d["combined"], list(ORIGINS)
    hind = {k: d["decision"]["per_origin"][k]["hindsight_cost"] for k in keys}
    shown = {k: shown_ranks(c[f"{k}_total"], hind[k]) for k in keys}
    overall = shown_ranks(c.combined_excess, sum(hind.values()))
    for k in keys:  # the displayed ranks agree with the saved ranks, apart from the shared rank of identical plans
        for p in c.index:
            first = int(shown[k][p].rstrip("="))
            assert first == c.loc[p, f"{k}_rank"] or (shown[k][p].endswith("=") and c.loc[p, f"{k}_rank"] == first + 1 and p.rsplit(" ", 1)[0] in FLAT)
    lines = ["| Plan | Origin 1 cost | Origin 1 rank | Origin 2 cost | Origin 2 rank | Combined excess over hindsight | Overall rank |",
             "| --- | ---: | --- | ---: | --- | ---: | --- |"]
    for p in d["decision"]["overall"]["ranking"]:
        cell = lambda k: shown[k][p] + (" (joint best)" if c.loc[p, f"{k}_joint_best"] else "")
        lines.append(f"| {p} | {money(c.loc[p, keys[0] + '_total'])} | {cell(keys[0])} | {money(c.loc[p, keys[1] + '_total'])} | {cell(keys[1])} | "
                     f"{money(c.loc[p, 'combined_excess'])} | {overall[p]} |")
    note = (f"Costs in millions of model units. Hindsight cost: {money(hind[keys[0]])}m at Origin 1 and {money(hind[keys[1]])}m at Origin 2, so the tie bands are "
            f"{money(0.01 * hind[keys[0]])}m and {money(0.01 * hind[keys[1]])}m. \"=\" marks a shared rank: for a flat forecast the Rule and the LP give the same "
            "plan, and their costs differ by less than 1 model unit (solver rounding).")
    return "\n".join(lines), note


def improvement_table(d):
    imp, scopes = d["imp"], list(ORIGINS) + ["combined"]
    triple = lambda plan, col, fmt: " / ".join(fmt(imp.loc[(s, plan), col]) for s in scopes)
    lines = ["| Plan | Value of optimisation | Saving vs Persistence | Saving vs Seasonal naive | Avoidable cost removed, vs Persistence LP | "
             "Avoidable cost removed, vs Seasonal naive LP | Gap forecast skill vs Seasonal naive |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for p in PLANS:
        lines.append(f"| {p} | {triple(p, 'value_of_optimisation', money)} | {triple(p, 'saving_vs_persistence', money)} | "
                     f"{triple(p, 'saving_vs_seasonal_naive', money)} | {triple(p, 'share_avoidable_vs_persistence_lp', pct)} | "
                     f"{triple(p, 'share_avoidable_vs_seasonal_naive_lp', pct)} | {triple(p, 'gap_skill_vs_seasonal_naive', skill)} |")
    return "\n".join(lines)


def answer(d):
    dec, imp = d["decision"], d["imp"]
    o1, o2 = (dec["per_origin"][k] for k in ORIGINS)
    best, first = dec["best_forecast"], dec["overall"]["first"]
    assert dec["overall"]["clear_winners"] == [] and dec["overall"]["joint_best_at_both"] == [] and dec["overall"]["statement"] == "no clear winner"
    assert o1["joint_best"] == ["Seasonal naive LP", "SARIMA-24 LP"] and o2["joint_best"] == ["Persistence Rule", "Persistence LP"]
    assert first == "SARIMA-24 LP" and best["method"] == "SARIMA-24" and not best["clear"]
    assert best["lowest_per_origin"] == {"O1_sep2025": "Seasonal naive", "O2_mar2026": "Mean-24"}
    both, second = imp.loc[("combined", first)], imp.loc[("O2_mar2026", first)]
    assert both.saving_vs_persistence > 0 and both.saving_vs_seasonal_naive > 0 and both.value_of_optimisation > 0 and second.saving_vs_persistence < 0
    # the LP was worse than the Rule only for flat forecasts (the same plan), by less than 1 model unit
    diffs = {(k, m): dec["per_origin"][k]["value_of_optimisation"][m] for k in ORIGINS for m in METHODS}
    assert all(v > 0 or (abs(v) < 1 and m in FLAT) for (k, m), v in diffs.items())
    assert all(item.split(" at ")[0] in FLAT for item in dec["lp_worse_than_rule"])
    return (f"There is no clear winner: no plan was joint best at both origins (Origin 1: SARIMA-24 LP and Seasonal naive LP; Origin 2: Persistence, whose "
            f"Rule and LP plans are the same). SARIMA-24 LP ranked first overall ({money(both.excess_over_hindsight)}m above hindsight over both origins) and "
            f"SARIMA-24 had the lowest mean gap MAE ({beds(best['mean_gap_mae'])} beds), but it is not a clear best forecast: Seasonal naive was more accurate "
            f"at Origin 1 and Mean-24 at Origin 2. Over both origins SARIMA-24 LP cost {money(both.saving_vs_persistence)}m less than Persistence LP, "
            f"{money(both.saving_vs_seasonal_naive)}m less than Seasonal naive LP and {money(both.value_of_optimisation)}m less than following its own forecast "
            f"with the Rule, although at Origin 2 it cost {money(-second.saving_vs_persistence)}m more than Persistence LP; the LP was never worse than the Rule, "
            "apart from rounding of less than 1 model unit where the two give the same plan. Two non-overlapping six-month windows show whether a ranking "
            "repeats once; they do not establish a general ranking. Costs are illustrative model units (validated: False).")


def notes(d):
    log, cfg = d["log"], d["config"]
    fits = [e["sarima"][s] for e in log["origins"].values() for s in ("occupied", "available")]
    rows = {e["extra_trees"][s]["training_rows"] for e in log["origins"].values() for s in ("occupied", "available")}
    assert all(f["converged"] and f["labels"] == 24 for f in fits) and rows == {12}
    verification = (DATA / "verification.md").read_text(encoding="utf-8")
    assert "ALL CHECKS PASS" in verification and "Cross-check: Origin 1 against the earlier Sep 2025 results (outputs/sep2025_comparison/) | PASS" in verification
    assert "NHS revised Sep 2026 between the September and October 2026 publications (available -87, occupied -2)" in cfg["disclosures"]
    return [
        "A review of this design was written before anything was run (`outputs/clean_checkpoint/methodology_review.md`). Nothing was rated \"must change\". "
        "The points below need to be kept in mind when reading the results.", "",
        "- **Fair in information.** Every method sees the same 24 months and faces the same six months, costs, limits and scoring. Hindsight uses the same LP.",
        "- **Costs are lopsided.** A missing bed costs ten times as much as a spare bed, and neither decision method adds a safety margin. A forecast that is too "
        "high is protected; a forecast that is too low is punished. The cheapest plan is therefore not always the most accurate forecast. Read the mean signed "
        "gap error together with the MAE.",
        f"- **SARIMA-24 (departure).** It is fitted on all 24 months as labels. The project's original procedure dropped the first 12 months and needed at least "
        f"30 labels, so it could not run on a 24-month window. One seasonal difference leaves about 12 observations. All {len(fits)} fits converged.",
        f"- **ExtraTrees-24 (departure).** Its settings were selected earlier for occupied beds and are reused for available beds. Every fit had {rows.pop()} "
        f"training rows for {len(cfg['forecasts']['ExtraTrees-24']['features'])} features. The results describe this model with very little data, not the model "
        "family in general.",
        "- **Fixed in advance, not blind.** The rules were hashed before any forecast. But October 2025 - September 2026 actuals had been used in earlier, separate "
        "tests, and the model settings, the 24-month window and both origins were chosen with knowledge of that earlier work.",
        "- **Data.** The October 2026 NHS England publication is used throughout (SHA-256 checked). NHS revised September 2026 between the September and October "
        "2026 publications (available -87, occupied -2).",
        "- **Start and end.** Each window starts from zero extra beds and has no closing charge at the end. This is the same for every plan, including hindsight.",
        "- **Wording of the winner rule.** If more than one plan had met the \"clear winner\" definition, all would have been reported as joint winners. This did "
        "not arise.",
        "- **Verification.** All checks in `outputs/clean_checkpoint/verification.md` pass: unit tests, date and constraint checks, independent recomputation of "
        "costs and rules, a full rerun, and a second LP solver. Origin 1 reproduces the earlier September 2025 results of this repository to within rounding "
        f"error. Frozen-plan SHA-256: `{d['plans_sha']}`.", "",
        "Code: `scripts/clean_checkpoint/`. Results: `outputs/clean_checkpoint/`."]


LIMITATIONS = [
    "Two origins only. Their training windows share 18 of 24 months, so the two tests are not independent.",
    "Small samples: each model sees 24 months. Model settings are fixed, not tuned.",
    "Point forecasts only. No plan includes a safety margin or a measure of uncertainty.",
    "Univariate models; no outside drivers such as weather, flu or staffing.",
    "Occupied and available beds are forecast separately and combined into the gap afterwards.",
    "National monthly totals; no trust or regional detail.",
    "A retrospective test on final published figures, not on the figures available at each origin.",
    "Costs are illustrative model units, not NHS cash.",
]


def build():
    d = load()
    table, note = combined_table(d)
    hind = {k: d["decision"]["per_origin"][k]["hindsight_cost"] for k in ORIGINS}
    window = lambda k: d["config"]["origins"][k]
    span = lambda a, b: f"{pd.Timestamp(a):%B %Y} - {pd.Timestamp(b):%B %Y}"
    lines = [
        "# Which forecast gives the best hospital capacity plan?", "",
        "## Question", "",
        "Hospitals in England need enough general and acute beds every month. We forecast bed numbers six months ahead and turn each forecast into a plan for "
        "extra beds. Which forecasting method, paired with which way of making the plan, gives the cheapest plan once the real numbers are known, and how much "
        "better is it than simple baselines and than simply following the forecast?", "",
        "## Method", "",
        "Five methods forecast occupied beds and available beds six months ahead, each from the same 24 months of NHS England data: the 24-month mean (Mean-24), "
        "the last month repeated (Persistence), the same month one year earlier (Seasonal naive), a seasonal time-series model (SARIMA-24) and a tree-based "
        "machine-learning model (ExtraTrees-24). The two forecasts give the planning gap, occupied / 0.90 - available: the extra beds needed to keep occupancy "
        "at 90%. Each gap forecast is turned into a plan in two ways. The Rule opens exactly the forecast gap each month. The LP (a linear program) finds the "
        "cheapest plan for the forecast gap, so it may keep beds open through a short dip instead of closing and reopening them. A bed costs 500 a month to "
        "run, 550 to open and 300 to close; each missing bed costs 5,000 a month. A plan starts with no extra beds, can hold at most 15,000 and can open or "
        "close at most 5,000 in a month. Each plan is then held fixed and costed against the actual gap. Hindsight is the LP solved with the actual gaps: no "
        "plan can cost less.", "",
        "| Origin | Training window (24 months) | Forecast months |", "| --- | --- | --- |"]
    for key, (_, name, month) in ORIGINS.items():
        w = window(key)
        lines.append(f"| {name}: {month} | {span(*w['training_window'])} | {span(w['forecast_months'][0], w['forecast_months'][-1])} |")
    lines += ["",
              "### How we pick a winner", "",
              f"These rules were written down and hashed before any forecast in this run was computed (config SHA-256 `{d['config_sha']}`).", "",
              "- **Joint best.** At each origin the 10 plans are ranked by realised cost. Plans within 1% of that origin's hindsight cost of the cheapest plan are "
              "\"joint best\".",
              "- **Overall ranking.** Plans are ranked by combined excess over hindsight: cost minus hindsight cost, added over the two origins.",
              "- **Clear winner.** A plan that is joint best at both origins, or that beats the overall runner-up by more than the 1% band at each origin. "
              "Otherwise there is no clear winner.",
              "- **Best forecast.** The method with the lowest mean gap MAE (mean absolute error) across the two origins. It is \"clear\" only if it is the "
              "lowest at both origins by more than 5 beds.", "",
              "The rules were fixed in advance, but the test is not blind: the outcome months had already been used in earlier tests in this project.", ""]
    lines += origin_1(d) + [""] + origin_2(d) + [""]
    lines += ["## Both origins together", "", table, "", note, "", improvement_table(d), "",
              "Each cell shows Origin 1 / Origin 2 / both origins; money in millions of model units. "
              "**Value of optimisation** = Rule cost - LP cost for the same forecast. "
              "**Saving** = baseline cost - plan cost, with the same decision method on both sides (Rule against Rule, LP against LP); below zero means the plan "
              "cost more than the baseline. "
              "**Avoidable cost removed** = (baseline LP cost - plan cost) / (baseline LP cost - hindsight cost); 100% would match hindsight, and the figure "
              "becomes large and negative when the baseline was already close to hindsight. "
              "**Skill** = 1 - gap MAE / Seasonal naive gap MAE; above zero is more accurate than Seasonal naive. Skill for occupied and available beds is in "
              "`outputs/clean_checkpoint/improvements.csv`.", "",
              f"![Excess over hindsight by plan, both origins]({FIG}/both_excess_over_hindsight.png)", "",
              "## Answer", "", answer(d), "",
              "## Methodology notes", ""] + notes(d) + ["", "## Limitations", ""] + [f"- {item}" for item in LIMITATIONS]
    assert all(v > 0 for v in hind.values())
    return "\n".join(lines) + "\n"


def main():
    text, path = build(), REPO / "README.md"
    if "--check" in sys.argv[1:]:
        same = path.read_bytes() == text.encode("utf-8")
        print(f"README.md {'matches' if same else 'DOES NOT match'} the text generated from outputs/clean_checkpoint/ ({len(text.splitlines())} lines)")
        sys.exit(0 if same else 1)
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote README.md ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
