"""Phase 4 verification of the fair comparison. Writes outputs/fair_comparison/verification.md with PASS/FAIL per check.

Unit tests, leakage checks on dates, reproduction of the clean-checkpoint point plans, logic checks with costs, losses
and rules recomputed by separate code, a second LP solver, and a full rerun in a work folder. Exit code 1 on any FAIL.

Usage: python scripts/fair_comparison/verify.py --data-dir <data/raw_v6> --work-dir <empty scratch folder>
"""

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
import pandas as pd

import fair as f

cc = f.cc
REL, BEDS, PLAN_BEDS = 1e-6, 1e-6, 1e-3
CHECKPOINT = f.REPO / "outputs/clean_checkpoint"
CHECKS = []


def check(name, ok, detail):
    CHECKS.append((name, bool(ok), detail))
    print("PASS" if ok else "FAIL", "-", name, "-", detail)


def read(path):
    cols = pd.read_csv(path, nrows=0).columns
    return pd.read_csv(path, parse_dates=["month"] if "month" in cols else None, float_precision="round_trip")


def cost(x, d, p):
    """Realised cost written out again, independently of the pipeline."""
    total, prev, months = 0.0, 0.0, []
    for xt, dt in zip(x, d):
        c = 500 * xt + 550 * max(xt - prev, 0.0) + 300 * max(prev - xt, 0.0) + p * max(dt - xt, 0.0)
        months.append(c)
        total += c
        prev = xt
    return total, months


def highs(scen, p, required=None):
    """The SAA program (or the service-level program when `required` is given) with HiGHS through scipy."""
    from scipy.optimize import linprog
    scen = np.atleast_2d(scen)
    N, T = scen.shape
    n = 3 * T + N * T
    c = np.r_[np.full(T, 500.0 + (0 if required is not None else f.TIE)), np.full(T, 550.0), np.full(T, 300.0), np.full(N * T, 0.0 if required is not None else p / N)]
    A_eq = np.zeros((T, n))
    for t in range(T):
        A_eq[t, t], A_eq[t, T + t], A_eq[t, 2 * T + t] = 1, -1, 1
        if t:
            A_eq[t, t - 1] = -1
    A_ub, b_ub = np.zeros((N * T, n)), np.zeros(N * T)
    for i in range(N):
        for t in range(T):
            A_ub[i * T + t, t], A_ub[i * T + t, 3 * T + i * T + t], b_ub[i * T + t] = -1, -1, -scen[i, t]
    lower = required if required is not None else np.zeros(T)
    bounds = [(lower[t], cc.CAP) for t in range(T)] + [(0, cc.RAMP)] * (2 * T) + [(0, None)] * (N * T)
    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=np.zeros(T), bounds=bounds, method="highs")
    assert res.status == 0, res.message
    return res.x[:T]


def load_origin(out, key):
    b = out / key
    return {n: read(b / f"{n}.csv") for n in ("forecasts", "backtests", "scenarios", "quantiles", "service_levels", "bank_summary", "plans", "totals", "accuracy", "actuals")}


def plan_x(o, name):
    return o["plans"][o["plans"].plan == name].sort_values("month").x.to_numpy()


