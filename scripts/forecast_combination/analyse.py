"""Evaluate the registered forecast-combination endpoints using verified scores only."""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/clean_checkpoint"))
import pipeline as cc

OUT = ROOT / "outputs/forecast_combination"
BASE = 5000
SINGLES = list(cc.METHODS)
MEAN = "Combo"
MEDIAN = "Combo-median"
METHODS = SINGLES + [MEAN]
MONTH_KEY = ["origin", "month", "horizon"]


def read(name):
    return pd.read_csv(OUT / name, float_precision="round_trip")


def monthly_errors(forecasts, actuals, quantiles):
    cols = MONTH_KEY + ["method"]
    data = forecasts[cols + ["forecast_gap"]].merge(actuals[MONTH_KEY + ["actual_gap"]],
                                                     on=MONTH_KEY, validate="many_to_one")
    assert len(data) == len(forecasts)
    data["signed_error"] = data.actual_gap - data.forecast_gap
    errors = data.groupby(["origin", "method"]).signed_error.agg(["size", "mean"])
    assert errors["size"].eq(cc.HORIZON).all()
    errors = errors["mean"].rename("signed_error").reset_index()
    errors["absolute_signed_error"] = errors.signed_error.abs()
    intervals = quantiles.merge(data, on=cols, validate="one_to_one")
    assert len(intervals) == len(quantiles)
    intervals["covered"] = intervals.actual_gap <= intervals.q90
    intervals["margin"] = intervals.q90 - intervals.forecast_gap
    return errors, intervals


def origin_metrics(costs, errors):
    base = costs[costs.penalty == BASE]
    assert base.groupby("origin").hindsight.nunique().eq(1).all()
    costs_wide = base.pivot(index=["origin", "method"], columns="plan", values="excess")
    costs_wide.columns = [p.lower() + "_excess" for p in costs_wide.columns]
    cases = errors.merge(costs_wide.reset_index(), on=["origin", "method"], validate="one_to_one")
    assert len(cases) == len(errors) and cases.lp_excess.notna().all()
    return cases


def compare_with_combo(costs):
    """Record Combo's win/tie/loss against each individual at each origin."""
    active = costs[costs.plan.isin(["LP", "Protected"])]
    keys = ["origin", "plan", "penalty"]
    combo = active[active.method == MEAN][keys + ["total"]].rename(columns={"total": "combo_total"})
    comp = active[active.method.isin(SINGLES)].merge(combo, on=keys, validate="many_to_one")
    comp["combo_minus_method"] = comp.combo_total - comp.total
    comp["tie_band"] = .01 * comp.hindsight
    comp["combo_result"] = np.select(
        [comp.combo_minus_method < -comp.tie_band, comp.combo_minus_method > comp.tie_band],
        ["win", "loss"], default="tie")
    return comp[[*keys, "method", "combo_minus_method", "tie_band", "combo_result"]]


def error_patterns(cases, costs):
    base = costs[costs.penalty.eq(BASE) & costs.plan.eq("Protected")].groupby("origin").hindsight.first()
    rows = []
    for origin, group in cases.groupby("origin"):
        group = group.set_index("method")
        entrants = [m for m in SINGLES if m in group.index]
        single = group.loc[entrants]
        label = MEAN if len(single) == len(SINGLES) else "Combo (4 methods)"
        combo = group.loc[label]
        signs = single.signed_error
        low, high = int(signs.gt(0).sum()), int(signs.lt(0).sum())
        dominant = "low" if low > high else ("high" if high > low else "split")
        best = single.protected_excess.idxmin()
        near_best = combo.protected_excess - single.loc[best, "protected_excess"]
        rows.append(dict(origin=origin, method=label, n_components=len(single), components_low=low,
                         components_high=high, dominant_direction=dominant, agreeing=max(low, high),
                         combo_signed=combo.signed_error, mean_component_abs=single.absolute_signed_error.mean(),
                         combo_abs=combo.absolute_signed_error, lp_excess=combo.lp_excess,
                         protected_excess=combo.protected_excess,
                         protection_saving=combo.lp_excess - combo.protected_excess,
                         best_single_protected=best, combo_minus_best_protected=near_best,
                         lost_to_best_outside_band=near_best > .01 * base.loc[origin]))
    return pd.DataFrame(rows)


