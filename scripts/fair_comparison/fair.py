"""Fair comparison: every forecasting method gets the same deliberate protection, built from its own past errors.

Reuses the verified functions of scripts/clean_checkpoint/pipeline.py by import (data loading, the five forecasts,
the point LP, cost accounting). New here: rolling backtests, error banks, scenarios, the SAA plan, the service-level
plan, pinball loss and the decision rules of outputs/fair_comparison/design_review.md. Stages, run in this order:

  register  write config.json and config.sha256 (approved design and decision rules); refused once plans exist
  plan      per origin: load data up to the cut-off only, run the backtests, build scenarios and every plan, then
            write the bank, forecasts, scenarios, quantiles, plans_frozen.csv and plans.sha256. No target month is loaded.
  score     verify both hashes, load the target months, solve hindsight, score the frozen plans, apply the rules

Usage: python scripts/fair_comparison/fair.py <stage> --data-dir <folder holding the workbook and SOURCE.json>
"""

import argparse
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/clean_checkpoint"))

import numpy as np
import pandas as pd

import pipeline as cc  # verified clean-checkpoint functions, imported unedited

OUT = REPO / "outputs/fair_comparison"
METHODS, ORIGINS = cc.METHODS, cc.ORIGINS
PENALTIES, BASE = (1000.0, 2500.0, 5000.0, 10000.0), 5000.0
ALPHAS = (0.10, 0.05)
LEVELS = (0.5, 0.8, 0.9, 0.95)  # q* = 1 - 500 / p for the four penalties
Q = 0.9
MIN_PATHS = 20
TIE = 1e-4  # added to the running cost per bed-month inside the SAA program so that ties go to the smaller plan
COST_BAND, BED_BAND = cc.COST_BAND, cc.MAE_BAND
INDICATIVE = "the 0.95 level (p = 10,000 and alpha = 0.05) is the largest past error and is indicative only"


# -------------------------------------------------------------------------------------------------- error bank

def backtest_origins(first_month, origin):
    """Every month whose 24-month window starts at or after the first data month and whose six targets end by the origin."""
    return pd.date_range(first_month + pd.DateOffset(months=cc.WINDOW - 1), origin - pd.DateOffset(months=cc.HORIZON), freq="MS")


def build_bank(data, origin):
    """Rolling backtests before the origin. Returns one row per backtest, method and horizon, and the dropped backtests."""
    assert data.index[-1] == origin, "the frame must end at the planning origin"
    rows, dropped = [], []
    for b in backtest_origins(data.index[0], origin):
        try:
            fc, _ = cc.forecast_origin(data.loc[:b], b)  # refit on the 24 months ending at b; refuses later months
        except SystemExit as stop:  # SARIMA-24 did not converge: drop this backtest for every method
            dropped.append(dict(backtest_origin=f"{b:%Y-%m}", reason=str(stop)))
            continue
        months = cc.target_months(b)
        assert months[-1] <= origin and data.loc[:b].index[0] >= data.index[0]
        actual = data.loc[months]
        for m in METHODS:
            g = fc[fc.method == m]
            assert list(g.month) == list(months)
            e_occ = actual.occupied.to_numpy() - g.forecast_occupied.to_numpy()
            e_av = actual.available.to_numpy() - g.forecast_available.to_numpy()
            rows += [dict(backtest_origin=f"{b:%Y-%m-%d}", method=m, horizon=h + 1, month=months[h],
                          window_start=f"{b - pd.DateOffset(months=cc.WINDOW - 1):%Y-%m-%d}",
                          forecast_occupied=g.forecast_occupied.iloc[h], forecast_available=g.forecast_available.iloc[h],
                          actual_occupied=actual.occupied.iloc[h], actual_available=actual.available.iloc[h],
                          error_occupied=e_occ[h], error_available=e_av[h], error_gap=cc.gap(e_occ[h], e_av[h])) for h in range(cc.HORIZON)]
    return pd.DataFrame(rows), dropped


def error_paths(bank, method):
    """Gap-error paths of one method: one row per backtest (occupied and available paired), one column per horizon."""
    g = bank[bank.method == method]
    paths = g.pivot(index="backtest_origin", columns="horizon", values="error_gap").sort_index()
    assert list(paths.columns) == list(range(1, cc.HORIZON + 1)) and not paths.isna().any().any()
    return paths.to_numpy(float)


def scenarios(point_gap, errors, centred=False):
    """Scenario gap paths: the point forecast plus each past error path (raw, or centred per horizon)."""
    errors = np.asarray(errors, dtype=float)
    return np.asarray(point_gap, dtype=float)[None, :] + (errors - errors.mean(axis=0) if centred else errors)


