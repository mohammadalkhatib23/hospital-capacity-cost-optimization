"""Freeze the combined forecasts and plans, then assess them against saved outcomes.

Run plan and score separately, each with --data-dir pointing to the raw_v6 folder.
No model is fitted. Individual forecasts, calibration paths, plans and scores are reused.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/fair_comparison_rolling"))
import rolling

cc, fair = rolling.cc, rolling.fair
OUT = ROOT / "outputs/forecast_combination"
ROLL = rolling.OUT
VALUE = ROOT / "outputs/optimisation_value"
MONTHLY = ROOT / "outputs/monthly_origins"
BASE = 5000
METHODS = tuple(cc.METHODS)
FC = ["forecast_occupied", "forecast_available", "forecast_gap"]
ERROR = ["error_occupied", "error_available", "error_gap"]
PK = ["origin", "method", "plan", "penalty"]
MK = PK + ["month", "horizon"]
FLOW = ["x", "a", "r"]
COMPONENTS = FLOW + ["shortfall", "spare", "running", "opening", "closing", "shortfall_cost", "monthly_cost"]
KINDS = {"point": "LP", "protected": "Protected", "hindsight": "Hindsight"}


def read(path, **kwargs):
    return pd.read_csv(path, float_precision="round_trip", **kwargs)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sources(data_dir):
    cc.verify_hashes(OUT / "config.sha256")
    cfg = read_json(OUT / "config.json")
    for name, digest in cfg["source_hashes"].items():
        assert cc.sha256(ROOT / name) == digest, f"registered source changed: {name}"
    old = rolling.check_registration(ROLL, data_dir)
    assert cfg["populations"]["P2"] == old["origins"]
    assert cfg["data"]["sha256"] == old["data"]["sha256"]
    assert old["last_published_month"] == "2026-10"
    for folder, fname in [(ROLL, "plans.sha256"), (VALUE, "config.sha256"), (VALUE, "rule_plans.sha256"),
                          (MONTHLY, "data.sha256"), (MONTHLY, "new_plans.sha256")]:
        cc.verify_hashes(folder / fname)
    for manifest in [read_json(VALUE / "config.json")["source_hashes"],
                     read_json(VALUE / "publication_input_hashes.json")]:
        for name, digest in manifest.items():
            assert cc.sha256(ROOT / name) == digest, f"source hash mismatch for {name}"
    return cfg


def members(partial):
    return tuple(m for m in METHODS if not (partial and m == "SARIMA-24"))


def names(partial):
    suffix = " (4 methods)" if partial else ""
    return "Combo" + suffix, "Combo-median" + suffix


def paired(frame, keys, methods):
    """Require a rectangular matched panel; a missing path cannot be averaged away."""
    assert set(frame.method) == set(methods), "unexpected member list"
    assert not frame.duplicated(["method", *keys]).any(), "duplicate method/key"
    counts = frame.groupby(keys).method.nunique()
    assert len(counts) and counts.eq(len(methods)).all(), "unmatched forecasts or calibration paths"
    assert frame[keys].notna().all().all()


def combine_forecasts(frame, partial=False):
    methods = members(partial)
    avg_name, med_name = names(partial)
    keys = ["month", "horizon"]
    paired(frame, keys, methods)
    assert len(frame) == len(methods) * cc.HORIZON and np.isfinite(frame[FC]).all().all()
    grouped = frame.groupby(keys, sort=True)
    avg = grouped[FC].mean().reset_index()
    gap = cc.gap(avg.forecast_occupied, avg.forecast_available)
    np.testing.assert_allclose(gap, avg.forecast_gap, rtol=0, atol=1e-6)
    avg = avg.assign(method=avg_name, forecast_gap=gap)
    med = grouped.forecast_gap.median().reset_index().assign(method=med_name)
    return pd.concat([frame, avg, med], ignore_index=True)[["method", *keys, *FC]]


def combine_bank(bank, partial=False):
    keys = ["backtest_origin", "horizon", "month"]
    paired(bank, keys, METHODS)
    assert bank.groupby(["method", "backtest_origin"]).size().eq(cc.HORIZON).all()
    assert np.isfinite(bank[ERROR]).all().all()
    for target in cc.SERIES:
        actual = bank.groupby(keys)["actual_" + target].nunique()
        assert actual.eq(1).all(), f"backtest actuals differ for {target}"
        np.testing.assert_allclose(bank["error_" + target],
                                   bank["actual_" + target] - bank["forecast_" + target], rtol=0, atol=1e-6)
    chosen = bank[bank.method.isin(members(partial))]
    mean = chosen.groupby(keys, sort=True)[ERROR].mean().reset_index()
    gap_error = cc.gap(mean.error_occupied, mean.error_available)
    np.testing.assert_allclose(mean.error_gap, gap_error, rtol=0, atol=1e-6)
    mean = mean.assign(method=names(partial)[0], error_gap=gap_error)
    return pd.concat([chosen, mean], ignore_index=True)[["method", *keys, *ERROR]]


def dates(origin):
    return cc.target_months(pd.Timestamp(origin)).strftime("%Y-%m-%d").tolist()


def check_six(g, origin):
    assert g.month.tolist() == dates(origin)
    assert g.horizon.tolist() == list(range(1, cc.HORIZON + 1))


def feasible(x, a, r):
    assert np.isfinite([x, a, r]).all()
    assert ((x >= 0) & (x <= cc.CAP)).all()
    assert ((a >= 0) & (a <= cc.RAMP + 1e-6) & (r >= 0) & (r <= cc.RAMP + 1e-6)).all()
    np.testing.assert_allclose(cc.flows(x), [a, r], rtol=0, atol=1e-6)


def origin_plans(origin, partial, frame, bank, saved_quantiles):
    forecasts = combine_forecasts(frame, partial)
    errors = combine_bank(bank, partial)
    avg_name, med_name = names(partial)
    n = errors.backtest_origin.nunique()
    backtests = sorted(errors.backtest_origin.unique())
    plans, quantiles, scenarios = [], [], []
    for method, g in forecasts.groupby("method", sort=False):
        g = g.sort_values("horizon")
        check_six(g, origin)
        point = g.forecast_gap.to_numpy()
        if method != med_name:
            scen = fair.scenarios(point, fair.error_paths(errors, method))
            assert scen.shape == (n, cc.HORIZON)
            q = fair.quantile_path(scen, .9)
            if saved_quantiles is not None and method in METHODS:
                old = saved_quantiles[saved_quantiles.method == method].sort_values("horizon")
                check_six(old, origin)
                np.testing.assert_allclose(q, old.quantile_gap, rtol=0, atol=1e-6)
                q = old.quantile_gap.to_numpy()
            quantiles.append(g[["method", "month", "horizon"]].assign(q90=q))
        if method not in (avg_name, med_name):
            continue
        choices = [("Rule", BASE, cc.rule_plan(point)), ("LP", BASE, cc.lp_plan(point)[0])]
        if method == avg_name:
            for i, b in enumerate(backtests):
                scenarios.append(g[["method", "month", "horizon"]].assign(backtest_origin=b, scenario_gap=scen[i]))
            for penalty in ([BASE] if partial else rolling.PENALTIES):
                choices.append(("Protected", int(penalty), fair.saa_plan(scen, penalty)))
        for plan, penalty, x in choices:
            a, r = cc.flows(x)
            feasible(x, a, r)
            plans.append(g[["method", "month", "horizon"]].assign(plan=plan, penalty=penalty, x=x, a=a, r=r))
    return dict(forecasts=forecasts, banks=errors, scenarios=pd.concat(scenarios),
                quantiles=pd.concat(quantiles), plans=pd.concat(plans))


def record(message):
    with (OUT / "verification.md").open("a", encoding="utf-8", newline="\n") as log:
        log.write(message + "\n\n")
    print(message, flush=True)


def plan(cfg):
    assert not any((OUT / f).exists() for f in ["plans.csv", "plans.sha256", "costs.csv"]), "plans already frozen"
    test = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(Path(__file__).with_name("test_combination.py"))], cwd=ROOT,
                          capture_output=True, text=True, check=False)
    if test.returncode:
        raise AssertionError(test.stdout + test.stderr)
    record("- **PASS** Toy tests for the combined forecast, paired bank and four-method fallback. " +
           test.stdout.strip().splitlines()[-1] + ".")
    full, partials = cfg["populations"]["P2"], cfg["populations"]["P3_partial"]
    master = rolling.read_bank(ROLL / "cache/backtests.csv")
    records = {r["origin"]: r for r in read_json(ROLL / "config.json")["origin_records"]}
    # The monthly forecast export also holds evaluation data. Select only forecast columns on read.
    fallback_fc = read(MONTHLY / "forecasts.csv", usecols=["origin", "method", "month", "horizon", *FC])
    fit_logs = {**read_json(ROLL / "fit_log.json"), **read_json(MONTHLY / "new_fit_log.json")}
    output = {key: [] for key in ["forecasts", "banks", "scenarios", "quantiles", "plans"]}
    for origin in sorted(full + partials):
        partial = origin in partials
        date = pd.Timestamp(origin)
        assert fit_logs[origin]["cutoff"] == fit_logs[origin]["data_last_month"] == origin
        assert fit_logs[origin]["window"] == [(date - pd.DateOffset(months=23)).strftime("%Y-%m"), origin]
        cutoff = date - pd.DateOffset(months=cc.HORIZON)
        eligible = master[pd.to_datetime(master.backtest_origin) <= cutoff]
        bank = rolling.bank_for_origin(eligible, date)
        assert bank.month.max() <= date and bank.backtest_origin.nunique() == records[origin]["bank_paths"]
        if partial:
            f = fallback_fc[fallback_fc.origin == origin].drop(columns="origin")
            quantiles = None
        else:
            f = read(ROLL / origin / "forecasts.csv")
            f["horizon"] = f.groupby("method").cumcount() + 1
            saved_bank = rolling.read_bank(ROLL / origin / "backtests.csv")
            rolling.compare_banks(bank, saved_bank)
            bank = saved_bank
            quantiles = read(ROLL / origin / "quantiles.csv")
        bank["month"] = bank.month.dt.strftime("%Y-%m-%d")
        for key, data in origin_plans(origin, partial, f, bank, quantiles).items():
            output[key].append(data.assign(origin=origin))
    for key, frames in output.items():
        data = pd.concat(frames, ignore_index=True)
        columns = MK + FLOW if key == "plans" else ["origin"] + [c for c in data if c != "origin"]
        cc.write_csv(OUT / f"{key}.csv", data[columns])
    path = OUT / "plans.csv"
    cc.write_text(OUT / "plans.sha256", "".join(f"{cc.sha256(OUT / (k + '.csv'))}  {k}.csv\n"
                                                 for k in ["plans", "forecasts", "banks", "scenarios", "quantiles"]))
    cc.verify_hashes(OUT / "plans.sha256")
    record("- **PASS** Forecast linearity, matched backtest keys, paired series, shared sample sizes and "
           "origin cutoffs. Complete origins reproduce individual saved 0.9 quantiles. All combined schedules "
           f"are feasible and frozen before scoring. Plans SHA-256 `{cc.sha256(path)}`.")


def matched(left, right, keys, columns, exact=False):
    left = left.set_index(keys, verify_integrity=True).sort_index()
    right = right.set_index(keys, verify_integrity=True).sort_index()
    pd.testing.assert_index_equal(left.index, right.index)
    if exact:
        np.testing.assert_array_equal(left[columns], right[columns])
    else:
        np.testing.assert_allclose(left[columns], right[columns], rtol=1e-6, atol=1e-6)


def actuals(cfg, data_dir):
    series = ["actual_occupied", "actual_available", "actual_gap"]
    monthly = read(MONTHLY / "forecasts.csv", usecols=["origin", "month", "horizon", *series]).drop_duplicates()
    raw = pd.read_excel(data_dir / read_json(data_dir / "SOURCE.json")["file"], sheet_name=cc.SHEET, header=None)
    observed, _ = rolling.publication_inventory(raw)
    assert observed[-1] == pd.Timestamp("2026-10-01")
    book = cc.parse(raw, observed[-1])
    result = []
    for origin in cfg["populations"]["P2"]:
        a = read(VALUE / origin / "actuals.csv").rename(columns={
            "index": "month", "occupied": "actual_occupied", "available": "actual_available", "gap": "actual_gap"})
        a = a.assign(origin=origin, horizon=range(1, cc.HORIZON + 1))
        result.append(a)
        if origin in cfg["populations"]["P3"]:
            matched(a, monthly[monthly.origin == origin], ["origin", "month", "horizon"], series, exact=True)
    result.append(monthly[monthly.origin.isin(cfg["populations"]["P3_partial"])])
    result = pd.concat(result).sort_values(["origin", "horizon"]).reset_index(drop=True)
    for origin, g in result.groupby("origin"):
        check_six(g, origin)
        observed = book.loc[pd.to_datetime(g.month)]
        np.testing.assert_array_equal(g.actual_occupied, observed.occupied)
        np.testing.assert_array_equal(g.actual_available, observed.available)
        np.testing.assert_allclose(g.actual_gap, cc.gap(observed.occupied, observed.available), rtol=0, atol=1e-6)
    return result[["origin", "month", "horizon", *series]]


def old_records(partials):
    rolling_month = read(ROLL / "monthly_costs.csv")
    rolling_month["plan"] = rolling_month.kind.map(KINDS)
    rolling_total = read(ROLL / "totals.csv")
    rolling_total["plan"] = rolling_total.kind.map(KINDS)
    rolling_plans = read(ROLL / "plans.csv")
    rolling_plans["plan"] = rolling_plans.kind.map(KINDS)
    rule = read(VALUE / "rule_plans.csv").assign(plan="Rule", penalty=BASE)
    value_month = read(VALUE / "monthly_costs.csv").assign(penalty=BASE)
    value_total = read(VALUE / "costs.csv").melt(id_vars=["origin", "method", "Hindsight"],
                    value_vars=["Rule", "LP", "Protected"], var_name="plan", value_name="total")
    value_total = value_total.rename(columns={"Hindsight": "hindsight"}).assign(penalty=BASE)
    both = (rolling_total.penalty == BASE) & (rolling_total.plan != "Hindsight")
    matched(rolling_total[both], value_total[value_total.plan != "Rule"], PK, ["total", "hindsight"], exact=True)
    month_both = (rolling_month.penalty == BASE) & (rolling_month.plan != "Hindsight")
    matched(rolling_month[month_both], value_month[value_month.plan != "Rule"], MK, COMPONENTS)

    partial_plans = read(MONTHLY / "new_plans.csv").assign(penalty=BASE)
    month = read(MONTHLY / "monthly_costs.csv").assign(penalty=BASE)
    common = set(month.origin) - set(partials)
    totals = read(MONTHLY / "costs.csv").melt(id_vars=["origin", "method", "Hindsight"],
                    value_vars=["Rule", "LP", "Protected"], var_name="plan", value_name="total")
    totals = totals.rename(columns={"Hindsight": "hindsight"}).assign(penalty=BASE)
    matched(value_total[value_total.origin.isin(common)], totals[totals.origin.isin(common)],
            PK, ["total", "hindsight"], exact=True)
    common_month = month[month.origin.isin(common)]
    matched(value_month[value_month.origin.isin(common)], common_month[common_month.plan != "Hindsight"],
            MK, COMPONENTS)
    matched(rolling_month[rolling_month.origin.isin(common) & (rolling_month.penalty == BASE) &
                         (rolling_month.plan == "Hindsight")], common_month[common_month.plan == "Hindsight"],
            MK, COMPONENTS)
    partial_total = totals[totals.origin.isin(partials)]
    oracle = partial_total[["origin", "hindsight", "penalty"]].drop_duplicates()
    assert len(oracle) == len(partials)
    oracle = oracle.assign(method="Hindsight", plan="Hindsight", total=oracle.hindsight)
    original = pd.concat([rolling_total, value_total[value_total.plan == "Rule"], partial_total, oracle])
    original = original[PK + ["total", "hindsight"]]
    scores = pd.concat([rolling_month, value_month[value_month.plan == "Rule"], month[month.origin.isin(partials)]])
    frozen = pd.concat([rolling_plans, rule, partial_plans])
    matched(frozen, scores[scores.plan != "Hindsight"], MK, FLOW)
    return scores[MK + COMPONENTS], original


def realised(g, actual, penalty):
    g = g.sort_values("horizon")
    origin = g.origin.iloc[0]
    check_six(g, origin)
    feasible(*[g[c].to_numpy() for c in FLOW])
    total, rows = fair.score_plan(g.x.to_numpy(), actual[origin], penalty)
    rows["shortfall_cost"] = penalty * rows.shortfall
    np.testing.assert_allclose(rows.monthly_cost.sum(), total["total"], rtol=1e-6, atol=0)
    return total["total"], rows


def score(cfg, data_dir):
    cc.verify_hashes(OUT / "plans.sha256")
    assert not (OUT / "costs.csv").exists(), "scores already recorded"
    actual = actuals(cfg, data_dir)
    actual_gap = {o: g.actual_gap.to_numpy() for o, g in actual.groupby("origin")}
    saved_month, original = old_records(cfg["populations"]["P3_partial"])
    indexed = original.set_index(PK, verify_integrity=True).sort_index()
    old_groups = saved_month.groupby(PK, sort=False)
    assert set(old_groups.groups) == set(indexed.index)
    for keys, g in old_groups:
        g = g.sort_values("horizon")
        cost, result = realised(g, actual_gap, keys[-1])
        np.testing.assert_allclose(g[COMPONENTS], result[COMPONENTS], rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(cost, indexed.loc[keys, "total"], rtol=1e-6, atol=0)
    record(f"- **PASS** All {len(indexed)} existing six-month schedules and scores including Hindsight "
           "reconcile to frozen decisions or saved Hindsight schedules, actuals and monthly accounts. "
           "Common individual LP and Protected totals match both source studies exactly.")
    benchmarks = original[original.plan == "Hindsight"].set_index(["origin", "penalty"], verify_integrity=True)
    practical = original[original.plan != "Hindsight"].copy()
    new_month, new_cost = [], []
    frozen = read(OUT / "plans.csv")
    for keys, g in frozen.groupby(PK, sort=False):
        origin, method, plan_name, penalty = keys
        cost, rows = realised(g, actual_gap, penalty)
        hindsight = benchmarks.loc[(origin, penalty), "total"]
        new_cost.append(dict(origin=origin, method=method, plan=plan_name, penalty=penalty,
                             total=cost, hindsight=hindsight))
        new_month.append(rows.assign(origin=origin, method=method, plan=plan_name, penalty=penalty,
                                     month=dates(origin), horizon=range(1, cc.HORIZON + 1)))
    totals = pd.concat([practical, pd.DataFrame(new_cost)], ignore_index=True)
    totals["excess"] = totals.total - totals.hindsight
    assert np.isfinite(totals[["total", "hindsight", "excess"]]).all().all()
    assert (totals.excess >= -1e-6 * totals.hindsight).all(), "plan cheaper than Hindsight"
    checks = read(OUT / "forecasts.csv")
    for origin, g in checks.groupby("origin"):
        partial = origin in cfg["populations"]["P3_partial"]
        mean, median = names(partial)
        assert set(g.method) == set(members(partial)) | {mean, median}
        records = totals[totals.origin == origin]
        expected = {(m, p, BASE) for m in (*members(partial), mean) for p in ("Rule", "LP", "Protected")}
        expected.update({(median, p, BASE) for p in ("Rule", "LP")})
        if not partial:
            expected.update((m, "Protected", p) for m in (*members(partial), mean) for p in [2500, 10000])
        assert set(records[["method", "plan", "penalty"]].itertuples(index=False, name=None)) == expected
    assert not totals.duplicated(PK).any()
    for name, frame in [("actuals", actual), ("monthly_costs", pd.concat(new_month)[MK + COMPONENTS]),
                        ("costs", totals.sort_values(PK))]:
        cc.write_csv(OUT / f"{name}.csv", frame)
    cc.verify_hashes(OUT / "plans.sha256")
    cc.verify_hashes(OUT / "config.sha256")
    record(f"- **PASS** The frozen file yielded {len(new_cost)} new schedules. Their monthly costs sum to "
           "their totals at the stated penalty, all plans respect Hindsight and the workbook agrees with "
           "the saved actuals. The config and plans hashes match before and after scoring.")
    record("All Phase 2 checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["plan", "score"])
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.stage == "plan":
            assert not (OUT / "verification.md").exists(), "Phase 2 already started"
            cc.write_text(OUT / "verification.md", "# Forecast combination verification\n\n")
        config = sources(args.data_dir)
        if args.stage == "plan":
            record("- **PASS** Registered design, raw_v6 workbook, imported specifications, saved individual "
                   "forecasts, calibration banks, plans and scoring source manifests verified. Config SHA-256 "
                   f"`{cc.sha256(OUT / 'config.json')}`.")
            plan(config)
        else:
            score(config, args.data_dir)
    except (Exception, SystemExit) as exc:
        record(f"- **FAIL** {args.stage} stopped. {type(exc).__name__} {exc}")
        raise
