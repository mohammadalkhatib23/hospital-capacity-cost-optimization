"""Assemble the April 2025–April 2026 experiment from the registered raw_v6 study.

Run with --data-dir pointing to the folder containing the workbook and SOURCE.json.
Rebuild only the two incomplete origins; original study files are read-only.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/fair_comparison_rolling"))
import rolling
from case_check import verify_cases

cc, fair = rolling.cc, rolling.fair
ROLL = rolling.OUT
VALUE = ROOT / "outputs/optimisation_value"
OUT = ROOT / "outputs/monthly_origins"
ORIGINS = pd.date_range("2025-04-01", "2026-04-01", freq="MS").strftime("%Y-%m").tolist()
MISSING = {"2025-10": "occupied", "2026-01": "available"}
COMMON = [origin for origin in ORIGINS if origin not in MISSING]
PLANS = ["Rule", "LP", "Protected"]
KINDS = {"point": "LP", "protected": "Protected", "hindsight": "Hindsight"}
FC = ["forecast_occupied", "forecast_available", "forecast_gap"]
ACTUAL = ["actual_occupied", "actual_available", "actual_gap"]
FLOW = ["x", "a", "r"]
MONTHLY = FLOW + ["shortfall", "spare", "running", "opening", "closing", "shortfall_cost", "monthly_cost"]
KEY = ["origin", "method", "plan", "month", "horizon"]
COSTS = ["origin", "method", *PLANS, "Hindsight"]


def read(path):
    return pd.read_csv(path, float_precision="round_trip")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_hashes(data_dir):
    cfg = rolling.check_registration(ROLL, data_dir)
    cc.verify_hashes(ROLL / "plans.sha256")
    cc.verify_hashes(VALUE / "config.sha256")
    cc.verify_hashes(VALUE / "rule_plans.sha256")
    for manifest in [load_json(VALUE / "config.json")["source_hashes"],
                     load_json(VALUE / "publication_input_hashes.json")]:
        for name, expected in manifest.items():
            assert cc.sha256(ROOT / name) == expected, f"Hash mismatch: {name}"
    assert sorted(set(ORIGINS) & set(cfg["origins"])) == COMMON
    failed = {r["origin"]: r["failed_targets"] for r in load_json(ROLL / "planning_convergence.json")
              if r["status"] == "excluded"}
    assert failed == {o: [target] for o, target in MISSING.items()}
    return cfg


def check_fit(origin, log):
    date = pd.Timestamp(origin)
    start = (date - pd.DateOffset(months=cc.WINDOW - 1)).strftime("%Y-%m")
    assert log["cutoff"] == log["data_last_month"] == origin
    assert log["window"] == [start, origin]
    assert log["forecast_months"] == cc.target_months(date).strftime("%Y-%m").tolist()
    assert log["seasonal_naive_source"] == (cc.target_months(date) - pd.DateOffset(years=1)).strftime("%Y-%m").tolist()
    for info in log["extra_trees"].values():
        assert info["earliest_month_used"] == start and info["last_row"] == origin
        assert info["training_rows"] == 12 and info["features"] == len(cc.FEATURES)
    if origin in COMMON:
        for info in log["sarima"].values():
            assert info["converged"] and info["labels"] == cc.WINDOW
            assert [info["label_start"], info["label_end"]] == log["window"]


def calibration(master, origin, observed, record):
    date = pd.Timestamp(origin)
    # Select by dates before passing any cached numerical values to the planning functions.
    eligible = master[pd.to_datetime(master.backtest_origin) <= date - pd.DateOffset(months=cc.HORIZON)]
    bank = rolling.bank_for_origin(eligible, date)
    start = pd.to_datetime(bank.window_start)
    b = pd.to_datetime(bank.backtest_origin)
    assert (start == b - pd.DateOffset(months=cc.WINDOW - 1)).all()
    assert (b < bank.month).all() and (bank.month <= date).all()
    assert start.isin(observed).all() and bank.month.isin(observed).all()
    assert bank.backtest_origin.nunique() == record["bank_paths"]
    assert set(bank.backtest_origin.str[:7]) == (
        set(fair.backtest_origins(observed[0], date).strftime("%Y-%m")) - set(record["dropped_backtests"])
    )
    np.testing.assert_allclose(bank.error_gap, cc.gap(bank.actual_occupied - bank.forecast_occupied,
                                                       bank.actual_available - bank.forecast_available),
                               rtol=0, atol=1e-6)
    if origin in COMMON:
        rolling.compare_banks(bank, rolling.read_bank(ROLL / origin / "backtests.csv"))
    return bank


def missing_plans(data_dir, banks):
    forecasts, frozen, logs = [], [], {}
    for origin in MISSING:
        date = pd.Timestamp(origin)
        history = cc.load(data_dir, cutoff=date)
        w = cc.window(history, date)
        months = cc.target_months(date)
        log = dict(cutoff=origin, data_last_month=history.index[-1].strftime("%Y-%m"),
                   window=[w.index[0].strftime("%Y-%m"), origin], forecast_months=months.strftime("%Y-%m").tolist(),
                   seasonal_naive_source=(months - pd.DateOffset(years=1)).strftime("%Y-%m").tolist(), extra_trees={})
        bank = banks[origin]
        assert bank.month.max() <= w.index[-1] == date
        functions = {"Mean-24": cc.mean_forecast, "Persistence": cc.persistence_forecast,
                     "Seasonal naive": cc.seasonal_naive_forecast, "ExtraTrees-24": cc.extra_trees_forecast}
        for method, forecast in functions.items():
            preds = {}
            for target in cc.SERIES:
                if method == "ExtraTrees-24":
                    preds[target], log["extra_trees"][target] = forecast(w[target])
                else:
                    preds[target] = forecast(w[target])
            point = cc.gap(preds["occupied"], preds["available"])
            f = pd.DataFrame(dict(origin=origin, method=method, month=months, horizon=range(1, 7),
                                  forecast_occupied=preds["occupied"], forecast_available=preds["available"],
                                  forecast_gap=point))
            forecasts.append(f)
            scen = fair.scenarios(point, fair.error_paths(bank, method))
            schedules = {"Rule": cc.rule_plan(point), "LP": cc.lp_plan(point)[0],
                         "Protected": fair.saa_plan(scen, fair.BASE)}
            for plan, x in schedules.items():
                a, r = cc.flows(x)
                frozen.append(f[["origin", "method", "month", "horizon"]].assign(plan=plan, x=x, a=a, r=r))
        check_fit(origin, log)
        log["backtest_origins"] = sorted(bank.backtest_origin.unique())
        log["last_calibration_target"] = bank.month.max().strftime("%Y-%m")
        logs[origin] = log
    return pd.concat(forecasts, ignore_index=True), pd.concat(frozen, ignore_index=True)[KEY + FLOW], logs


def saved_actuals(data_dir):
    # Every target month, including targets of the two incomplete origins, is already saved.
    frames = []
    for origin in COMMON:
        a = read(VALUE / origin / "actuals.csv").rename(columns={
            "index": "month", "occupied": "actual_occupied", "available": "actual_available", "gap": "actual_gap"
        })
        a["month"] = pd.to_datetime(a.month)
        assert a.month.tolist() == cc.target_months(pd.Timestamp(origin)).tolist()
        frames.append(a)
    actual = pd.concat(frames).drop_duplicates().set_index("month", verify_integrity=True).sort_index()
    published = cc.load(data_dir, cutoff=pd.Timestamp("2026-10-01")).loc[actual.index]
    np.testing.assert_array_equal(actual.actual_occupied, published.occupied)
    np.testing.assert_array_equal(actual.actual_available, published.available)
    np.testing.assert_allclose(actual.actual_gap, cc.gap(published.occupied, published.available), rtol=0, atol=1e-8)
    return actual[ACTUAL]


def score_missing(frozen, actual):
    monthly, totals = [], []
    for origin in MISSING:
        months = cc.target_months(pd.Timestamp(origin))
        gaps = actual.loc[months].actual_gap.to_numpy()
        candidates = [(m, p, g.sort_values("month").x.to_numpy())
                      for (m, p), g in frozen[frozen.origin == origin].groupby(["method", "plan"])]
        candidates.append(("Hindsight", "Hindsight", fair.hindsight_plan(gaps, fair.BASE)))
        for method, plan, x in candidates:
            result, table = fair.score_plan(x, gaps, fair.BASE)
            monthly.append(table.assign(origin=origin, method=method, plan=plan, month=months, horizon=range(1, 7)))
            totals.append(dict(origin=origin, method=method, plan=plan, total=result["total"]))
    long = pd.DataFrame(totals)
    ordinary = long[long.plan != "Hindsight"].pivot(index=["origin", "method"], columns="plan", values="total")
    oracle = long[long.plan == "Hindsight"].set_index("origin").total
    costs = ordinary.reset_index().assign(Hindsight=lambda x: x.origin.map(oracle))
    return pd.concat(monthly, ignore_index=True)[KEY + MONTHLY], costs[COSTS]


def same_rows(left, right, keys, columns, *, rtol=0, atol=1e-6):
    l = left.set_index(keys, verify_integrity=True).sort_index()
    r = right.set_index(keys, verify_integrity=True).sort_index()
    pd.testing.assert_index_equal(l.index, r.index)
    np.testing.assert_allclose(l[columns], r[columns], rtol=rtol, atol=atol)


def saved_common():
    monthly = cc.read_csv(VALUE / "monthly_costs.csv").query("origin in @COMMON")
    costs = read(VALUE / "costs.csv").query("origin in @COMMON")
    rule = cc.read_csv(VALUE / "rule_plans.csv").query("origin in @COMMON").assign(plan="Rule")
    other = cc.read_csv(ROLL / "plans.csv").query("origin in @COMMON and penalty == 5000").copy()
    other["plan"] = other.kind.map(KINDS)
    frozen = pd.concat([rule, other])[KEY + FLOW]
    same_rows(monthly, frozen, KEY, FLOW)

    rolling_month = cc.read_csv(ROLL / "monthly_costs.csv").query("origin in @COMMON and penalty == 5000").copy()
    rolling_month["plan"] = rolling_month.kind.map(KINDS)
    same_rows(monthly[monthly.plan != "Rule"], rolling_month[rolling_month.plan != "Hindsight"], KEY, MONTHLY)
    oracle = rolling_month[rolling_month.plan == "Hindsight"]
    rolling_cost = read(ROLL / "totals.csv").query("origin in @COMMON and penalty == 5000")
    joint = rolling_cost[rolling_cost.kind != "hindsight"].merge(costs, on=["origin", "method"], validate="many_to_one")
    np.testing.assert_allclose(joint.total, np.where(joint.kind == "point", joint.LP, joint.Protected), rtol=1e-12)
    np.testing.assert_allclose(joint.hindsight, joint.Hindsight, rtol=1e-12)
    forecasts = []
    for origin in COMMON:
        f = cc.read_csv(ROLL / origin / "forecasts.csv").assign(origin=origin)
        f["horizon"] = f.groupby("method").cumcount() + 1
        forecasts.append(f)
    return pd.concat(forecasts), pd.concat([monthly, oracle])[KEY + MONTHLY], costs[COSTS]


def check_all(forecasts, monthly, costs):
    assert len(forecasts) == 63 * 6 and len(monthly) == 202 * 6 and len(costs) == 63
    assert set(forecasts.origin) == set(monthly.origin) == set(costs.origin) == set(ORIGINS)
    assert np.isfinite(forecasts[FC + ACTUAL]).all().all() and np.isfinite(monthly[MONTHLY]).all().all()
    assert np.isfinite(costs[PLANS + ["Hindsight"]]).all().all()
    assert not forecasts.duplicated(["origin", "method", "horizon"]).any()
    assert not monthly.duplicated(KEY).any() and not costs.duplicated(["origin", "method"]).any()
    np.testing.assert_allclose(forecasts.forecast_gap, cc.gap(forecasts.forecast_occupied, forecasts.forecast_available),
                               rtol=0, atol=1e-6)
    max_error = 0.
    for origin in ORIGINS:
        methods = set(cc.METHODS) - ({"SARIMA-24"} if origin in MISSING else set())
        f = forecasts[forecasts.origin == origin]
        c = costs[costs.origin == origin].set_index("method")
        m = monthly[monthly.origin == origin]
        assert set(f.method) == set(c.index) == methods and c.Hindsight.nunique() == 1
        assert set(zip(m.method, m.plan)) == {(s, p) for s in methods for p in PLANS} | {("Hindsight", "Hindsight")}
        dates = cc.target_months(pd.Timestamp(origin)).tolist()
        for _, g in f.groupby("method"):
            assert g.sort_values("horizon").month.tolist() == dates
        gaps = f.drop_duplicates("month").sort_values("month").actual_gap.to_numpy()
        for (method, plan), g in m.groupby(["method", "plan"]):
            g = g.sort_values("horizon")
            assert g.horizon.tolist() == list(range(1, 7)) and g.month.tolist() == dates
            assert g.x.between(0, cc.CAP).all() and g.a.between(0, cc.RAMP + 1e-6).all()
            assert g.r.between(0, cc.RAMP + 1e-6).all()
            expected = cc.cost_table(g.x, gaps)
            np.testing.assert_allclose(g[MONTHLY], expected[MONTHLY], rtol=1e-10, atol=1e-6)
            total = c.Hindsight.iloc[0] if method == "Hindsight" else c.loc[method, plan]
            error = abs(g.monthly_cost.sum() - total) / total
            assert error <= 1e-6, f"{origin} {method} {plan}: monthly sum disagrees with total"
            assert c.Hindsight.iloc[0] <= total * (1 + 1e-6)
            max_error = max(max_error, error)
    return max_error


def run(data_dir):
    report = []
    def save_report():
        cc.write_text(OUT / "verification.md", "# Monthly-origin verification\n\n" + "\n\n".join(report) + "\n")

    @contextmanager
    def checking(name):
        details = []
        try:
            yield details
        except (Exception, SystemExit) as exc:
            report.append(f"- **FAIL** {name}. {type(exc).__name__} {exc}")
            save_report()
            raise
        report.append(f"- **PASS** {name}. " + " ".join(details))
        save_report()
        print(report[-1], flush=True)

    with checking("Registered sources, plans and published score hashes") as note:
        cfg = audit_hashes(data_dir)
        note.append(f"Rolling config `{cc.sha256(ROLL / 'config.json')}`, frozen schedules "
                    f"`{cc.sha256(ROLL / 'plans.csv')}`, Rule config `{cc.sha256(VALUE / 'config.json')}` "
                    f"and Rule schedules `{cc.sha256(VALUE / 'rule_plans.csv')}`. Scores match the saved "
                    "input manifests. Only studies using the registered October 2026 workbook were eligible.")
    with checking("Source workbook, nonblank observations and target windows") as note:
        source = rolling.verify_source(data_dir)
        raw = pd.read_excel(data_dir / source["file"], sheet_name=cc.SHEET, header=None)
        observed, blanks = rolling.publication_inventory(raw)
        del raw
        assert observed[-1] == pd.Timestamp("2026-10-01")
        assert blanks.tolist() == pd.date_range("2026-11-01", "2027-03-01", freq="MS").tolist()
        for key in ORIGINS:
            o = pd.Timestamp(key)
            rolling.assert_observed_window(observed, o - pd.DateOffset(months=23), cc.target_months(o)[-1])
        note.append(f"Workbook SHA-256 `{source['sha256']}`. {len(observed)} complete months through October "
                    f"2026 and {len(blanks)} trailing blank months. All training and target months are present.")
    with checking("Origin cutoffs and calibration history") as note:
        records = {r["origin"]: r for r in cfg["origin_records"]}
        master = rolling.read_bank(ROLL / "cache/backtests.csv")
        banks = {o: calibration(master, o, observed, records[o]) for o in ORIGINS}
        old_logs = load_json(ROLL / "fit_log.json")
        for key in COMMON:
            check_fit(key, old_logs[key])
        note.append("All 13 banks use only complete six-month paths with targets observed by their planning "
                    "origin. Every backtest uses the registered 24-month window. The 11 common origins "
                    "match the frozen forecasts' fit logs and their saved calibration banks.")
    with checking("Partial origins and reproducibility before scoring") as note:
        new, schedules, logs = missing_plans(data_dir, banks)
        repeat_f, repeat_s, repeat_logs = missing_plans(data_dir, banks)
        same_rows(new, repeat_f, ["origin", "method", "month", "horizon"], FC)
        same_rows(schedules, repeat_s, KEY, FLOW)
        assert logs == repeat_logs
        cc.write_csv(OUT / "new_plans.csv", schedules)
        cc.write_text(OUT / "new_plans.sha256", f"{cc.sha256(OUT / 'new_plans.csv')}  new_plans.csv\n")
        cc.verify_hashes(OUT / "new_plans.sha256")
        rolling.write_json(OUT / "new_fit_log.json", logs)
        note.append("October 2025 (occupied) and January 2026 (available) retain the registered "
                    "SARIMA-24 convergence failures. Raw workbook histories were truncated at each origin "
                    "before fitting the other four methods. Two runs reproduced all forecasts, 24 deployable "
                    f"schedules and flows within 1e-6 beds. Frozen schedule SHA-256 `{cc.sha256(OUT / 'new_plans.csv')}`.")
    with checking("Reuse of saved scores and reproducibility of new scores and Hindsight") as note:
        actual = saved_actuals(data_dir)
        fc, m, c = saved_common()
        missing_m, missing_c = score_missing(cc.read_csv(OUT / "new_plans.csv"), actual)
        repeat_m, repeat_c = score_missing(repeat_s, actual)
        same_rows(missing_m, repeat_m, KEY, MONTHLY, rtol=1e-6)
        same_rows(missing_c, repeat_c, ["origin", "method"], PLANS + ["Hindsight"], rtol=1e-6)
        cc.verify_hashes(OUT / "new_plans.sha256")
        forecasts = pd.concat([fc, new]).join(actual, on="month", validate="many_to_one")
        forecasts = forecasts[["origin", "method", "month", "horizon", *FC, *ACTUAL]]
        monthly = pd.concat([m, missing_m], ignore_index=True)
        costs = pd.concat([c, missing_c], ignore_index=True)
        note.append("Eleven complete origins reused their saved forecasts, schedules and scores. "
                    "All published actuals agree with the required workbook. The two partial origins' "
                    "monthly scores, totals and Hindsight schedules reproduced within 1e-6 relative tolerance.")
    with checking("Feasibility, monthly accounting, coverage and Hindsight bound") as note:
        max_error = check_all(forecasts, monthly, costs)
        note.append("63 method-origin forecasts, 189 deployable plans and 13 Hindsight plans. "
                    "All 1,212 month-by-plan rows passed date, flow, capacity, ramp, shortfall, spare "
                    f"and cost checks. Largest relative monthly-sum discrepancy {max_error:.3g}.")
    with checking("Reproduction of the two published case studies") as note:
        note.append(verify_cases(forecasts, monthly, costs))
    with checking("Published numeric files and availability register") as note:
        availability = pd.DataFrame([dict(origin=o, method=m, status="not available" if o in MISSING and m == "SARIMA-24" else "available",
                                          failed_target=MISSING[o] if o in MISSING and m == "SARIMA-24" else "")
                                     for o in ORIGINS for m in cc.METHODS])
        assert (availability.status == "available").sum() == len(costs)
        frames = {"forecasts.csv": forecasts.sort_values(["origin", "method", "horizon"]),
                  "monthly_costs.csv": monthly.sort_values(KEY), "costs.csv": costs.sort_values(["origin", "method"]),
                  "availability.csv": availability}
        for name, frame in frames.items():
            cc.write_csv(OUT / name, frame)
        cc.write_text(OUT / "data.sha256", "".join(f"{cc.sha256(OUT / n)}  {n}\n" for n in
                      [*frames, "new_plans.csv", "new_fit_log.json"]))
        cc.verify_hashes(OUT / "data.sha256")
        note.append("Six assembled data, calibration-log and new-plan files have verified SHA-256 digests in "
                    "`data.sha256`. All costs use the registered illustrative prices (validated: False).")
    report.append("All Phase 1 checks passed.")
    save_report()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    run(parser.parse_args().data_dir)