def rank(n, q):
    """Rank of the q-quantile among n past values: ceil((n + 1) q), capped at n."""
    return min(n, math.ceil(round((n + 1) * q, 9)))


def quantile_path(scen, q):
    """Per month, the rank(n, q)-th smallest scenario gap."""
    scen = np.asarray(scen, dtype=float)
    return np.sort(scen, axis=0)[rank(len(scen), q) - 1]


# ------------------------------------------------------------------------------------------------------- plans

def saa_plan(scen, penalty):
    """One shared capacity plan minimising the average cost over the scenario gap paths (equal weights)."""
    import pulp
    scen = np.atleast_2d(np.asarray(scen, dtype=float))
    N, T = scen.shape
    lp = pulp.LpProblem("saa_capacity_plan", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{t}", 0, cc.CAP) for t in range(T)]
    a = [pulp.LpVariable(f"a_{t}", 0, cc.RAMP) for t in range(T)]
    r = [pulp.LpVariable(f"r_{t}", 0, cc.RAMP) for t in range(T)]
    s = [[pulp.LpVariable(f"s_{i}_{t}", 0) for t in range(T)] for i in range(N)]
    for t in range(T):
        lp += x[t] == (x[t - 1] if t else cc.X0) + a[t] - r[t]
        for i in range(N):
            lp += x[t] + s[i][t] >= float(scen[i, t])
    lp += (pulp.lpSum((cc.RUN + TIE) * x[t] + cc.OPEN * a[t] + cc.CLOSE * r[t] for t in range(T))
           + (penalty / N) * pulp.lpSum(s[i][t] for i in range(N) for t in range(T)))
    status = pulp.LpStatus[lp.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))]
    if status != "Optimal":
        raise SystemExit(f"SAA status {status}; stopping")
    return np.array([v.value() for v in x], dtype=float)


def reachable(T):
    """The most capacity that can be open in each month, from zero, under the ramp and the cap."""
    return np.minimum(cc.CAP, cc.X0 + cc.RAMP * np.arange(1, T + 1))


def service_plan(required):
    """Cheapest operating plan with x_t at least the required level each month (no shortfall penalty)."""
    import pulp
    required = np.asarray(required, dtype=float)
    T = len(required)
    assert (required <= reachable(T) + 1e-9).all() and (required >= 0).all()
    lp = pulp.LpProblem("service_level_plan", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{t}", 0, cc.CAP) for t in range(T)]
    a = [pulp.LpVariable(f"a_{t}", 0, cc.RAMP) for t in range(T)]
    r = [pulp.LpVariable(f"r_{t}", 0, cc.RAMP) for t in range(T)]
    for t in range(T):
        lp += x[t] == (x[t - 1] if t else cc.X0) + a[t] - r[t]
        lp += x[t] >= float(required[t])
    lp += pulp.lpSum(cc.RUN * x[t] + cc.OPEN * a[t] + cc.CLOSE * r[t] for t in range(T))
    status = pulp.LpStatus[lp.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))]
    if status != "Optimal":
        raise SystemExit(f"service-level status {status}; stopping")
    return np.array([v.value() for v in x], dtype=float)


def service_requirement(scen, alpha):
    """Required capacity: the (1 - alpha) quantile of the scenario gaps, not below zero, lowered to what is reachable."""
    quantile = quantile_path(scen, 1 - alpha)
    return np.minimum(np.maximum(quantile, 0.0), reachable(len(quantile))), quantile


def plan_id(method, kind, penalty=None, alpha=None):
    tail = f"alpha={alpha:g}" if kind == "service_level" else f"p={penalty:g}"
    return f"{method} | {kind} | {tail}"


def method_plans(point_gap, errors):
    """Every plan of one method at one origin: (kind, penalty, alpha, x)."""
    scen = scenarios(point_gap, errors)
    plans = [("point", BASE, None, cc.lp_plan(point_gap)[0]),
             ("bias_corrected", BASE, None, cc.lp_plan(point_gap + errors.mean(axis=0))[0]),
             ("protected_centred", BASE, None, saa_plan(scenarios(point_gap, errors, centred=True), BASE))]
    for p in PENALTIES:
        plans.append(("protected", p, None, saa_plan(scen, p)))
        if p != BASE:
            plans.append(("point", p, None, saa_plan(point_gap[None, :], p)))
    for alpha in ALPHAS:
        plans.append(("service_level", None, alpha, service_plan(service_requirement(scen, alpha)[0])))
    return plans