def logic_checks(out, data_dir):
    decision = json.loads((out / "decision.json").read_text(encoding="utf-8"))
    log = json.loads((out / "fit_log.json").read_text(encoding="utf-8"))
    frozen = read(out / "plans_frozen.csv")
    full = cc.load(data_dir, cutoff="2026-09-01")
    origins = {key: load_origin(out, key) for key in f.ORIGINS}

    try:
        f.check_config(out, data_dir)
        cc.verify_hashes(out / "plans.sha256")
        ok = decision["hashes"] == dict(config=cc.sha256(out / "config.json"), plans_frozen=cc.sha256(out / "plans_frozen.csv"))
    except SystemExit:
        ok = False
    check("Config and plan hashes unchanged; config matches the code", ok,
          f"config {cc.sha256(out / 'config.json')[:16]}..., plans {cc.sha256(out / 'plans_frozen.csv')[:16]}...; workbook SHA-256 matches SOURCE.json")

    # leakage: dates
    problems, counts = [], {}
    for key, origin in f.ORIGINS.items():
        bank = origins[key]["backtests"]
        b, start = pd.to_datetime(bank.backtest_origin), pd.to_datetime(bank.window_start)
        counts[key] = bank.backtest_origin.nunique()
        if not ((bank.month <= origin).all() and (bank.month > b).all() and (b + pd.DateOffset(months=6) <= origin).all()
                and (start >= full.index[0]).all() and (start == b - pd.DateOffset(months=23)).all()):
            problems.append(f"{key}: a backtest date is out of range")
        if log[key]["data_last_month"] != f"{origin:%Y-%m}" or log[key]["paths"] != counts[key] or counts[key] + len(log[key]["dropped"]) != len(f.backtest_origins(full.index[0], origin)):
            problems.append(f"{key}: log does not match the bank")
        try:
            f.build_bank(full, origin)  # a frame that holds months after the origin must be refused
            problems.append(f"{key}: a frame with later months was accepted")
        except AssertionError:
            pass
        if (origins[key]["forecasts"].month <= origin).any():
            problems.append(f"{key}: a forecast month is not after the origin")
    check("Leakage: every backtest target month and every training month is at or before the origin", not problems,
          "; ".join(problems) or f"{counts} error paths; all targets <= origin, all 24-month windows end at the backtest origin and start on or after "
          f"{full.index[0]:%b %Y}; the bank builder refuses a frame that holds later months")

    # backtests recomputed for the three closed-form methods; actuals from the workbook
    worst = 0.0
    for key, o in origins.items():
        bank = o["backtests"]
        for series in ("occupied", "available"):
            worst = max(worst, np.abs(bank[f"actual_{series}"].to_numpy() - full.loc[bank.month, series].to_numpy()).max())
            g = bank[bank.method == "Persistence"]
            worst = max(worst, np.abs(g[f"forecast_{series}"].to_numpy() - full.loc[pd.to_datetime(g.backtest_origin), series].to_numpy()).max())
            g = bank[bank.method == "Seasonal naive"]
            worst = max(worst, np.abs(g[f"forecast_{series}"].to_numpy() - full.loc[g.month - pd.DateOffset(years=1), series].to_numpy()).max())
            g = bank[bank.method == "Mean-24"]
            means = [full.loc[pd.Timestamp(s):pd.Timestamp(b), series].mean() for s, b in zip(g.window_start, g.backtest_origin)]
            worst = max(worst, np.abs(g[f"forecast_{series}"].to_numpy() - np.array(means)).max())
        worst = max(worst, np.abs(bank.error_gap - ((bank.actual_occupied - bank.forecast_occupied) / 0.90 - (bank.actual_available - bank.forecast_available))).max())
    check("Backtests recomputed independently (Persistence, Seasonal naive, Mean-24; actuals; paired gap errors)", worst <= 1e-9, f"largest difference {worst:.2g} beds")

    # scenarios and quantiles
    worst = 0.0
    for key, o in origins.items():
        n = o["backtests"].backtest_origin.nunique()
        for m in f.METHODS:
            point = o["forecasts"][o["forecasts"].method == m].sort_values("month")
            s = o["scenarios"][o["scenarios"].method == m]
            err = o["backtests"][o["backtests"].method == m].set_index(["backtest_origin", "horizon"]).error_gap
            pt = point.set_index("month").forecast_gap
            expect = pt.loc[s.month].to_numpy() + err.loc[list(zip(s.backtest_origin, s.horizon))].to_numpy()
            worst = max(worst, np.abs(s.scenario_gap.to_numpy() - expect).max())
            for q in f.LEVELS:
                k = min(n, math.ceil(round((n + 1) * q, 9)))
                qs = o["quantiles"][(o["quantiles"].method == m) & np.isclose(o["quantiles"].level, q)].sort_values("month")
                for month, value in zip(qs.month, qs.quantile_gap):
                    worst = max(worst, abs(value - sorted(s[s.month == month].scenario_gap)[k - 1]))
    check("Scenarios are the point forecast plus the method's own error paths; quantiles follow the rank rule", worst <= 1e-9,
          f"largest difference {worst:.2g} beds; ranks " + "; ".join(f"{k}: {log[k]['ranks']}" for k in origins))

    # point plans reproduce the clean checkpoint
    old_plans = read(CHECKPOINT / "plans_frozen.csv")
    exact, et = 0.0, 0.0
    for key, o in origins.items():
        old_fc = read(CHECKPOINT / key / "forecasts.csv")
        for m in f.METHODS:
            new = frozen[(frozen.origin == key) & (frozen.plan == f.plan_id(m, "point", f.BASE))].sort_values("month").x.to_numpy()
            old = old_plans[(old_plans.origin == key) & (old_plans.plan == f"{m} LP")].sort_values("month").x.to_numpy()
            dfc = max(np.abs(o["forecasts"][o["forecasts"].method == m][c].to_numpy() - old_fc[old_fc.method == m][c].to_numpy()).max()
                      for c in ("forecast_occupied", "forecast_available", "forecast_gap"))
            diff = max(np.abs(new - old).max(), dfc)
            if m == "ExtraTrees-24":
                et = max(et, diff)
            else:
                exact = max(exact, diff)
    check("With protection removed, point forecasts and LP plans reproduce the clean checkpoint", exact == 0.0 and et <= BEDS,
          f"Mean-24, Persistence, Seasonal naive, SARIMA-24: identical (difference {exact:g}); ExtraTrees-24: {et:.2g} beds (allowed 1e-6)")

    # plans: frozen = scored, feasible, constraints
    worst, same, n_plans = 0.0, True, 0
    for key, o in origins.items():
        for name, g in o["plans"].groupby("plan"):
            g = g.sort_values("month")
            x, a, r = g.x.to_numpy(), g.a.to_numpy(), g.r.to_numpy()
            worst = max(worst, (-x).max(), (x - cc.CAP).max(), (-a).max(), (-r).max(), (a - cc.RAMP).max(), (r - cc.RAMP).max(), np.abs(np.diff(np.r_[0.0, x]) - (a - r)).max())
            n_plans += 1
            if not name.startswith("Hindsight"):
                fr = frozen[(frozen.origin == key) & (frozen.plan == name)].sort_values("month")
                same &= bool((fr.x.to_numpy() == x).all())
    check(f"All plans feasible ({n_plans} plans); scored plans are the frozen plans", worst <= 1e-9 and same,
          f"0 <= x <= 15,000; ramps <= 5,000; x_t = x_(t-1) + a_t - r_t from zero; largest violation {max(worst, 0):.2g}; frozen x bit-identical")

    # costs recomputed; hindsight floor
    worst_total, worst_sum, floor = 0.0, 0.0, np.inf
    for key, o in origins.items():
        d = o["actuals"].gap.to_numpy()
        assert (o["actuals"].occupied.to_numpy() == full.loc[o["actuals"].month, "occupied"].to_numpy()).all()
        assert np.abs(d - (o["actuals"].occupied.to_numpy() / 0.90 - o["actuals"].available.to_numpy())).max() == 0
        for row in o["totals"].itertuples():
            g = o["plans"][o["plans"].plan == row.plan].sort_values("month")
            total, _ = cost(g.x.to_numpy(), d, row.scored_at)
            worst_total = max(worst_total, abs(total - row.total) / row.total)
            worst_sum = max(worst_sum, abs(g.monthly_cost.sum() - row.total) / row.total)
            if row.kind != "hindsight":
                floor = min(floor, (row.total - row.hindsight) / row.hindsight)
    check("Monthly costs sum to totals; costs recomputed independently at each plan's penalty", worst_total <= 1e-12 and worst_sum <= 1e-12,
          f"largest relative difference {max(worst_total, worst_sum):.2g}")
    check("Hindsight cost <= every plan's cost (each penalty level has its own hindsight)", floor >= -REL, f"smallest (plan - hindsight) / hindsight = {floor:.3g}")

    # SAA no worse than point on its own scenarios; service level met
    worst_saa, worst_sl, lowered = -np.inf, -np.inf, 0
    for key, o in origins.items():
        for m in f.METHODS:
            scen = o["scenarios"][o["scenarios"].method == m].pivot(index="backtest_origin", columns="horizon", values="scenario_gap").to_numpy()
            for p in f.PENALTIES:
                saa, pt = plan_x(o, f.plan_id(m, "protected", p)), plan_x(o, f.plan_id(m, "point", p))
                avg = lambda x: np.mean([cost(x, g, p)[0] for g in scen])
                worst_saa = max(worst_saa, (avg(saa) - avg(pt)) / avg(pt))
            for alpha in f.ALPHAS:
                req = o["service_levels"][(o["service_levels"].method == m) & np.isclose(o["service_levels"].alpha, alpha)].sort_values("month")
                worst_sl = max(worst_sl, (req.required.to_numpy() - plan_x(o, f.plan_id(m, "service_level", alpha=alpha))).max())
                lowered += int(req.lowered.sum())
    check("Protected plan is never worse than the point plan on its own scenarios (40 pairs)", worst_saa <= REL, f"largest (SAA - point) / point average scenario cost = {worst_saa:.3g}")
    check("Service-level plans meet the required quantile every month (20 plans)", worst_sl <= PLAN_BEDS,
          f"largest shortfall against the requirement {max(worst_sl, 0):.2g} beds; months where the requirement was lowered to the reachable level: {lowered}")

    # second solver
    worst_obj, worst_x, n_lp = 0.0, 0.0, 0
    for key, o in origins.items():
        d = o["actuals"].gap.to_numpy()
        for m in f.METHODS:
            point = o["forecasts"][o["forecasts"].method == m].sort_values("month").forecast_gap.to_numpy()
            s = o["scenarios"][o["scenarios"].method == m]
            scen = s.pivot(index="backtest_origin", columns="horizon", values="scenario_gap").to_numpy()
            err = s.pivot(index="backtest_origin", columns="horizon", values="error_gap").to_numpy()
            jobs = [(f.plan_id(m, "protected", p), scen, p, None) for p in f.PENALTIES] + [(f.plan_id(m, "point", p), point[None, :], p, None) for p in f.PENALTIES]
            jobs += [(f.plan_id(m, "protected_centred", f.BASE), point[None, :] + err - err.mean(axis=0), f.BASE, None),
                     (f.plan_id(m, "bias_corrected", f.BASE), (point + err.mean(axis=0))[None, :], f.BASE, None)]
            for alpha in f.ALPHAS:
                req = o["service_levels"][(o["service_levels"].method == m) & np.isclose(o["service_levels"].alpha, alpha)].sort_values("month").required.to_numpy()
                jobs.append((f.plan_id(m, "service_level", alpha=alpha), scen, 0.0, req))
            for name, sc, p, req in jobs:
                x, hx = plan_x(o, name), highs(sc, p, req)
                avg = lambda v: np.mean([cost(v, g, p)[0] for g in sc])
                worst_obj, worst_x, n_lp = max(worst_obj, abs(avg(x) - avg(hx)) / avg(hx)), max(worst_x, np.abs(x - hx).max()), n_lp + 1
        for p in f.PENALTIES:
            x, hx = plan_x(o, f"Hindsight | p={p:g}"), highs(d[None, :], p)
            worst_obj, worst_x, n_lp = max(worst_obj, abs(cost(x, d, p)[0] - cost(hx, d, p)[0]) / cost(hx, d, p)[0]), max(worst_x, np.abs(x - hx).max()), n_lp + 1
    check(f"All plans agree with a second solver (HiGHS; {n_lp} programs)", worst_obj <= REL and worst_x <= PLAN_BEDS,
          f"largest relative objective difference {worst_obj:.2g}; largest plan difference {worst_x:.2g} beds")

    # accuracy recomputed
    worst = 0.0
    for key, o in origins.items():
        d = o["actuals"].gap.to_numpy()
        acc = o["accuracy"].set_index("method")
        for m in f.METHODS:
            point = o["forecasts"][o["forecasts"].method == m].sort_values("month").forecast_gap.to_numpy()
            q9 = o["quantiles"][(o["quantiles"].method == m) & np.isclose(o["quantiles"].level, 0.9)].sort_values("month").quantile_gap.to_numpy()
            loss = lambda fc, q: float(np.mean([q * (y - v) if y >= v else (1 - q) * (v - y) for y, v in zip(d, fc)]))
            scen = o["scenarios"][o["scenarios"].method == m].pivot(index="backtest_origin", columns="horizon", values="scenario_gap").to_numpy()
            crps = np.mean([np.mean(np.abs(scen[:, t] - d[t])) - 0.5 * np.mean(np.abs(scen[:, t][:, None] - scen[:, t][None, :])) for t in range(len(d))])
            expect = dict(gap_mae=np.abs(d - point).mean(), gap_mean_signed_error=(d - point).mean(), pinball_090=loss(q9, 0.9), pinball_090_point=loss(point, 0.9),
                          covered_090=sum(y <= v for y, v in zip(d, q9)), mean_margin_090=(q9 - point).mean(), crps=crps)
            for q, col in ((0.5, "pinball_050"), (0.8, "pinball_080"), (0.95, "pinball_095")):
                qs = o["quantiles"][(o["quantiles"].method == m) & np.isclose(o["quantiles"].level, q)].sort_values("month").quantile_gap.to_numpy()
                expect[col] = loss(qs, q)
            worst = max(worst, max(abs(acc.loc[m, k] - v) for k, v in expect.items()))
    check("Gap MAE, pinball loss, coverage and CRPS recomputed independently", worst <= 1e-9, f"largest difference {worst:.2g} beds")

    # rules recomputed
    def tables(kind, p):
        t = {key: o["totals"][(o["totals"].kind == kind) & np.isclose(o["totals"].scored_at, p)].set_index("method").total for key, o in origins.items()}
        h = {key: float(o["totals"][(o["totals"].kind == "hindsight") & np.isclose(o["totals"].scored_at, p)].total.iloc[0]) for key, o in origins.items()}
        return t, h

    def outcome(kind, p):
        t, h = tables(kind, p)
        joint = {k: [m for m in f.METHODS if t[k][m] - t[k].min() <= 0.01 * h[k]] for k in origins}
        excess = {m: sum(t[k][m] - h[k] for k in origins) for m in f.METHODS}
        order = sorted(f.METHODS, key=lambda m: (excess[m], f.METHODS.index(m)))
        beats = all(t[k][order[1]] - t[k][order[0]] > 0.01 * h[k] for k in origins)
        winners = [m for m in f.METHODS if all(m in joint[k] for k in origins) or (beats and m == order[0])]
        return joint, order, winners, " and ".join(winners) or "no clear winner"

    joint, order, winners, label = outcome("protected", f.BASE)
    pt, h = tables("point", f.BASE)
    pr, _ = tables("protected", f.BASE)
    ok = decision["overall"]["ranking"] == order and decision["overall"]["clear_winners"] == winners and decision["overall"]["outcome"] == label
    for key in origins:
        e = decision["per_origin"][key]
        prev = pt[key][list(f.METHODS)].idxmin()
        ok &= e["joint_best_protected"] == joint[key] and e["previous_winner"]["method"] == prev and e["previous_winner"]["still_wins"] == (prev in joint[key])
        ok &= e["previous_winner"]["protected_rank"] == 1 + sum(pr[key][m] < pr[key][prev] for m in f.METHODS)
        for m in f.METHODS:
            diff, band = pt[key][m] - pr[key][m], 0.01 * h[key]
            ok &= e["protection"][m]["effect"] == ("helped" if diff > band else "hurt" if diff < -band else "no material difference")
    sweep = {f"{p:g}": outcome("protected", p) for p in f.PENALTIES}
    ok &= all(decision["penalty_sweep"][k]["outcome"] == v[3] and decision["penalty_sweep"][k]["ranking"] == v[1] for k, v in sweep.items())
    ok &= decision["robust_to_penalty"] == all(v[3] == label for v in sweep.values())
    ok &= decision["centred_sensitivity"]["outcome"] == outcome("protected_centred", f.BASE)[3]
    pin = {m: np.mean([origins[k]["accuracy"].set_index("method").loc[m, "pinball_090"] for k in origins]) for m in f.METHODS}
    best = min(pin, key=pin.get)
    clear = all(min(o["accuracy"].set_index("method").pinball_090.drop(best)) - o["accuracy"].set_index("method").loc[best, "pinball_090"] > 5 for o in origins.values())
    ok &= decision["best_calibrated"]["method"] == best and decision["best_calibrated"]["clear"] == clear
    check("Decision rules recomputed independently (winner, previous winner, protection effect, sweep, calibration)", ok,
          f"protected joint best {joint}; overall order {order}; outcome: {label}; sweep outcomes " + ", ".join(f"p={k}: {v[3]}" for k, v in sweep.items())
          + f"; best calibrated {best} (clear: {clear})")

    # improvements recomputed
    imp = read(out / "improvements.csv")
    bc, _ = tables("bias_corrected", f.BASE)
    worst = 0.0
    for scope, keys in [(k, [k]) for k in origins] + [("combined", list(origins))]:
        P, B, R = ({m: sum(t[k][m] for k in keys) for m in f.METHODS} for t in (pt, bc, pr))
        H = sum(h[k] for k in keys)
        loss = {n: {m: np.mean([origins[k]["accuracy"].set_index("method").loc[m, n] for k in keys]) for m in f.METHODS} for n in ("pinball_090", "gap_mae")}
        for row in imp[imp.scope == scope].itertuples():
            m = row.method
            expect = dict(point=P[m], bias_corrected=B[m], protected=R[m], value_of_protection=P[m] - R[m], value_of_bias_correction=P[m] - B[m], value_of_margin=B[m] - R[m],
                          saving_vs_seasonal_naive=R["Seasonal naive"] - R[m], saving_vs_persistence=R["Persistence"] - R[m],
                          share_avoidable_vs_seasonal_naive=100 * (R["Seasonal naive"] - R[m]) / (R["Seasonal naive"] - H),
                          share_avoidable_vs_persistence=100 * (R["Persistence"] - R[m]) / (R["Persistence"] - H),
                          excess_over_hindsight=R[m] - H, excess_pct_of_hindsight=100 * (R[m] - H) / H,
                          pinball_090_skill_vs_seasonal_naive=1 - loss["pinball_090"][m] / loss["pinball_090"]["Seasonal naive"],
                          gap_mae_skill_vs_seasonal_naive=1 - loss["gap_mae"][m] / loss["gap_mae"]["Seasonal naive"])
            worst = max(worst, max(abs(getattr(row, k) - v) / max(1.0, abs(v)) for k, v in expect.items()))
    check("Improvement measures recomputed independently (15 rows: per origin and combined)", len(imp) == 15 and worst <= 1e-9, f"largest relative difference {worst:.2g}")
    return decision, log