def endpoints(pop, group, comparisons):
    out = []
    for (plan, penalty, method), g in group.groupby(["plan", "penalty", "method"]):
        series = g.set_index("origin").excess
        counts = comparisons[(comparisons.plan == plan) & (comparisons.penalty == penalty) &
                             (comparisons.method == method)].combo_result.value_counts()
        out.append(dict(population=pop, plan=plan, penalty=penalty, method=method, n_origins=len(g),
                        mean_excess=series.mean(), p90_excess=series.quantile(.90, interpolation="linear"),
                        worst_excess=series.max(), worst_origin=series.idxmax(),
                        combo_wins=counts.get("win", 0) if len(counts) else None,
                        combo_ties=counts.get("tie", 0) if len(counts) else None,
                        combo_losses=counts.get("loss", 0) if len(counts) else None))
    return pd.DataFrame(out)


def centring(pop, cases):
    data = cases.groupby("method").agg(n_origins=("origin", "size"), mean_signed=("signed_error", "mean"),
                                      mean_abs_signed=("absolute_signed_error", "mean"),
                                      sd_signed=("signed_error", "std"), min_signed=("signed_error", "min"),
                                      max_signed=("signed_error", "max"))
    return data.reset_index().assign(population=pop)


def coverage(pop, intervals):
    # Horizon zero denotes all six target months pooled.
    groups = pd.concat([intervals, intervals.assign(horizon=0)]).groupby(["method", "horizon"])
    frame = groups.agg(covered=("covered", "sum"), months=("covered", "size"),
                       coverage=("covered", "mean"), mean_margin=("margin", "mean")).reset_index()
    return frame.assign(population=pop)


def decomposition(cases, origins):
    selected = cases.set_index(["origin", "method"])
    rows = []
    for origin in origins:
        combo, seasonal = selected.loc[(origin, MEAN)], selected.loc[(origin, "Seasonal naive")]
        averaging = seasonal.lp_excess - combo.lp_excess
        protection = combo.lp_excess - combo.protected_excess
        total = seasonal.lp_excess - combo.protected_excess
        np.testing.assert_allclose(averaging + protection, total, rtol=0, atol=1e-6)
        rows.append(dict(origin=origin, sn_lp_excess=seasonal.lp_excess, combo_lp_excess=combo.lp_excess,
                         combo_protected_excess=combo.protected_excess, sn_protected_excess=seasonal.protected_excess,
                         averaging_saving=averaging, protection_saving=protection, total_saving=total,
                         saving_vs_protected_sn=seasonal.protected_excess - combo.protected_excess))
    return pd.DataFrame(rows)


def hypothesis_checks(pop, origins, registered_subsets, protected, errors, cases):
    """Use the unrounded means for H1, and apply the extra clear-winner rule separately."""
    matrix = cases[cases.method.isin(METHODS)].pivot(index="origin", columns="method", values="protected_excess")
    matrix = matrix.loc[origins, METHODS]
    assert matrix.notna().all().all()
    rows = []
    for offset in range(6):
        subset = matrix.iloc[offset::6]
        assert list(subset.index) == registered_subsets[str(offset)]
        dates = pd.PeriodIndex(subset.index, freq="M").asi8
        assert len(subset) and np.all(np.diff(dates) >= 6)
        means = subset.mean()
        for method in METHODS:
            rows.append(dict(population=pop, offset=offset, method=method, origins=";".join(subset.index),
                             n_origins=len(subset), mean_excess=means[method], lowest=means[method] == means.min()))
    subsets = pd.DataFrame(rows)
    n_lowest = int(subsets[subsets.method == MEAN].lowest.sum())
    data = protected.set_index("method").loc[METHODS]
    err = errors.set_index("method").loc[METHODS]
    indiv = data.loc[SINGLES]
    h1 = data.loc[MEAN, "mean_excess"] < indiv.mean_excess.min()
    h2 = data.loc[MEAN, "worst_excess"] < indiv.worst_excess.min()
    h3 = err.loc[MEAN, "mean_abs_signed"] < err.loc[SINGLES].mean_abs_signed.min()
    beats_each = (indiv.combo_wins > indiv.combo_losses).all()
    flags = dict(population=pop, H1=h1, H2=h2, H3=h3, lowest_mean_protected=data.mean_excess.idxmin(),
                 lowest_worst_protected=data.worst_excess.idxmin(), lowest_mean_abs_signed=err.mean_abs_signed.idxmin(),
                 lowest_subsets=n_lowest, beats_each_more_than_loses=beats_each,
                 clear_winner=bool(h1 and n_lowest >= 4 and beats_each))
    return flags, subsets