# ----------------------------------------------------------------------------------------------------- scoring

def score_plan(x, gaps, penalty):
    """Realised cost of a fixed plan against a gap path at a given shortfall penalty."""
    t = cc.cost_table(x, gaps)
    operating = float(t.running.sum() + t.opening.sum() + t.closing.sum())
    shortfall = float(t.shortfall.sum())
    monthly = (t.running + t.opening + t.closing + penalty * t.shortfall).to_numpy()
    return dict(operating=operating, shortfall_bed_months=shortfall, short_months=int((t.shortfall > 1e-6).sum()),
                shortfall_cost=penalty * shortfall, total=operating + penalty * shortfall), t.assign(monthly_cost=monthly)


def average_cost(x, scen, penalty):
    """Average cost of a plan over scenario gap paths (the SAA objective without the tie-break term)."""
    return float(np.mean([score_plan(x, g, penalty)[0]["total"] for g in np.atleast_2d(scen)]))


def hindsight_plan(gaps, penalty):
    return cc.lp_plan(gaps)[0] if penalty == BASE else saa_plan(np.asarray(gaps, dtype=float)[None, :], penalty)


def pinball(actual, forecast, q):
    """Mean quantile (pinball) loss: q (y - f) if y >= f, (1 - q) (f - y) otherwise. In beds."""
    diff = np.asarray(actual, dtype=float) - np.asarray(forecast, dtype=float)
    return float(np.mean(np.where(diff >= 0, q * diff, (q - 1) * diff)))


def crps(actual, scen):
    """Mean CRPS of the scenario set per month: E|X - y| - 0.5 E|X - X'|."""
    scen, actual = np.asarray(scen, dtype=float), np.asarray(actual, dtype=float)
    spread = np.abs(scen[:, None, :] - scen[None, :, :]).mean(axis=(0, 1))
    return float(np.mean(np.abs(scen - actual[None, :]).mean(axis=0) - 0.5 * spread))


# ------------------------------------------------------------------------------------------------------- rules

def protection_effect(point_cost, protected_cost, band):
    diff = point_cost - protected_cost
    return "helped" if diff > band else "hurt" if diff < -band else "no material difference"


def winner_outcome(cost, hindsight):
    """The clean-checkpoint rules on a methods x origins cost table: ranking, joint best, clear winners, outcome label."""
    r = cc.overall(cost, hindsight)
    winners = r["clear_winners"]
    return dict(ranking=r["order"], first=r["first"], runner_up=r["runner_up"], joint_best=r["joint_best"], joint_best_at_both=r["joint_best_at_both"],
                beats_runner_up_at_each_origin=r["beats_runner_up_at_each_origin"], clear_winners=winners,
                outcome="no clear winner" if not winners else " and ".join(winners), combined_excess={m: float(v) for m, v in r["table"].combined_excess.items()})


def previous_winner(point_cost, protected_cost, hindsight):
    """Cheapest point plan at one origin, and whether its protected plan is joint best among the protected plans."""
    ranked, _ = cc.rank_origin(protected_cost, hindsight)
    method = point_cost.idxmin()
    return dict(method=method, still_wins=bool(ranked.loc[method, "joint_best"]), protected_rank=int(ranked.loc[method, "rank"]),
                protected_joint_best=list(ranked.index[ranked.joint_best]))


def improvements(costs, hindsight, losses):
    """costs: {kind: methods x origins} at the base penalty; losses: {name: methods x origins}. Combined ratios are ratios of sums."""
    keys = list(costs["protected"].columns)
    rows = []
    for scope, use in [(k, [k]) for k in keys] + [("combined", keys)]:
        c = {kind: table[use].sum(axis=1) for kind, table in costs.items()}
        h = sum(hindsight[k] for k in use)
        loss = {name: table[use].mean(axis=1) for name, table in losses.items()}
        prot = c["protected"]
        for m in prot.index:
            share = lambda base: 100 * (prot[base] - prot[m]) / (prot[base] - h) if prot[base] != h else float("nan")
            rows.append(dict(scope=scope, method=m, point=c["point"][m], bias_corrected=c["bias_corrected"][m], protected=prot[m],
                             value_of_protection=c["point"][m] - prot[m], value_of_bias_correction=c["point"][m] - c["bias_corrected"][m],
                             value_of_margin=c["bias_corrected"][m] - prot[m],
                             saving_vs_seasonal_naive=prot["Seasonal naive"] - prot[m], saving_vs_persistence=prot["Persistence"] - prot[m],
                             share_avoidable_vs_seasonal_naive=share("Seasonal naive"), share_avoidable_vs_persistence=share("Persistence"),
                             excess_over_hindsight=prot[m] - h, excess_pct_of_hindsight=100 * (prot[m] - h) / h,
                             **{f"{name}_skill_vs_seasonal_naive": 1 - loss[name][m] / loss[name]["Seasonal naive"] for name in loss}))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------ config