def unit_tests():
    run = subprocess.run([sys.executable, "-I", "-m", "pytest", str(HERE), "-q", "-p", "no:cacheprovider", "-W", "ignore"], capture_output=True, text=True)
    check("Unit tests (pytest scripts/fair_comparison): scenarios, pinball loss, service level, SAA", run.returncode == 0, (run.stdout.strip().splitlines() or ["no output"])[-1])


def rerun(out, data_dir, work):
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for stage in ("register", "plan", "score"):
        run = subprocess.run([sys.executable, "-I", "-W", "ignore", str(HERE / "fair.py"), stage, "--data-dir", str(data_dir), "--out-dir", str(work)], capture_output=True, text=True)
        if run.returncode != 0:
            check("Rerunning gives the same results", False, f"stage {stage} failed: {run.stderr.strip()[-300:]}")
            return
    files = ["plans_frozen.csv", "combined.csv", "improvements.csv", "penalty_sweep.csv", "service_level.csv"] + [
        f"{k}/{n}.csv" for k in f.ORIGINS for n in ("forecasts", "backtests", "scenarios", "quantiles", "service_levels", "bank_summary", "plans", "totals", "accuracy", "actuals")]
    beds, rel, identical, text_ok = 0.0, 0.0, 0, True
    for name in files:
        a, b = read(out / name), read(work / name)
        identical += (out / name).read_bytes() == (work / name).read_bytes()
        text_ok &= a.shape == b.shape
        for col in a.columns:
            if pd.api.types.is_float_dtype(a[col]):
                x, y = a[col].to_numpy(float), b[col].to_numpy(float)
                keep = ~(np.isnan(x) & np.isnan(y))
                diff = np.abs(x[keep] - y[keep])
                if name.split("/")[-1] in ("forecasts.csv", "backtests.csv", "scenarios.csv", "quantiles.csv", "service_levels.csv", "plans_frozen.csv") or col in ("x", "a", "r", "shortfall", "spare"):
                    beds = max(beds, diff.max(initial=0.0))
                rel = max(rel, (diff / np.maximum(1.0, np.abs(x[keep]))).max(initial=0.0))
            else:
                text_ok &= bool((a[col].astype(str) == b[col].astype(str)).all())
    da, db = (json.loads((d / "decision.json").read_text(encoding="utf-8")) for d in (out, work))
    same = (da["overall"]["ranking"] == db["overall"]["ranking"] and da["overall"]["outcome"] == db["overall"]["outcome"] and da["hashes"]["config"] == db["hashes"]["config"]
            and all(da["penalty_sweep"][k]["outcome"] == db["penalty_sweep"][k]["outcome"] for k in da["penalty_sweep"])
            and all(da["per_origin"][k]["previous_winner"] == db["per_origin"][k]["previous_winner"] for k in f.ORIGINS))
    check("Rerunning the three stages gives the same results (ExtraTrees within 1e-6 beds)", beds <= BEDS and rel <= 1e-8 and text_ok and same,
          f"{identical} of {len(files)} CSV files byte-identical; largest difference {beds:.2g} beds in forecasts, errors, scenarios and plans, {rel:.2g} relative in any number; "
          "config hash, rankings and decisions identical; frozen-plan hash " + ("identical" if da["hashes"]["plans_frozen"] == db["hashes"]["plans_frozen"] else "differs (ExtraTrees float noise)"))
    shutil.rmtree(work)