def run():
    verified = (OUT / "verification.md").read_text(encoding="utf-8")
    review = (OUT / "logic_review.md").read_text(encoding="utf-8")
    if "All Phase 2 checks passed." not in verified or "All Part B checks passed." not in review:
        raise RuntimeError("Endpoint analysis requires completed Phase 2 and Part B checks.")
    cc.verify_hashes(OUT / "config.sha256")
    cc.verify_hashes(OUT / "plans.sha256")
    config = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    pops = config["populations"]
    costs = read("costs.csv")
    errors, intervals = monthly_errors(read("forecasts.csv"), read("actuals.csv"), read("quantiles.csv"))
    cases = origin_metrics(costs, errors)
    comparisons = compare_with_combo(costs)
    patterns = error_patterns(cases, costs)
    chain = decomposition(cases, pops["P2"])

    endpoint_frames, error_frames, calibration_frames = [], [], []
    subset_frames, direction_frames, decisions, contributions = [], [], [], []
    for pop in ["P1", "P2", "P3"]:
        origins = pops[pop]
        scores = costs[costs.origin.isin(origins)]
        methods = cases[cases.origin.isin(origins)]
        assert sorted(origins) == origins and not methods.method.str.contains("4 methods").any()
        assert len(origins) == {"P1": 23, "P2": 25, "P3": 11}[pop]
        comp = comparisons[comparisons.origin.isin(origins)]
        results = endpoints(pop, scores, comp)
        assert results.n_origins.eq(len(origins)).all()
        # Every E1/E2 comparator pair has exactly one outcome per origin.
        compared = results[results.method.isin(SINGLES) & results.plan.isin(["LP", "Protected"])]
        assert compared[["combo_wins", "combo_ties", "combo_losses"]].sum(axis=1).eq(len(origins)).all()
        err = centring(pop, methods)
        cal = coverage(pop, intervals[intervals.origin.isin(origins)])
        assert cal.loc[cal.horizon.eq(0), "months"].eq(cc.HORIZON * len(origins)).all()
        endpoint_frames.append(results)
        error_frames.append(err)
        calibration_frames.append(cal)
        protected = results[results.penalty.eq(BASE) & results.plan.eq("Protected")]
        flags, splits = hypothesis_checks(pop, origins, config["subsets"][pop], protected, err, methods)
        subset_frames.append(splits)
        if pop in config["hypotheses"]["populations"]:
            decisions.append(flags)
        by_sign = patterns[patterns.origin.isin(origins)].copy()
        by_sign["protection_saved"] = by_sign.protection_saving.gt(0)
        direction = by_sign.groupby(["dominant_direction", "agreeing"]).agg(
            n_origins=("origin", "size"), origins=("origin", ";".join),
            mean_component_abs=("mean_component_abs", "mean"), mean_combo_abs=("combo_abs", "mean"),
            mean_protection_saving=("protection_saving", "mean"),
            origins_protection_saved=("protection_saved", "sum"),
            origins_lost_to_best_protected=("lost_to_best_outside_band", "sum"),
        ).reset_index()
        direction_frames.append(direction.assign(population=pop))
        means = chain[chain.origin.isin(origins)].drop(columns="origin").mean().to_dict()
        contributions.append(dict(population=pop, n_origins=len(origins), **means))

    tables = {
        "origin_metrics": cases, "direction_by_origin": patterns, "direction_summary": pd.concat(direction_frames),
        "pairwise_by_origin": comparisons,
        "endpoints": pd.concat(endpoint_frames), "centring": pd.concat(error_frames),
        "coverage": pd.concat(calibration_frames), "subsets": pd.concat(subset_frames),
        "hypotheses": pd.DataFrame(decisions), "decomposition": chain,
        "decomposition_summary": pd.DataFrame(contributions),
    }
    for name, frame in tables.items():
        cc.write_csv(OUT / f"{name}.csv", frame)


if __name__ == "__main__":
    run()