def build_config(source):
    base = cc.build_config(source)
    return dict(
        name="Fair comparison: equal protection for every forecast",
        approved="design_review.md, approved by the project owner with: raw errors primary and centred errors as sensitivity; bias-corrected point "
                 "plan added; ExtraTrees-24 point plans may differ from the checkpoint by up to 1e-6 beds, the other four must match exactly; the "
                 "0.95 level is indicative only",
        problem="a point forecast that runs high gets free insurance under a cost that punishes shortfalls ten times more than spare beds; every "
                "method therefore gets the same deliberate protection, built from its own past errors",
        data=base["data"], origins=base["origins"], gap=base["gap"], forecasts=base["forecasts"],
        costs=dict(running=cc.RUN, opening=cc.OPEN, closing=cc.CLOSE, shortfall_base=BASE, shortfall_sweep=list(PENALTIES)),
        limits=dict(cap=cc.CAP, ramp=cc.RAMP, x0=cc.X0, terminal_charge=0),
        reuse="scripts/clean_checkpoint/pipeline.py imported unedited: load, forecast_origin, lp_plan, cost_table, rank_origin, overall",
        error_bank=dict(
            backtest_origins="every month b whose 24-month window starts at or after the first data month and with b + 6 months <= the planning origin",
            expected_paths={"O1_sep2025": 29, "O2_mar2026": 35},
            procedure="at each b refit all five methods on the 24 months ending at b with the unchanged specification and forecast six months",
            error="actual - forecast for occupied and available; gap error = occupied error / 0.90 - available error; one six-month path per backtest",
            non_convergence=f"if SARIMA-24 does not converge at b, b is dropped for all methods at that origin and logged; stop below {MIN_PATHS} paths"),
        scenarios=dict(primary="gap forecast path of the method plus each of its own raw error paths; equal weights",
                       sensitivity="the same with errors centred per horizon",
                       quantile="level q for month t: the ceil((n + 1) q)-th smallest scenario gap (the largest if the rank exceeds n)"),
        plans={
            "point": "LP on the point forecast (reference, no protection); at p = 5,000 it is the clean-checkpoint LP plan",
            "bias_corrected": "LP on the point forecast plus the mean error path (reference: bias removed, no margin), p = 5,000",
            "protected": "PRIMARY. SAA: one shared plan (x, a, r) minimising the average over scenarios of running, opening, closing and shortfall "
                         "cost, same limits, p = 5,000",
            "protected_centred": "SAA with centred errors, p = 5,000 (sensitivity)",
            "penalty_sweep": "point and protected plans at p = 1,000, 2,500 and 10,000; each planned and scored at its own p with its own hindsight",
            "service_level": "minimise operating cost subject to x_t >= the (1 - alpha) quantile of the scenario gaps (not below zero), "
                             "alpha = 0.10 and 0.05; if the ramp or the cap makes it unreachable the requirement is lowered to the reachable "
                             "level and logged; scored with operating cost, short months, shortfall bed-months and cost at p = 5,000",
            "hindsight": "the LP on the actual gaps, for each p",
            "tie_break": f"inside the SAA program {TIE} is added to the running cost per bed-month so that equally cheap plans resolve to the "
                         "smaller one; reported costs never include it",
            "solver": "PuLP CBC, one thread"},
        measures=["gap MAE and mean signed gap error (actual - forecast) of the point forecast",
                  "pinball loss at 0.9 of the 0.9 quantile forecast (beds), and of the point forecast as a reference",
                  "coverage: months with the actual gap at or below the 0.9 quantile, of 6 per origin and of 12 pooled",
                  "CRPS of the scenario set; pinball loss at 0.5, 0.8 and 0.95",
                  "error bank summary: paths, mean and standard deviation of the gap error, average margin (0.9 quantile - point forecast)",
                  "per plan: operating cost, shortfall bed-months, short months, shortfall cost, total, excess over hindsight"],
        rules=dict(
            primary_endpoint="realised cost of the protected plan at p = 5,000; five plans per origin",
            per_origin=["tie band for cost: 1% of that origin's hindsight cost", "tie band for pinball loss and MAE: 5 beds",
                        "rank the five protected plans by realised cost; plans within the tie band of the cheapest are 'joint best'"],
            across_origins=[
                "overall ranking: combined excess over hindsight",
                "'clear winner': a method whose protected plan is joint best at both origins, or that beats the overall runner-up by more than the "
                "tie band at each origin; otherwise 'no clear winner' with the joint-best methods; if more than one method meets the definition all "
                "are reported as joint winners",
                "previous winner: at each origin the method with the cheapest point plan; it 'still wins' if its protected plan is joint best at "
                "that origin, otherwise it 'no longer wins'; its protected rank is reported",
                "did protection help: point cost - protected cost per method and origin; 'helped' above the tie band, 'hurt' below minus the tie "
                "band, otherwise 'no material difference'",
                "best-calibrated forecast: lowest mean pinball loss at 0.9 across both origins; 'clear' only if lowest at both origins by more "
                "than 5 beds; coverage is reported without a rule",
                "robust to the penalty: the clear-winner rule is applied at each p; the base result is 'robust' if the outcome (the same winner, "
                "or 'no clear winner') is the same at all four levels",
                "service level: no winner is declared"],
            improvement_measures=[
                "value of protection: point cost - protected cost, split into bias (point - bias-corrected) and margin (bias-corrected - protected)",
                "saving against the protected plans of Seasonal naive and of Persistence: baseline cost - plan cost",
                "share of avoidable cost removed: (baseline cost - plan cost) / (baseline cost - hindsight cost), same baselines",
                "skill against Seasonal naive: 1 - loss / Seasonal naive loss, for pinball loss at 0.9 and for gap MAE",
                "excess over hindsight, in model units and as a percentage of hindsight cost",
                "per origin and combined; combined ratios are ratios of sums"],
            indicative=INDICATIVE),
        disclosures=[
            "the outcome months, the bias of each method in each window, the Persistence result at March 2026 and the earlier SARIMA SAA result "
            "were known before this design was written; the rules are fixed in advance but not blind",
            "error paths overlap: 29 and 35 paths carry the information of about 5 and 6 independent six-month blocks",
            INDICATIVE,
            "the gap is measured against 90% occupancy, itself a safety ceiling; a shortfall means occupancy above 90%",
            "all six months are fixed at the origin; there is no re-planning",
            "NHS revised Sep 2026 between the September and October 2026 publications (available -87, occupied -2); the October 2026 publication "
            "is used throughout",
            "SARIMA-24 and ExtraTrees-24 are the small-sample variants of the clean checkpoint",
            "costs are illustrative model units (validated: False)"],
        procedure=["write and hash this config before any backtest or forecast",
                   "plan stage: data up to each origin only; backtests, scenarios and all plans for both origins; hash them",
                   "score stage: verify both hashes, load the target months, solve hindsight, score once, apply the rules"])