def readme_check():
    builder = HERE / "build_readme.py"
    if not builder.exists():
        return "README check: not run (section not built yet)."
    run = subprocess.run([sys.executable, "-I", str(builder), "--check"], capture_output=True, text=True)
    check("README.md equals the clean-checkpoint text plus the section generated from outputs/fair_comparison/", run.returncode == 0,
          (run.stdout.strip().splitlines() or run.stderr.strip().splitlines() or ["no output"])[-1])
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path, help="empty scratch folder for the rerun (removed afterwards)")
    parser.add_argument("--out-dir", type=Path, default=f.OUT)
    args = parser.parse_args()
    out, data_dir = args.out_dir.resolve(), args.data_dir.resolve()
    unit_tests()
    decision, log = logic_checks(out, data_dir)
    rerun(out, data_dir, args.work_dir.resolve())
    pending = readme_check()
    failed = [name for name, ok, _ in CHECKS if not ok]
    dropped = {k: [d["backtest_origin"] for d in e["dropped"]] for k, e in log.items()}
    lines = ["# Verification (Phase 4)", "",
             f"Result: **{'ALL CHECKS PASS' if not failed else 'FAILED: ' + '; '.join(failed)}** ({len(CHECKS) - len(failed)} of {len(CHECKS)}).", "",
             f"Numerical tolerances: {REL:g} relative for costs, {BEDS:g} beds for forecasts and scenarios, {PLAN_BEDS:g} beds between solvers.", "",
             "| # | Check | Result | Detail |", "| ---: | --- | --- | --- |"]
    lines += [f"| {i} | {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for i, (name, ok, detail) in enumerate(CHECKS, 1)]
    lines += ["", "## Notes", "",
              f"1. Backtests dropped because SARIMA-24 did not converge (dropped for all five methods, as registered): {dropped}. "
              "The config expected 29 and 35 paths; the banks hold " + " and ".join(str(e["paths"]) for e in log.values()) + ".",
              "2. The LP solver (CBC through PuLP) returns plan values with 8 significant digits, as in the clean checkpoint.",
              f"3. Inside the SAA program {f.TIE:g} per bed-month is added to the running cost so that equally cheap plans resolve to the smaller one. "
              "Reported costs do not include it.",
              "4. ExtraTrees-24 fits differ between runs by about 1e-11 beds (parallel summation), so the frozen-plan hash can differ between runs.",
              "5. The clean-checkpoint README check compares the whole README with the checkpoint text. After this section is appended, the check that applies "
              "is the one above: README.md must equal the unchanged checkpoint text followed by the generated section."]
    if pending:
        lines.append("6. " + pending)
    cc.write_text(out / "verification.md", "\n".join(lines) + "\n")
    print(f"{len(CHECKS) - len(failed)} of {len(CHECKS)} checks pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
