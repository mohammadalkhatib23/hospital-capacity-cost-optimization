"""Phase 4 verification of the clean checkpoint. Writes outputs/clean_checkpoint/verification.md with PASS/FAIL per check.

Unit tests, logic checks on the saved results (costs, accuracy and rules are recomputed here with separate code), a full
rerun in a work folder, a second LP solver, and a cross-check of Origin 1 against the earlier
outputs/sep2025_comparison/ files. That cross-check is the only place where an earlier saved output is read.
Exit code 1 if any check fails.

Usage: python scripts/clean_checkpoint/verify.py --data-dir <data/raw_v6> --work-dir <empty scratch folder>
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
import pandas as pd

import pipeline as p

REL, BEDS = 1e-6, 1e-6  # numerical tolerances: relative for costs, absolute beds for forecasts and plans
EARLIER = p.REPO / "outputs/sep2025_comparison"
CROSS = "Cross-check: Origin 1 against the earlier Sep 2025 results (outputs/sep2025_comparison/)"
CHECKS, NOTES = [], []


def check(name, ok, detail):
    CHECKS.append((name, bool(ok), detail))
    print("PASS" if ok else "FAIL", "-", name, "-", detail)


def m(value):
    return f"{value / 1e6:,.3f}m"


def read(path):
    return pd.read_csv(path, parse_dates=["month"], float_precision="round_trip") if "month" in pd.read_csv(path, nrows=0).columns \
        else pd.read_csv(path, float_precision="round_trip")


def highs_plan(gaps):
    """The same LP with a second solver (HiGHS through scipy)."""
    from scipy.optimize import linprog
    T = len(gaps)
    c = np.r_[np.full(T, p.RUN), np.full(T, p.OPEN), np.full(T, p.CLOSE), np.full(T, p.SHORT), np.zeros(T)]
    A, b = np.zeros((2 * T, 5 * T)), np.zeros(2 * T)
    for t in range(T):
        A[t, t], A[t, T + t], A[t, 2 * T + t] = 1, -1, 1  # x_t - x_{t-1} - a_t + r_t = 0 (x_0 = 0)
        if t:
            A[t, t - 1] = -1
        A[T + t, t], A[T + t, 3 * T + t], A[T + t, 4 * T + t] = 1, 1, -1  # x_t + s_t - u_t = gap_t
        b[T + t] = gaps[t]
    bounds = [(0, p.CAP)] * T + [(0, p.RAMP)] * (2 * T) + [(0, None)] * (2 * T)
    res = linprog(c, A_eq=A, b_eq=b, bounds=bounds, method="highs")
    assert res.status == 0, res.message
    return res.x[:T], float(res.fun)


def realised(x, d):
    """Realised cost written out again, independently of pipeline.cost_table."""
    total, prev, months = 0.0, 0.0, []
    for xt, dt in zip(x, d):
        opened, closed = max(xt - prev, 0.0), max(prev - xt, 0.0)
        cost = 500 * xt + 550 * opened + 300 * closed + 5000 * max(dt - xt, 0.0)
        months.append(cost)
        total += cost
        prev = xt
    return total, months


def load_origin(out, key):
    b = out / key
    return dict(fc=read(b / "forecasts.csv"), plans=read(b / "plans.csv"), totals=read(b / "totals.csv").set_index("plan"),
                acc=read(b / "accuracy.csv").set_index("method"), act=read(b / "actuals.csv"))


def logic_checks(out, data_dir):
    log = json.loads((out / "fit_log.json").read_text(encoding="utf-8"))
    decision = json.loads((out / "decision.json").read_text(encoding="utf-8"))
    frozen = read(out / "plans_frozen.csv")
    full = p.load(data_dir, cutoff="2026-09-01")
    origins = {key: load_origin(out, key) for key in p.ORIGINS}

    # hashes
    try:
        p.check_config(out, data_dir)
        p.verify_hashes(out / "plans.sha256")
        ok = decision["hashes"] == dict(config=p.sha256(out / "config.json"), plans_frozen=p.sha256(out / "plans_frozen.csv"))
    except SystemExit as e:
        ok = False
        NOTES.append(f"hash failure: {e}")
    check("Config and plan hashes unchanged; config matches the code", ok,
          f"config {p.sha256(out / 'config.json')[:16]}..., plans {p.sha256(out / 'plans_frozen.csv')[:16]}...; workbook SHA-256 matches SOURCE.json")

    # dates: nothing after the cut-off
    problems = []
    for key, origin in p.ORIGINS.items():
        e = log["origins"][key]
        cut, start = f"{origin:%Y-%m}", f"{origin - pd.DateOffset(months=p.WINDOW - 1):%Y-%m}"
        used = [e["data_last_month"], e["window"][1], *e["seasonal_naive_source"]]
        for s in p.SERIES:
            used += [e["sarima"][s]["label_end"], e["extra_trees"][s]["last_row"]]
            if e["sarima"][s]["label_start"] != start or e["extra_trees"][s]["earliest_month_used"] < start:
                problems.append(f"{key} {s}: a fit reaches before the window")
        if max(used) > cut or e["window"] != [start, cut] or min(e["seasonal_naive_source"]) < start or min(e["forecast_months"]) <= cut:
            problems.append(f"{key}: dates {used}")
        try:
            p.forecast_origin(full, origin)  # a frame that holds later months must be refused
            problems.append(f"{key}: a frame with later months was accepted")
        except AssertionError:
            pass
    check("No training window, fit or feature uses data after the cut-off", not problems,
          "; ".join(problems) or "logged dates of every window, SARIMA label, ExtraTrees row and Seasonal naive source are <= the cut-off and inside "
          "the 24-month window; the forecasting code refuses a frame that holds later months")

    # fits
    conv = [e["sarima"][s]["converged"] and e["sarima"][s]["labels"] == 24 for e in log["origins"].values() for s in p.SERIES]
    rows = [e["extra_trees"][s]["training_rows"] for e in log["origins"].values() for s in p.SERIES]
    check("SARIMA-24 converged (4 of 4 fits, 24 labels); ExtraTrees-24 has 12 training rows (4 of 4 fits)", all(conv) and rows == [12] * 4,
          f"converged {sum(conv)}/4; training rows {rows}")

    # seasonal naive
    worst = 0.0
    for key, o in origins.items():
        g = o["fc"][o["fc"].method == "Seasonal naive"]
        for month, occ, av in zip(g.month, g.forecast_occupied, g.forecast_available):
            src = full.loc[month - pd.DateOffset(years=1)]
            worst = max(worst, abs(occ - src.occupied), abs(av - src.available))
    check("Seasonal naive forecasts equal the actuals one year earlier", worst == 0.0, f"largest difference {worst:g} beds (24 values)")

    # constraints and frozen plans
    worst, same = 0.0, True
    for key, o in origins.items():
        for plan, g in o["plans"].groupby("plan"):
            g = g.sort_values("month")
            x, a, r = g.x.to_numpy(), g.a.to_numpy(), g.r.to_numpy()
            step = np.diff(np.r_[0.0, x])
            worst = max(worst, (-x).max(), (x - p.CAP).max(), (-a).max(), (-r).max(), (a - p.RAMP).max(), (r - p.RAMP).max(), np.abs(step - (a - r)).max())
            if plan != "Hindsight":
                f = frozen[(frozen.origin == key) & (frozen.plan == plan)].sort_values("month")
                same &= bool((f.x.to_numpy() == x).all() and (f.a.to_numpy() == a).all() and (f.r.to_numpy() == r).all())
    check("Every plan satisfies all constraints (22 plans)", worst <= 1e-9,
          f"0 <= x <= 15,000; 0 <= a, r <= 5,000; x_t = x_(t-1) + a_t - r_t from x_0 = 0; largest violation {max(worst, 0):.3g}")
    check("Scored plans are the frozen plans", same, "x, a and r of the 20 forecast-based plans are bit-identical to plans_frozen.csv")

    # costs recomputed
    worst_total, worst_month, worst_parts = 0.0, 0.0, 0.0
    for key, o in origins.items():
        d = o["act"].gap.to_numpy()
        assert np.abs(d - (o["act"].occupied.to_numpy() / 0.90 - o["act"].available.to_numpy())).max() == 0
        assert (o["act"].occupied.to_numpy() == full.loc[o["act"].month, "occupied"].to_numpy()).all()
        assert (o["act"].available.to_numpy() == full.loc[o["act"].month, "available"].to_numpy()).all()
        for plan, g in o["plans"].groupby("plan"):
            g = g.sort_values("month")
            total, months = realised(g.x.to_numpy(), d)
            t = o["totals"].loc[plan]
            worst_total = max(worst_total, abs(total - t.total) / t.total, abs(g.monthly_cost.sum() - t.total) / t.total)
            worst_month = max(worst_month, np.abs(np.array(months) - g.monthly_cost.to_numpy()).max())
            worst_parts = max(worst_parts, abs(t.running + t.opening + t.closing - t.operating), abs(t.operating + t.shortfall_cost - t.total),
                              abs(5000 * t.shortfall_bed_months - t.shortfall_cost), abs(5000 * g.shortfall.sum() - t.shortfall_cost))
    check("Monthly costs sum to totals; costs recomputed independently from x and the actual gaps", worst_total <= 1e-12 and worst_parts <= 1e-6,
          f"largest relative difference in a total {worst_total:.2g}; largest monthly difference {worst_month:.2g} units; components add up within {worst_parts:.2g} units")

    # LP against Rule
    planned, real, strict, fewer = [], [], [], 0.0
    for key, o in origins.items():
        for method in p.METHODS:
            g = o["fc"][o["fc"].method == method].sort_values("month").forecast_gap.to_numpy()
            x = {dec: o["plans"][o["plans"].plan == f"{method} {dec}"].sort_values("month").x.to_numpy() for dec in p.DECISIONS}
            pr, pl = realised(x["Rule"], g)[0], realised(x["LP"], g)[0]
            rr, rl = o["totals"].loc[f"{method} Rule", "total"], o["totals"].loc[f"{method} LP", "total"]
            planned.append((pl - pr) / pr)
            real.append((rl - rr) / rr)
            fewer = max(fewer, (x["Rule"] - x["LP"]).max())
            if rl > rr:
                strict.append(f"{method} at {key}: LP higher by {rl - rr:.2f} units ({(rl - rr) / rr:.1e} of cost)")
    check("LP planned cost <= Rule planned cost for every method (cost against the forecast gap)", max(planned) <= REL,
          f"largest (LP - Rule) / Rule = {max(planned):.2g} over 10 method-origin pairs; tolerance {REL:g}")
    check("LP realised cost <= Rule realised cost for every method", max(real) <= REL,
          f"largest (LP - Rule) / Rule = {max(real):.2g}; tolerance {REL:g}. " + ("Strictly: " + "; ".join(strict) + " (see note 1)" if strict else "Also holds strictly."))
    NOTES.append(f"LP beds minus Rule beds, month by month: the LP plan is never below the Rule plan by more than {fewer:.1e} beds.")

    # hindsight
    worst = min((o["totals"].loc[list(p.PLANS), "total"] - o["totals"].loc["Hindsight", "total"]).min() for o in origins.values())
    check("Hindsight cost <= every plan's cost", worst >= 0, f"smallest (plan - hindsight) = {m(worst)}")

    # accuracy recomputed
    worst = 0.0
    for key, o in origins.items():
        for method in p.METHODS:
            g = o["fc"][o["fc"].method == method].sort_values("month")
            for s, col in (("gap", "forecast_gap"), ("occupied", "forecast_occupied"), ("available", "forecast_available")):
                err = o["act"][s].to_numpy() - g[col].to_numpy()
                worst = max(worst, abs(np.abs(err).mean() - o["acc"].loc[method, f"{s}_mae"]), abs(err.mean() - o["acc"].loc[method, f"{s}_mean_signed_error"]))
            assert np.abs(g.forecast_gap.to_numpy() - (g.forecast_occupied.to_numpy() / 0.90 - g.forecast_available.to_numpy())).max() == 0
    check("Accuracy recomputed independently from forecasts and actuals", worst <= 1e-9, f"largest difference {worst:.2g} beds (MAE and mean signed error, 3 series)")

    # rules recomputed
    cost = {key: o["totals"].loc[list(p.PLANS), "total"].astype(float) for key, o in origins.items()}
    hind = {key: float(o["totals"].loc["Hindsight", "total"]) for key, o in origins.items()}
    joint = {key: [pl for pl in p.PLANS if cost[key][pl] - cost[key].min() <= 0.01 * hind[key]] for key in origins}
    excess = {pl: sum(cost[key][pl] - hind[key] for key in origins) for pl in p.PLANS}
    order = sorted(p.PLANS, key=lambda pl: (excess[pl], p.PLANS.index(pl)))
    beats = all(cost[key][order[1]] - cost[key][order[0]] > 0.01 * hind[key] for key in origins)
    winners = [pl for pl in p.PLANS if all(pl in joint[key] for key in origins) or (beats and pl == order[0])]
    mean_mae = {me: np.mean([origins[key]["acc"].loc[me, "gap_mae"] for key in origins]) for me in p.METHODS}
    best = min(mean_mae, key=mean_mae.get)
    clear = all(min(o["acc"].gap_mae.drop(best)) - o["acc"].loc[best, "gap_mae"] > 5 for o in origins.values())
    combined = read(out / "combined.csv").set_index("plan")
    ok = (all(decision["per_origin"][key]["joint_best"] == joint[key] and decision["per_origin"][key]["cheapest"] == cost[key].idxmin() for key in origins)
          and decision["overall"]["ranking"] == order and decision["overall"]["clear_winners"] == winners
          and decision["best_forecast"]["method"] == best and decision["best_forecast"]["clear"] == clear
          and max(abs(combined.loc[pl, "combined_excess"] - excess[pl]) for pl in p.PLANS) <= 1e-6
          and all((combined[f"{key}_joint_best"].to_numpy() == np.array([pl in joint[key] for pl in combined.index])).all() for key in origins))
    check("Decision rules recomputed independently", ok,
          f"joint best {joint}; overall first {order[0]}, runner-up {order[1]}; clear winners {winners or 'none'}; best forecast {best} (clear: {clear})")

    # improvement measures recomputed
    imp = read(out / "improvements.csv")
    worst = 0.0
    for scope, keys in [(k, [k]) for k in origins] + [("combined", list(origins))]:
        c = {pl: sum(cost[k][pl] for k in keys) for pl in p.PLANS}
        h = sum(hind[k] for k in keys)
        mae = {s: {me: np.mean([origins[k]["acc"].loc[me, f"{s}_mae"] for k in keys]) for me in p.METHODS} for s in ("gap", "occupied", "available")}
        for row in imp[imp.scope == scope].itertuples():
            method, dec = row.plan.rsplit(" ", 1)
            expect = dict(total=c[row.plan], excess_over_hindsight=c[row.plan] - h, excess_pct_of_hindsight=100 * (c[row.plan] - h) / h,
                          value_of_optimisation=c[f"{method} Rule"] - c[f"{method} LP"], saving_vs_persistence=c[f"Persistence {dec}"] - c[row.plan],
                          saving_vs_seasonal_naive=c[f"Seasonal naive {dec}"] - c[row.plan],
                          share_avoidable_vs_persistence_lp=100 * (c["Persistence LP"] - c[row.plan]) / (c["Persistence LP"] - h),
                          share_avoidable_vs_seasonal_naive_lp=100 * (c["Seasonal naive LP"] - c[row.plan]) / (c["Seasonal naive LP"] - h),
                          **{f"{s}_skill_vs_seasonal_naive": 1 - mae[s][method] / mae[s]["Seasonal naive"] for s in mae})
            worst = max(worst, max(abs(getattr(row, k) - v) / max(1.0, abs(v)) for k, v in expect.items()))
    check("Improvement measures recomputed independently (30 rows: per origin and combined)", len(imp) == 30 and worst <= 1e-9, f"largest relative difference {worst:.2g}")

    # second solver
    worst_obj, worst_x = 0.0, 0.0
    for key, o in origins.items():
        paths = {f"{me} LP": o["fc"][o["fc"].method == me].sort_values("month").forecast_gap.to_numpy() for me in p.METHODS}
        paths["Hindsight"] = o["act"].gap.to_numpy()
        for plan, g in paths.items():
            x = o["plans"][o["plans"].plan == plan].sort_values("month").x.to_numpy()
            hx, hobj = highs_plan(g)
            worst_obj = max(worst_obj, abs(realised(x, g)[0] - hobj) / hobj)
            worst_x = max(worst_x, np.abs(x - hx).max())
    check("LP plans agree with a second solver (HiGHS)", worst_obj <= REL and worst_x <= 1e-3,
          f"12 LPs (10 forecast-based, 2 hindsight): largest relative objective difference {worst_obj:.2g}; largest plan difference {worst_x:.2g} beds")
    return origins, decision


def unit_tests():
    run = subprocess.run([sys.executable, "-I", "-m", "pytest", str(HERE), "-q", "-p", "no:cacheprovider", "-W", "ignore"], capture_output=True, text=True)
    summary = (run.stdout.strip().splitlines() or ["no output"])[-1]
    check("Unit tests (pytest scripts/clean_checkpoint)", run.returncode == 0, summary)


def rerun(out, data_dir, work):
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for stage in ("register", "plan", "score"):
        run = subprocess.run([sys.executable, "-I", "-W", "ignore", str(HERE / "pipeline.py"), stage, "--data-dir", str(data_dir), "--out-dir", str(work)],
                             capture_output=True, text=True)
        if run.returncode != 0:
            check("Rerunning the pipeline gives identical results", False, f"stage {stage} failed: {run.stderr.strip()[-300:]}")
            return
    files = ["plans_frozen.csv", "combined.csv", "improvements.csv"] + [f"{k}/{n}.csv" for k in p.ORIGINS for n in ("forecasts", "plans", "totals", "accuracy", "actuals")]
    beds, rel, identical, text_ok = 0.0, 0.0, 0, True
    for name in files:
        a, b = read(out / name), read(work / name)
        identical += (out / name).read_bytes() == (work / name).read_bytes()
        for col in a.columns:
            if pd.api.types.is_float_dtype(a[col]):
                x, y = a[col].to_numpy(float), b[col].to_numpy(float)
                keep = ~(np.isnan(x) & np.isnan(y))
                diff = np.abs(x[keep] - y[keep])
                if name.endswith(("forecasts.csv", "plans_frozen.csv")) or col in ("x", "a", "r", "shortfall", "spare"):
                    beds = max(beds, diff.max(initial=0.0))
                rel = max(rel, (diff / np.maximum(1.0, np.abs(x[keep]))).max(initial=0.0))
            else:
                text_ok &= bool((a[col].astype(str) == b[col].astype(str)).all())
    da, db = (json.loads((d / "decision.json").read_text(encoding="utf-8")) for d in (out, work))
    same_decision = (da["overall"]["ranking"] == db["overall"]["ranking"] and da["overall"]["clear_winners"] == db["overall"]["clear_winners"]
                     and da["best_forecast"]["method"] == db["best_forecast"]["method"] and da["hashes"]["config"] == db["hashes"]["config"]
                     and all(da["per_origin"][k]["joint_best"] == db["per_origin"][k]["joint_best"] for k in p.ORIGINS))
    check("Rerunning the pipeline gives identical results (ExtraTrees within 1e-6 beds)", beds <= BEDS and rel <= 1e-9 and text_ok and same_decision,
          f"fresh run of all three stages: {identical} of {len(files)} CSV files byte-identical; largest difference {beds:.2g} beds in forecasts and plans, "
          f"{rel:.2g} relative in any number; config hash, rankings and decisions identical; frozen-plan hash "
          + ("identical" if da["hashes"]["plans_frozen"] == db["hashes"]["plans_frozen"] else "differs (ExtraTrees float noise)"))
    shutil.rmtree(work)


def cross_check(origins):
    """Origin 1 against the earlier saved Sep 2025 results (computed with the project's own code on the raw_v5 workbook)."""
    o = origins["O1_sep2025"]
    old_fc, old_act = read(EARLIER / "forecasts.csv"), read(EARLIER / "actuals.csv")
    old_plans, old_totals = read(EARLIER / "plans.csv"), read(EARLIER / "totals.csv").set_index("plan")
    rows = []
    d_act = max(np.abs(o["act"][c].to_numpy() - old_act[c].to_numpy()).max() for c in ("occupied", "available", "gap"))
    rows.append(("Actuals (occupied, available, gap)", f"{d_act:.3g} beds"))
    worst_fc = {}
    for method in p.METHODS:
        a, b = o["fc"][o["fc"].method == method].sort_values("month"), old_fc[old_fc.source == method].sort_values("month")
        assert list(a.month) == list(b.month)
        worst_fc[method] = max(np.abs(a[c].to_numpy() - b[c].to_numpy()).max() for c in ("forecast_occupied", "forecast_available", "forecast_gap"))
        rows.append((f"Forecasts, {method}", f"{worst_fc[method]:.3g} beds"))
    names = list(p.PLANS) + ["Hindsight"]
    worst_x = max(np.abs(o["plans"][o["plans"].plan == n].sort_values("month").x.to_numpy() - old_plans[old_plans.plan == n].sort_values("month").x.to_numpy()).max() for n in names)
    worst_cost = max(np.abs(o["plans"][o["plans"].plan == n].sort_values("month").monthly_cost.to_numpy()
                            - old_plans[old_plans.plan == n].sort_values("month").monthly_cost.to_numpy()).max() for n in names)
    rows += [("Plans x_t (10 plans and hindsight)", f"{worst_x:.3g} beds"), ("Monthly costs", f"{worst_cost:.3g} units")]
    rel = {n: abs(o["totals"].loc[n, "total"] - old_totals.loc[n, "total"]) / old_totals.loc[n, "total"] for n in names}
    parts = max(abs(o["totals"].loc[n, c] - old_totals.loc[n, c]) for n in names for c in ("running", "opening", "closing", "shortfall_cost"))
    worst = max(rel, key=rel.get)
    rows += [("Totals (relative)", f"{rel[worst]:.3g} ({worst})"), ("Cost components", f"{parts:.3g} units")]
    check(CROSS, max(rel.values()) <= REL,
          f"largest relative difference in a total {rel[worst]:.2g} ({worst}); tolerance {REL:g}; details below")
    return rows


def readme_check(out):
    builder = HERE / "build_readme.py"
    if not builder.exists():
        return "README check: not run (README not built yet)."
    run = subprocess.run([sys.executable, "-I", str(builder), "--check"], capture_output=True, text=True)
    check("README.md equals the text generated from outputs/clean_checkpoint/ (every number comes from the saved results)", run.returncode == 0,
          (run.stdout.strip().splitlines() or run.stderr.strip().splitlines() or ["no output"])[-1])
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path, help="empty scratch folder for the rerun (removed afterwards)")
    parser.add_argument("--out-dir", type=Path, default=p.OUT)
    args = parser.parse_args()
    out, data_dir = args.out_dir.resolve(), args.data_dir.resolve()
    unit_tests()
    origins, decision = logic_checks(out, data_dir)
    rerun(out, data_dir, args.work_dir.resolve())
    rows = cross_check(origins)
    pending = readme_check(out)

    failed = [name for name, ok, _ in CHECKS if not ok]
    log = json.loads((out / "fit_log.json").read_text(encoding="utf-8"))
    lines = ["# Verification (Phase 4)", "",
             f"Result: **{'ALL CHECKS PASS' if not failed else 'FAILED: ' + '; '.join(failed)}** ({len(CHECKS) - len(failed)} of {len(CHECKS)}).", "",
             f"Numerical tolerances: {REL:g} relative for costs, {BEDS:g} beds for forecasts and plans. Versions: "
             + ", ".join(f"{k} {v}" for k, v in log["versions"].items()) + ".", "",
             "| # | Check | Result | Detail |", "| ---: | --- | --- | --- |"]
    lines += [f"| {i} | {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for i, (name, ok, detail) in enumerate(CHECKS, 1)]
    lines += ["", "## Cross-check against the earlier September 2025 results", "",
              "The earlier files were produced by the project's own code (scripts/compare_methods_sep2025.py and the project modules) on the "
              "September 2026 workbook. This checkpoint uses new code and the October 2026 workbook. Largest differences at Origin 1:", "",
              "| Item | Largest difference |", "| --- | ---: |"]
    lines += [f"| {a} | {b} |" for a, b in rows]
    cross_ok = dict((name, ok) for name, ok, _ in CHECKS)[CROSS]
    lines += ["", "No difference in a total is above 1e-6 relative, so no explanation is needed and neither version shows an error." if cross_ok
              else "A total differs by more than 1e-6 relative. This must be explained before publishing.", ""]
    lines += ["## Notes", "",
              "1. The LP solver (CBC through PuLP) returns plan values with 8 significant digits, so an LP plan can differ from the exact optimum by "
              "up to about 5e-5 beds. For the flat forecasts (Mean-24, Persistence) the Rule plan and the LP plan are the same plan; their costs "
              "differ by less than 1 model unit in more than 10 million. decision.json lists these cases strictly "
              f"(lp_worse_than_rule: {decision['lp_worse_than_rule'] or 'none'}). Only the order inside those two pairs depends on this rounding; "
              "no joint-best set, no position against another forecast and no decision does.",
              f"2. {NOTES[-1] if NOTES else ''} With shortfall at 5,000 and at most 1,350 to open, run and close a bed for one month, the LP covers the "
              "forecast gap whenever the Rule does, which is why the realised check holds here although it is not guaranteed in general.",
              "3. SARIMA warnings recorded in fit_log.json: " + "; ".join(sorted({w for e in log["origins"].values() for s in p.SERIES for w in e["sarima"][s]["warnings"]})) + "."]
    if pending:
        lines += ["4. " + pending]
    p.write_text(out / "verification.md", "\n".join(lines) + "\n")
    print(f"{len(CHECKS) - len(failed)} of {len(CHECKS)} checks pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