def check_config(out, data_dir):
    cc.verify_hashes(out / "config.sha256")
    source = json.loads((data_dir / "SOURCE.json").read_text(encoding="utf-8"))
    if json.loads((out / "config.json").read_text(encoding="utf-8")) != json.loads(json.dumps(build_config(source))):
        raise SystemExit("config.json does not match the code; stopping")


# ------------------------------------------------------------------------------------------------------ stages

def register(args):
    out = args.out_dir
    if (out / "plans_frozen.csv").exists():
        raise SystemExit("plans already exist; the config can no longer be written")
    source = json.loads((args.data_dir / "SOURCE.json").read_text(encoding="utf-8"))
    text = json.dumps(build_config(source), indent=1) + "\n"
    path = out / "config.json"
    if path.exists() and path.read_bytes() != text.encode("utf-8"):
        raise SystemExit("config.json exists with different content; stopping")
    cc.write_text(path, text)
    cc.write_text(out / "config.sha256", f"{cc.sha256(path)}  config.json\n")
    print("config.sha256", cc.sha256(path))


def plan(args):
    out = args.out_dir
    check_config(out, args.data_dir)
    if (out / "plans_frozen.csv").exists():
        raise SystemExit("plans are already frozen; stopping")
    frozen, log = [], {}
    for key, origin in ORIGINS.items():
        data = cc.load(args.data_dir, cutoff=origin)  # nothing after the planning origin is in memory
        fc, fit = cc.forecast_origin(data, origin)
        bank, dropped = build_bank(data, origin)
        n = bank.backtest_origin.nunique()
        if n < MIN_PATHS:
            raise SystemExit(f"{key}: only {n} error paths; stopping")
        months = cc.target_months(origin)
        scen_rows, quant_rows, service_rows, summary = [], [], [], []
        for m in METHODS:
            point = fc[fc.method == m].forecast_gap.to_numpy(float)
            errors = error_paths(bank, m)
            assert len(errors) == n
            scen = scenarios(point, errors)
            origins_b = sorted(bank[bank.method == m].backtest_origin.unique())
            scen_rows += [dict(method=m, backtest_origin=origins_b[i], month=months[h], horizon=h + 1, error_gap=errors[i, h], scenario_gap=scen[i, h])
                          for i in range(n) for h in range(cc.HORIZON)]
            quant_rows += [dict(method=m, month=months[h], level=q, rank=rank(n, q), quantile_gap=quantile_path(scen, q)[h], point_gap=point[h])
                           for q in LEVELS for h in range(cc.HORIZON)]
            for alpha in ALPHAS:
                required, quantile = service_requirement(scen, alpha)
                service_rows += [dict(method=m, alpha=alpha, month=months[h], quantile_gap=quantile[h], required=required[h],
                                      lowered=bool(required[h] < max(quantile[h], 0.0) - 1e-9)) for h in range(cc.HORIZON)]
            summary.append(dict(method=m, paths=n, mean_gap_error=errors.mean(), sd_gap_error=errors.std(ddof=1),
                                mean_margin_090=float((quantile_path(scen, Q) - point).mean())))
            for kind, p, alpha, x in method_plans(point, errors):
                a, r = cc.flows(x)
                frozen += [dict(origin=key, plan=plan_id(m, kind, p, alpha), method=m, kind=kind, penalty=p, alpha=alpha, month=months[h],
                                x=x[h], a=a[h], r=r[h]) for h in range(cc.HORIZON)]
        for name, frame in (("forecasts", fc), ("backtests", bank), ("scenarios", pd.DataFrame(scen_rows)), ("quantiles", pd.DataFrame(quant_rows)),
                            ("service_levels", pd.DataFrame(service_rows)), ("bank_summary", pd.DataFrame(summary))):
            cc.write_csv(out / key / f"{name}.csv", frame)
        log[key] = dict(cutoff=f"{origin:%Y-%m}", data_first_month=f"{data.index[0]:%Y-%m}", data_last_month=f"{data.index[-1]:%Y-%m}", paths=n,
                        backtest_origins=[bank.backtest_origin.min()[:7], bank.backtest_origin.max()[:7]], dropped=dropped,
                        last_backtest_target=f"{bank.month.max():%Y-%m}", earliest_training_month=bank.window_start.min()[:7],
                        ranks={f"{q:g}": rank(n, q) for q in LEVELS}, origin_fit=fit)
    cc.write_csv(out / "plans_frozen.csv", pd.DataFrame(frozen))
    cc.write_text(out / "fit_log.json", json.dumps(log, indent=1) + "\n")
    names = ["plans_frozen.csv"] + [f"{key}/{n}.csv" for key in ORIGINS for n in ("forecasts", "backtests", "scenarios", "quantiles", "service_levels", "bank_summary")]
    cc.write_text(out / "plans.sha256", "".join(f"{cc.sha256(out / n)}  {n}\n" for n in names))
    print("plans.sha256", cc.sha256(out / "plans_frozen.csv"))
    for key, e in log.items():
        print(key, "paths", e["paths"], "backtests", e["backtest_origins"], "dropped", len(e["dropped"]), "ranks", e["ranks"])


def score(args):
    out = args.out_dir
    check_config(out, args.data_dir)
    cc.verify_hashes(out / "plans.sha256")
    if (out / "decision.json").exists():
        raise SystemExit("already scored; stopping")
    frozen = cc.read_csv(out / "plans_frozen.csv")
    data = cc.load(args.data_dir, cutoff=max(cc.target_months(o)[-1] for o in ORIGINS.values()))
    cost = {(kind, p): {} for kind in ("point", "protected") for p in PENALTIES}
    cost.update({("bias_corrected", BASE): {}, ("protected_centred", BASE): {}})
    hindsight = {p: {} for p in PENALTIES}
    loss = {name: {} for name in ("pinball_090", "gap_mae")}
    per_origin, service = {}, []
    for key, origin in ORIGINS.items():
        months = cc.target_months(origin)
        actual = data.loc[months]
        d = cc.gap(actual.occupied, actual.available).to_numpy()
        cc.write_csv(out / key / "actuals.csv", pd.DataFrame(dict(month=months, occupied=actual.occupied.to_numpy(), available=actual.available.to_numpy(), gap=d)))
        totals, monthly = [], []
        for p in PENALTIES:
            x = hindsight_plan(d, p)
            row, table = score_plan(x, d, p)
            hindsight[p][key] = row["total"]
            totals.append(dict(plan=f"Hindsight | p={p:g}", method="Hindsight", kind="hindsight", penalty=p, alpha=None, scored_at=p, **row))
            monthly.append(table.assign(plan=f"Hindsight | p={p:g}", month=months))
        f = frozen[frozen.origin == key]
        for name, g in f.groupby("plan", sort=False):
            g = g.sort_values("month")
            assert list(g.month) == list(months)
            kind, m = g.kind.iloc[0], g.method.iloc[0]
            p = BASE if kind == "service_level" else float(g.penalty.iloc[0])
            row, table = score_plan(g.x.to_numpy(), d, p)
            totals.append(dict(plan=name, method=m, kind=kind, penalty=None if kind == "service_level" else p,
                               alpha=g.alpha.iloc[0] if kind == "service_level" else None, scored_at=p, **row))
            monthly.append(table.assign(plan=name, month=months))
            if (kind, p) in cost and kind != "service_level":
                cost[(kind, p)].setdefault(key, {})[m] = row["total"]
            if kind == "service_level":
                service.append(dict(origin=key, method=m, alpha=float(g.alpha.iloc[0]), **row))
        totals = pd.DataFrame(totals)
        totals["hindsight"] = totals.scored_at.map(lambda p: hindsight[p][key])
        totals["excess_over_hindsight"] = totals.total - totals.hindsight
        totals["excess_pct_of_hindsight"] = 100 * totals.excess_over_hindsight / totals.hindsight
        cc.write_csv(out / key / "totals.csv", totals)
        cc.write_csv(out / key / "plans.csv", pd.concat(monthly)[["plan", "month", "x", "a", "r", "shortfall", "spare", "monthly_cost"]])
        fc, quant, scen = (cc.read_csv(out / key / f"{n}.csv") for n in ("forecasts", "quantiles", "scenarios"))
        rows = []
        for m in METHODS:
            point = fc[fc.method == m].sort_values("month").forecast_gap.to_numpy()
            qs = {q: quant[(quant.method == m) & np.isclose(quant.level, q)].sort_values("month").quantile_gap.to_numpy() for q in LEVELS}
            ens = scen[scen.method == m].pivot(index="backtest_origin", columns="horizon", values="scenario_gap").to_numpy()
            rows.append(dict(method=m, gap_mae=float(np.abs(d - point).mean()), gap_mean_signed_error=float((d - point).mean()),
                             pinball_090=pinball(d, qs[Q], Q), pinball_090_point=pinball(d, point, Q), covered_090=int((d <= qs[Q]).sum()),
                             months=len(d), mean_margin_090=float((qs[Q] - point).mean()), crps=crps(d, ens),
                             **{f"pinball_{int(round(q * 100)):03d}": pinball(d, qs[q], q) for q in LEVELS if q != Q}))
        acc = pd.DataFrame(rows).set_index("method")
        for name in loss:
            loss[name][key] = acc[name]
            acc[f"{name}_skill_vs_seasonal_naive"] = 1 - acc[name] / acc.loc["Seasonal naive", name]
        cc.write_csv(out / key / "accuracy.csv", acc.reset_index())
        band = COST_BAND * hindsight[BASE][key]
        point_c, prot_c = pd.Series(cost[("point", BASE)][key])[list(METHODS)], pd.Series(cost[("protected", BASE)][key])[list(METHODS)]
        ranked, _ = cc.rank_origin(prot_c, hindsight[BASE][key])
        per_origin[key] = dict(hindsight_cost=hindsight[BASE][key], cost_tie_band=band, cheapest_protected=prot_c.idxmin(),
                               joint_best_protected=list(ranked.index[ranked.joint_best]), protected_rank={m: int(ranked.loc[m, "rank"]) for m in METHODS},
                               previous_winner=previous_winner(point_c, prot_c, hindsight[BASE][key]),
                               protection={m: dict(point=float(point_c[m]), protected=float(prot_c[m]), value=float(point_c[m] - prot_c[m]),
                                                   effect=protection_effect(point_c[m], prot_c[m], band)) for m in METHODS},
                               lowest_pinball_090=acc.pinball_090.idxmin(), lowest_gap_mae=acc.gap_mae.idxmin(),
                               coverage_090={m: f"{int(acc.loc[m, 'covered_090'])} of {int(acc.loc[m, 'months'])}" for m in METHODS})
    tables = {k: pd.DataFrame(v).loc[list(METHODS), list(ORIGINS)] for k, v in cost.items()}
    losses = {name: pd.DataFrame(v).loc[list(METHODS), list(ORIGINS)] for name, v in loss.items()}
    base = winner_outcome(tables[("protected", BASE)], hindsight[BASE])
    sweep = {f"{p:g}": dict(q_star=1 - cc.RUN / p, **winner_outcome(tables[("protected", p)], hindsight[p]),
                            point=winner_outcome(tables[("point", p)], hindsight[p])["ranking"]) for p in PENALTIES}
    robust = all(v["outcome"] == base["outcome"] for v in sweep.values())
    centred = winner_outcome(tables[("protected_centred", BASE)], hindsight[BASE])
    calibrated = cc.best_forecast(losses["pinball_090"])
    combined = pd.DataFrame(dict(method=list(METHODS)))
    for key in ORIGINS:
        for kind in ("point", "bias_corrected", "protected", "protected_centred"):
            combined[f"{key}_{kind}"] = tables[(kind, BASE)][key].to_numpy()
        ranked, _ = cc.rank_origin(tables[("protected", BASE)][key], hindsight[BASE][key])
        combined[f"{key}_protected_excess"], combined[f"{key}_protected_rank"] = ranked.excess.to_numpy(), ranked["rank"].to_numpy()
        combined[f"{key}_protected_joint_best"] = ranked.joint_best.to_numpy()
    combined["combined_protected_excess"] = [base["combined_excess"][m] for m in METHODS]
    combined["overall_rank"] = pd.Series(base["combined_excess"]).rank(method="min").astype(int).to_numpy()
    cc.write_csv(out / "combined.csv", combined)
    rows = []
    for p in PENALTIES:
        for kind in ("point", "protected"):
            r = winner_outcome(tables[(kind, p)], hindsight[p])
            for key in ORIGINS:
                ranked, _ = cc.rank_origin(tables[(kind, p)][key], hindsight[p][key])
                rows += [dict(penalty=p, q_star=1 - cc.RUN / p, kind=kind, scope=key, method=m, total=ranked.loc[m, "total"], excess=ranked.loc[m, "excess"],
                              rank=int(ranked.loc[m, "rank"]), joint_best=bool(ranked.loc[m, "joint_best"])) for m in METHODS]
            ex = pd.Series(r["combined_excess"])
            rows += [dict(penalty=p, q_star=1 - cc.RUN / p, kind=kind, scope="combined", method=m, total=float(tables[(kind, p)].loc[m].sum()), excess=ex[m],
                          rank=int(ex.rank(method="min")[m]), joint_best=m in r["joint_best_at_both"]) for m in METHODS]
    cc.write_csv(out / "penalty_sweep.csv", pd.DataFrame(rows))
    cc.write_csv(out / "service_level.csv", pd.DataFrame(service))
    cc.write_csv(out / "improvements.csv", improvements({k: tables[(k, BASE)] for k in ("point", "bias_corrected", "protected")}, hindsight[BASE], losses))
    decision = dict(per_origin=per_origin, overall=base, centred_sensitivity=dict(outcome=centred["outcome"], ranking=centred["ranking"], joint_best=centred["joint_best"]),
                    best_calibrated=dict(method=calibrated["method"], mean_pinball_090=calibrated["mean_gap_mae"], clear=calibrated["clear"],
                                         lowest_per_origin=calibrated["lowest_per_origin"], margin_over_next_best=calibrated["margin_over_next_best"]),
                    penalty_sweep=sweep, robust_to_penalty=robust, indicative=INDICATIVE,
                    hashes=dict(config=cc.sha256(out / "config.json"), plans_frozen=cc.sha256(out / "plans_frozen.csv")))
    cc.write_text(out / "decision.json", json.dumps(decision, indent=1) + "\n")
    print(json.dumps(decision, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("register", "plan", "score"))
    parser.add_argument("--data-dir", required=True, type=Path, help="folder holding the workbook and SOURCE.json (data/raw_v6)")
    parser.add_argument("--out-dir", type=Path, default=OUT)
    args = parser.parse_args()
    args.data_dir, args.out_dir = args.data_dir.resolve(), args.out_dir.resolve()
    {"register": register, "plan": plan, "score": score}[args.stage](args)


if __name__ == "__main__":
    main()
