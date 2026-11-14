"""Clean checkpoint: five forecasts x two decision methods at two origins, rebuilt from raw data.

Self-contained. It loads the NHS workbook, fits the forecasts, builds the plans and scores them without importing any
project module or reading any saved result. Stages, run in this order:

  register  write config.json and config.sha256 (setup and decision rules); refused once plans exist
  plan      per origin: load data up to the cut-off only, fit the five forecasts, build the Rule and LP plans, then
            write forecasts.csv, plans_frozen.csv, fit_log.json and plans.sha256. No outcome month is loaded.
  score     verify both hashes, load the outcome months, solve hindsight, score the frozen plans, apply the rules

Usage: python scripts/clean_checkpoint/pipeline.py <stage> --data-dir <folder holding the workbook and SOURCE.json>
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import warnings

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "outputs/clean_checkpoint"

TAU = 0.90
RUN, OPEN, CLOSE, SHORT = 500.0, 550.0, 300.0, 5000.0
CAP, RAMP, X0 = 15000.0, 5000.0, 0.0
WINDOW, HORIZON = 24, 6
SHEET, HEADER_ROW = "TS - All Acutes", 12  # zero-based row index (Excel row 13)
LABELS = {"available": "G&A beds available", "occupied": "G&A beds occupied"}
SERIES = ("occupied", "available")
ORIGINS = {"O1_sep2025": pd.Timestamp("2025-09-01"), "O2_mar2026": pd.Timestamp("2026-03-01")}
METHODS = ("Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24")
DECISIONS = ("Rule", "LP")
PLANS = tuple(f"{m} {d}" for m in METHODS for d in DECISIONS)
COST_BAND, MAE_BAND = 0.01, 5.0
LAGS, ROLLS = (1, 2, 3, 6, 12), (3, 6, 12)
FEATURES = ([f"target_lag_{k}" for k in LAGS] + [f"target_roll_{s}_{w}" for w in ROLLS for s in ("mean", "std")]
            + ["month_number", "quarter", "year", "month_sin", "month_cos"])
SARIMA = dict(order=(1, 0, 0), seasonal_order=(0, 1, 0, 12), trend="n", enforce_stationarity=True, enforce_invertibility=True,
              simple_differencing=False, missing="raise")
SARIMA_FIT = dict(method="lbfgs", maxiter=400, disp=False, cov_type="none")
EXTRA_TREES = dict(n_estimators=300, min_samples_leaf=2, random_state=42, n_jobs=-1)


# ---------------------------------------------------------------------------------------------------------- files

def write_text(path, text):
    """UTF-8 with LF line endings on every platform, so file hashes do not depend on the machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_csv(path, frame):
    write_text(path, frame.to_csv(index=False, float_format="%.17g", date_format="%Y-%m-%d", lineterminator="\n"))


def read_csv(path):
    return pd.read_csv(path, parse_dates=["month"], float_precision="round_trip")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_hashes(hash_file):
    """Every line '<sha256>  <relative path>' must match the file next to the hash file."""
    for line in hash_file.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        if sha256(hash_file.parent / name) != digest:
            raise SystemExit(f"{name} does not match {hash_file.name}; stopping")


# ----------------------------------------------------------------------------------------------------------- data

def parse(raw, cutoff):
    """Monthly available and occupied beds up to `cutoff`. Later rows are discarded before any value is converted."""
    header = raw.iloc[HEADER_ROW].astype(str).str.strip()
    assert header.iloc[1] == "Month", "header row moved"
    columns = {}
    for name, label in LABELS.items():
        hits = [i for i, v in enumerate(header) if v == label]
        assert len(hits) == 1, f"expected exactly one '{label}' column"
        columns[name] = hits[0]
    months = pd.to_datetime(raw.iloc[HEADER_ROW + 1:, 1], format="%B %Y", errors="coerce")
    keep = months.notna() & (months <= pd.Timestamp(cutoff))
    body = raw.iloc[HEADER_ROW + 1:][keep.to_numpy()]
    data = pd.DataFrame(index=pd.DatetimeIndex(months[keep], name="month"))
    for name, col in columns.items():
        data[name] = pd.to_numeric(body[col].astype(str).str.replace(",", ""), errors="coerce").to_numpy(float)
    data = data.dropna()  # months with a blank value are dropped
    assert data.index.equals(pd.date_range(data.index[0], data.index[-1], freq="MS")), "months are not consecutive"
    return data


def load(data_dir, cutoff):
    source = json.loads((data_dir / "SOURCE.json").read_text(encoding="utf-8"))
    book = data_dir / source["file"]
    if sha256(book) != source["sha256"]:
        raise SystemExit("workbook SHA-256 does not match SOURCE.json; stopping")
    return parse(pd.read_excel(book, sheet_name=SHEET, header=None), cutoff)


def gap(occupied, available):
    return occupied / TAU - available


def target_months(origin):
    return pd.date_range(origin + pd.DateOffset(months=1), periods=HORIZON, freq="MS")


def window(data, origin):
    """The 24 months ending at the origin. The frame itself must end at the origin (nothing later is in memory)."""
    assert data.index[-1] == origin, "the frame must end at the cut-off"
    w = data.iloc[-WINDOW:]
    assert len(w) == WINDOW and w.index.equals(pd.date_range(end=origin, periods=WINDOW, freq="MS"))
    return w


# ------------------------------------------------------------------------------------------------------ forecasts

def mean_forecast(y):
    return np.repeat(float(y.mean()), HORIZON)


def persistence_forecast(y):
    return np.repeat(float(y.iloc[-1]), HORIZON)


def seasonal_naive_forecast(y):
    source = target_months(y.index[-1]) - pd.DateOffset(years=1)
    assert source.isin(y.index).all(), "seasonal naive needs the same months one year earlier inside the window"
    return y.loc[source].to_numpy(float)


def sarima_forecast(y):
    """SARIMA(1,0,0)(0,1,0,12), trend 'n', on the standardised series; every window month is a fitting label."""
    from sklearn.preprocessing import StandardScaler
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    values = y.to_numpy().reshape(-1, 1)
    scaler = StandardScaler().fit(values)
    z = pd.Series(scaler.transform(values).ravel(), index=y.index, name=y.name)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fitted = SARIMAX(z, exog=None, **SARIMA).fit(**SARIMA_FIT)
    info = dict(converged=bool(fitted.mle_retvals.get("converged", False)), iterations=int(fitted.mle_retvals.get("iterations", -1)),
                labels=len(y), label_start=f"{y.index[0]:%Y-%m}", label_end=f"{y.index[-1]:%Y-%m}",
                params={k: float(v) for k, v in fitted.params.items()},
                warnings=sorted({f"{type(m.message).__name__}: {m.message}" for m in caught}))
    if not info["converged"]:
        raise SystemExit(f"SARIMA-24 did not converge for {y.name} at {y.index[-1]:%Y-%m}; stopping")
    raw = scaler.inverse_transform(np.asarray(fitted.forecast(HORIZON)).reshape(-1, 1)).ravel()
    return np.maximum(0.0, raw), info


def training_features(y):
    """The 16 target-only features. Lags and rolling statistics use earlier months only, never the current value."""
    f = pd.DataFrame(index=y.index)
    shifted = y.shift(1)
    for k in LAGS:
        f[f"target_lag_{k}"] = y.shift(k)
    for w in ROLLS:
        f[f"target_roll_mean_{w}"] = shifted.rolling(w).mean()
        f[f"target_roll_std_{w}"] = shifted.rolling(w).std()
    f["month_number"] = f.index.month
    f["quarter"] = f.index.quarter
    f["year"] = f.index.year
    f["month_sin"] = np.sin(2 * np.pi * f.index.month / 12)
    f["month_cos"] = np.cos(2 * np.pi * f.index.month / 12)
    return f[FEATURES]


def recursive_row(history, date):
    """Features for the month after `history`, from observed values and the model's own earlier predictions."""
    assert date == history.index[-1] + pd.offsets.MonthBegin(1), "recursive forecasts advance one month at a time"
    v = {}
    for k in LAGS:
        v[f"target_lag_{k}"] = float(history.loc[date - pd.DateOffset(months=k)])
    for w in ROLLS:
        prior = history.iloc[-w:]
        v[f"target_roll_mean_{w}"] = float(prior.mean())
        v[f"target_roll_std_{w}"] = float(prior.std())
    v["month_number"], v["quarter"], v["year"] = date.month, date.quarter, date.year
    v["month_sin"] = math.sin(2 * math.pi * date.month / 12)
    v["month_cos"] = math.cos(2 * math.pi * date.month / 12)
    return pd.DataFrame([v], index=pd.DatetimeIndex([date], name="month"))[FEATURES]


def extra_trees_forecast(y):
    """ExtraTrees on the 16 features, recursive six steps. Rows whose features need data before the window are dropped."""
    from sklearn.ensemble import ExtraTreesRegressor
    x = training_features(y).dropna()
    assert not x.isna().any().any() and x.index[-1] == y.index[-1]
    assert x.index[0] - pd.DateOffset(months=max(LAGS)) >= y.index[0], "a training row reaches before the window"
    model = ExtraTreesRegressor(**EXTRA_TREES).fit(x, y.loc[x.index])
    history, out = y.copy(), []
    for date in target_months(y.index[-1]):
        value = max(0.0, float(model.predict(recursive_row(history, date))[0]))
        out.append(value)
        history.loc[date] = value
    info = dict(training_rows=len(x), first_row=f"{x.index[0]:%Y-%m}", last_row=f"{x.index[-1]:%Y-%m}",
                earliest_month_used=f"{x.index[0] - pd.DateOffset(months=max(LAGS)):%Y-%m}", features=len(FEATURES))
    return np.array(out), info


def forecast_origin(data, origin):
    """All five forecasts for both series from the 24-month window. Returns the forecast table and a fit log."""
    w = window(data, origin)
    months = target_months(origin)
    paths = {}
    log = dict(cutoff=f"{origin:%Y-%m}", data_last_month=f"{data.index[-1]:%Y-%m}", window=[f"{w.index[0]:%Y-%m}", f"{w.index[-1]:%Y-%m}"],
               forecast_months=[f"{m:%Y-%m}" for m in months],
               seasonal_naive_source=[f"{m:%Y-%m}" for m in months - pd.DateOffset(years=1)], sarima={}, extra_trees={})
    for s in SERIES:
        y = w[s]
        paths[("Mean-24", s)] = mean_forecast(y)
        paths[("Persistence", s)] = persistence_forecast(y)
        paths[("Seasonal naive", s)] = seasonal_naive_forecast(y)
        paths[("SARIMA-24", s)], log["sarima"][s] = sarima_forecast(y)
        paths[("ExtraTrees-24", s)], log["extra_trees"][s] = extra_trees_forecast(y)
    rows = []
    for m in METHODS:
        occ, av = paths[(m, "occupied")], paths[(m, "available")]
        rows += [dict(method=m, month=months[h], forecast_occupied=occ[h], forecast_available=av[h], forecast_gap=gap(occ[h], av[h]))
                 for h in range(HORIZON)]
    return pd.DataFrame(rows), log


# ------------------------------------------------------------------------------------------------------ decisions

def rule_plan(gaps):
    """Follow the forecast: x_t = max(0, gap), moved as far toward it as the cap and the ramp limits allow."""
    x, prev = [], X0
    for g in gaps:
        target = min(CAP, max(0.0, float(g)))
        prev = min(prev + RAMP, max(prev - RAMP, target))
        x.append(prev)
    return np.array(x)


def lp_plan(gaps):
    """Minimise running + opening + closing + shortfall cost against the gap path. Returns the plan and its objective."""
    import pulp
    T = len(gaps)
    lp = pulp.LpProblem("capacity_plan", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{t}", 0, CAP) for t in range(T)]
    a = [pulp.LpVariable(f"a_{t}", 0, RAMP) for t in range(T)]
    r = [pulp.LpVariable(f"r_{t}", 0, RAMP) for t in range(T)]
    s = [pulp.LpVariable(f"s_{t}", 0) for t in range(T)]
    u = [pulp.LpVariable(f"u_{t}", 0) for t in range(T)]
    for t in range(T):
        lp += x[t] == (x[t - 1] if t else X0) + a[t] - r[t]
        lp += x[t] + s[t] - u[t] == float(gaps[t])
    lp += pulp.lpSum(RUN * x[t] + OPEN * a[t] + CLOSE * r[t] + SHORT * s[t] for t in range(T))
    status = pulp.LpStatus[lp.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))]
    if status != "Optimal":
        raise SystemExit(f"LP status {status}; stopping")
    return np.array([v.value() for v in x], dtype=float), float(pulp.value(lp.objective))


def flows(x):
    """Beds opened and closed each month, from the change in x (starting from X0)."""
    step = np.diff(np.concatenate([[X0], np.asarray(x, dtype=float)]))
    return np.maximum(step, 0.0), np.maximum(-step, 0.0)


def cost_table(x, gaps):
    """Month-by-month cost of a fixed plan against a gap path. No shortfall deadband."""
    x, gaps = np.asarray(x, dtype=float), np.asarray(gaps, dtype=float)
    a, r = flows(x)
    shortfall, spare = np.maximum(gaps - x, 0.0), np.maximum(x - gaps, 0.0)
    return pd.DataFrame(dict(x=x, a=a, r=r, shortfall=shortfall, spare=spare, running=RUN * x, opening=OPEN * a, closing=CLOSE * r,
                             shortfall_cost=SHORT * shortfall, monthly_cost=RUN * x + OPEN * a + CLOSE * r + SHORT * shortfall))


def totals_row(table):
    operating = float(table.running.sum() + table.opening.sum() + table.closing.sum())
    shortfall_cost = float(table.shortfall_cost.sum())
    return dict(running=float(table.running.sum()), opening=float(table.opening.sum()), closing=float(table.closing.sum()), operating=operating,
                shortfall_bed_months=float(table.shortfall.sum()), shortfall_cost=shortfall_cost, total=operating + shortfall_cost)


# ---------------------------------------------------------------------------------------------------------- rules

def split(plan):
    method, decision = plan.rsplit(" ", 1)
    return method, decision


def rank_origin(cost, hindsight):
    """Rank the plans of one origin. Plans within 1% of hindsight cost of the cheapest are joint best."""
    band = COST_BAND * hindsight
    return pd.DataFrame(dict(total=cost, excess=cost - hindsight, excess_pct=100 * (cost - hindsight) / hindsight,
                             rank=cost.rank(method="min").astype(int), joint_best=(cost - cost.min()) <= band)), band


def overall(cost, hindsight):
    """cost: plans x origins. Combined excess over hindsight, overall rank, joint best per origin, clear winners."""
    keys = list(cost.columns)
    bands = {k: COST_BAND * hindsight[k] for k in keys}
    joint = {k: [p for p in cost.index if cost.loc[p, k] - cost[k].min() <= bands[k]] for k in keys}
    excess = sum(cost[k] - hindsight[k] for k in keys)
    order = list(excess.sort_values(kind="stable").index)  # exact ties keep the fixed plan order
    first, runner = order[0], order[1]
    margins = {k: float(cost.loc[runner, k] - cost.loc[first, k]) for k in keys}
    beats_runner = all(margins[k] > bands[k] for k in keys)
    joint_both = [p for p in cost.index if all(p in joint[k] for k in keys)]
    winners = [p for p in cost.index if p in joint_both or (beats_runner and p == first)]
    table = pd.DataFrame(dict(combined_excess=excess, overall_rank=excess.rank(method="min").astype(int)))
    return dict(table=table, order=order, first=first, runner_up=runner, margins_first_over_runner_up=margins, tie_bands=bands,
                beats_runner_up_at_each_origin=beats_runner, joint_best=joint, joint_best_at_both=joint_both, clear_winners=winners)


def best_forecast(gap_mae):
    """gap_mae: methods x origins. Lowest mean gap MAE; clear only if lowest at every origin by more than 5 beds."""
    mean = gap_mae.mean(axis=1)
    best = mean.idxmin()
    margins = {k: float((gap_mae[k].drop(best) - gap_mae.loc[best, k]).min()) for k in gap_mae.columns}
    return dict(method=best, mean_gap_mae=float(mean[best]), margin_over_next_best=margins, clear=all(v > MAE_BAND for v in margins.values()),
                lowest_per_origin={k: gap_mae[k].idxmin() for k in gap_mae.columns})


def improvements(cost, hindsight, mae):
    """Improvement measures per origin and combined. mae: {series: methods x origins}. Combined ratios are ratios of sums."""
    keys = list(cost.columns)
    rows = []
    for scope, use in [(k, [k]) for k in keys] + [("combined", keys)]:
        c, h = cost[use].sum(axis=1), sum(hindsight[k] for k in use)
        m = {s: mae[s][use].mean(axis=1) for s in mae}
        share = lambda base, plan: 100 * (c[base] - c[plan]) / (c[base] - h) if c[base] != h else float("nan")
        for plan in cost.index:
            method, decision = split(plan)
            rows.append(dict(scope=scope, plan=plan, method=method, decision=decision, total=c[plan], excess_over_hindsight=c[plan] - h,
                             excess_pct_of_hindsight=100 * (c[plan] - h) / h,
                             value_of_optimisation=c[f"{method} Rule"] - c[f"{method} LP"],
                             saving_vs_persistence=c[f"Persistence {decision}"] - c[plan],
                             saving_vs_seasonal_naive=c[f"Seasonal naive {decision}"] - c[plan],
                             share_avoidable_vs_persistence_lp=share("Persistence LP", plan),
                             share_avoidable_vs_seasonal_naive_lp=share("Seasonal naive LP", plan),
                             **{f"{s}_skill_vs_seasonal_naive": 1 - m[s][method] / m[s]["Seasonal naive"] for s in mae}))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------- config

def build_config(source):
    ym = lambda idx: [f"{m:%Y-%m}" for m in idx]
    origins = {key: dict(cutoff=f"{o:%Y-%m}", training_window=ym(pd.date_range(end=o, periods=WINDOW, freq="MS"))[::WINDOW - 1],
                         forecast_months=ym(target_months(o))) for key, o in ORIGINS.items()}
    return dict(
        name="Clean checkpoint: five forecasting methods x two decision methods at two origins",
        question="Which forecasting method, combined with which decision method, gives the cheapest realised bed-capacity plan, and how much "
                 "does it improve on simple baselines and on simply following the forecast?",
        data=dict(file=f"data/raw_v6/{source['file']}", sha256=source["sha256"], source_url=source["source_url"],
                  retrieved_utc=source["retrieved_utc"], sheet=SHEET, header_row="zero-based row index 12 (Excel row 13)",
                  available=LABELS["available"], occupied=LABELS["occupied"], blanks="months with a blank value are dropped",
                  cutoff_rule="rows after a cut-off are discarded before any value is converted", not_used="data/raw_v5"),
        origins=origins,
        gap="d = occupied / 0.90 - available",
        forecasts={
            "common": "each method forecasts occupied and available separately, six months ahead, from the 24-month training window only",
            "Mean-24": "mean of the window, flat",
            "Persistence": "last month of the window, flat",
            "Seasonal naive": "same calendar month one year earlier",
            "SARIMA-24": dict(order=[1, 0, 0], seasonal_order=[0, 1, 0, 12], trend="n", enforce_stationarity=True, enforce_invertibility=True,
                              simple_differencing=False, target_scaling="StandardScaler fitted on the 24 window values; forecasts inverse-transformed",
                              fit="lbfgs, maxiter 400, cov_type none", labels="all 24 window months", bounds="forecasts clipped at 0",
                              non_convergence="stop; no rescue"),
            "ExtraTrees-24": dict(estimator="ExtraTreesRegressor", n_estimators=300, min_samples_leaf=2, random_state=42, n_jobs=-1,
                                  features=FEATURES, feature_rule="lags and shifted rolling mean/std (ddof 1) of the target, plus calendar values",
                                  training_rows="rows whose features need data before the window are dropped (12 rows expected)",
                                  forecasting="recursive, six steps, earlier predictions feed later features", bounds="forecasts clipped at 0",
                                  note="the project pipeline's median imputer is a no-op here because no feature is missing"),
        },
        decisions=dict(
            costs=dict(running=RUN, opening=OPEN, closing=CLOSE, shortfall=SHORT), limits=dict(cap=CAP, ramp_up=RAMP, ramp_down=RAMP, x0=X0),
            Rule="x_t = max(0, forecast gap), clipped in month order to 0 <= x_t <= 15,000 and the ramp limits (if a ramp binds, move as far "
                 "toward the forecast gap as allowed); a_t, r_t follow from changes in x_t",
            LP="minimise sum_t (500 x_t + 550 a_t + 300 r_t + 5000 s_t) s.t. x_t + s_t - u_t = forecast gap_t; x_t = x_{t-1} + a_t - r_t; "
               "x_0 = 0; 0 <= x_t <= 15,000; 0 <= a_t, r_t <= 5,000; s_t, u_t >= 0; continuous; no terminal charge; solver PuLP CBC, one thread",
            Hindsight="the LP solved with the actual gaps"),
        scoring="each plan is held fixed; realised cost = sum_t (500 x_t + 550 a_t + 300 r_t) + 5000 sum_t max(0, d_t - x_t) with actual gaps; "
                "no shortfall deadband; a_t and r_t are the positive and negative parts of x_t - x_{t-1}",
        plans=list(PLANS) + ["Hindsight"],
        rules=dict(
            per_origin=["tie band for cost: 1% of that origin's hindsight cost", "tie band for gap MAE: 5 beds",
                        "rank the 10 plans by realised cost; plans within the tie band of the cheapest are 'joint best'"],
            across_origins=[
                "overall cost ranking: by combined excess over hindsight (sum over the two origins of plan cost minus that origin's hindsight cost)",
                "'clear winner': a plan that is joint best at both origins, or that beats the overall runner-up by more than the tie band at each "
                "origin; otherwise report 'no clear winner' and list the joint-best plans",
                "best forecast: lowest mean gap MAE across both origins; 'clear' only if lowest at both origins by more than 5 beds"],
            improvement_measures=[
                "value of optimisation: Rule cost - LP cost for each method",
                "saving vs Persistence and vs Seasonal naive: baseline cost - plan cost (the same decision method for both)",
                "share of avoidable cost removed: (baseline cost - plan cost) / (baseline cost - hindsight cost), as a percentage, for baselines "
                "Persistence LP and Seasonal naive LP",
                "forecast skill vs Seasonal naive: 1 - (method MAE / Seasonal naive MAE), for the gap, occupied and available",
                "excess over hindsight, in model units and as a percentage of hindsight cost",
                "all reported per origin and combined"],
            literal_readings=[
                "if more than one plan meets the 'clear winner' definition, every such plan is reported as a joint winner and none is singled out",
                "'runner-up' is the second plan in the overall ranking; exact ties in combined excess keep the fixed plan order",
                "'lowest at both origins by more than 5 beds' means lower than every other method by more than 5 beds at each origin",
                "combined ratios are ratios of sums: share = (sum baseline - sum plan) / (sum baseline - sum hindsight); skill = 1 - mean MAE of the "
                "method / mean MAE of Seasonal naive; excess % = sum of excess / sum of hindsight cost",
                "ranks use realised cost; plans with identical cost share a rank"]),
        disclosures=[
            "Oct 2025 - Sep 2026 actuals were used in earlier, separate tests; the rules are fixed before this run but are not blind",
            "NHS revised Sep 2026 between the September and October 2026 publications (available -87, occupied -2)",
            "the October 2026 publication is used throughout",
            "SARIMA-24 uses all 24 months as labels; the project's original procedure dropped 12 and required at least 30",
            "ExtraTrees-24 uses settings selected for occupied beds on available beds too, and has about 12 training rows",
            "costs are illustrative model units (validated: False)"],
        procedure=["write and hash this config before any forecast", "plan stage: forecasts and plans for both origins from data up to each cut-off; "
                   "hash them", "score stage: verify both hashes, load outcomes, solve hindsight, score once, apply the rules"])


def check_config(out, data_dir):
    """The registered config must be intact and must describe exactly what this code will do."""
    verify_hashes(out / "config.sha256")
    source = json.loads((data_dir / "SOURCE.json").read_text(encoding="utf-8"))
    if json.loads((out / "config.json").read_text(encoding="utf-8")) != json.loads(json.dumps(build_config(source))):
        raise SystemExit("config.json does not match the code; stopping")


# ------------------------------------------------------------------------------------------------------- stages

def register(args):
    out = args.out_dir
    if (out / "plans_frozen.csv").exists():
        raise SystemExit("plans already exist; the config can no longer be written")
    source = json.loads((args.data_dir / "SOURCE.json").read_text(encoding="utf-8"))
    text = json.dumps(build_config(source), indent=1) + "\n"
    path = out / "config.json"
    if path.exists() and path.read_bytes() != text.encode("utf-8"):
        raise SystemExit("config.json exists with different content; stopping")
    write_text(path, text)
    write_text(out / "config.sha256", f"{sha256(path)}  config.json\n")
    print("config.sha256", sha256(path))


def plan(args):
    out = args.out_dir
    check_config(out, args.data_dir)
    if (out / "plans_frozen.csv").exists():
        raise SystemExit("plans are already frozen; stopping")
    import sklearn, statsmodels, pulp
    frozen = []
    log = dict(versions=dict(python=sys.version.split()[0], numpy=np.__version__, pandas=pd.__version__, scikit_learn=sklearn.__version__,
                             statsmodels=statsmodels.__version__, pulp=pulp.__version__), origins={})
    for key, origin in ORIGINS.items():
        data = load(args.data_dir, cutoff=origin)  # nothing after the cut-off is in memory
        fc, log["origins"][key] = forecast_origin(data, origin)
        write_csv(out / key / "forecasts.csv", fc)
        for method in METHODS:
            g = fc[fc.method == method]
            for decision in DECISIONS:
                x = rule_plan(g.forecast_gap) if decision == "Rule" else lp_plan(g.forecast_gap.to_numpy())[0]
                a, r = flows(x)
                frozen += [dict(origin=key, plan=f"{method} {decision}", method=method, decision=decision, month=m, x=x[h], a=a[h], r=r[h])
                           for h, m in enumerate(g.month)]
    write_csv(out / "plans_frozen.csv", pd.DataFrame(frozen))
    write_text(out / "fit_log.json", json.dumps(log, indent=1) + "\n")
    names = ["plans_frozen.csv"] + [f"{key}/forecasts.csv" for key in ORIGINS]
    write_text(out / "plans.sha256", "".join(f"{sha256(out / n)}  {n}\n" for n in names))
    print("plans.sha256", sha256(out / "plans_frozen.csv"))
    for key, entry in log["origins"].items():
        print(key, "SARIMA converged:", {s: v["converged"] for s, v in entry["sarima"].items()},
              "ExtraTrees rows:", {s: v["training_rows"] for s, v in entry["extra_trees"].items()})


def score(args):
    out = args.out_dir
    check_config(out, args.data_dir)
    verify_hashes(out / "plans.sha256")
    if (out / "decision.json").exists():
        raise SystemExit("already scored; stopping")
    frozen = read_csv(out / "plans_frozen.csv")
    data = load(args.data_dir, cutoff=max(target_months(o)[-1] for o in ORIGINS.values()))
    cost, hindsight, per_origin = {}, {}, {}
    mae = {s: {} for s in ("gap",) + SERIES}
    for key, origin in ORIGINS.items():
        months = target_months(origin)
        actual = data.loc[months]
        assert len(actual) == HORIZON
        d = gap(actual.occupied, actual.available).to_numpy()
        write_csv(out / key / "actuals.csv", pd.DataFrame(dict(month=months, occupied=actual.occupied.to_numpy(), available=actual.available.to_numpy(), gap=d)))
        plans = {}
        for p in PLANS:
            g = frozen[(frozen.origin == key) & (frozen.plan == p)].sort_values("month")
            assert list(g.month) == list(months)
            plans[p] = g.x.to_numpy()
        plans["Hindsight"] = lp_plan(d)[0]
        tables = {p: cost_table(x, d) for p, x in plans.items()}
        totals = pd.DataFrame({p: totals_row(t) for p, t in tables.items()}).T
        hindsight[key] = float(totals.loc["Hindsight", "total"])
        ranked, band = rank_origin(totals.loc[list(PLANS), "total"].astype(float), hindsight[key])
        cost[key] = ranked.total
        totals["excess_over_hindsight"] = totals.total - hindsight[key]
        totals["excess_pct_of_hindsight"] = 100 * totals.excess_over_hindsight / hindsight[key]
        totals["rank"] = ranked["rank"].reindex(totals.index)
        totals["joint_best"] = ranked.joint_best.reindex(totals.index)
        totals.insert(0, "decision", [split(p)[1] if p in PLANS else "LP" for p in totals.index])
        totals.insert(0, "method", [split(p)[0] if p in PLANS else "Hindsight" for p in totals.index])
        totals.insert(0, "plan", totals.index)
        write_csv(out / key / "totals.csv", totals)
        write_csv(out / key / "plans.csv", pd.concat([t[["x", "a", "r", "shortfall", "spare", "monthly_cost"]].assign(plan=p, month=months)
                                                      for p, t in tables.items()])[["plan", "month", "x", "a", "r", "shortfall", "spare", "monthly_cost"]])
        fc = read_csv(out / key / "forecasts.csv")
        rows = []
        for m in METHODS:
            g = fc[fc.method == m].sort_values("month")
            assert list(g.month) == list(months)
            err = dict(gap=d - g.forecast_gap.to_numpy(), occupied=actual.occupied.to_numpy() - g.forecast_occupied.to_numpy(),
                       available=actual.available.to_numpy() - g.forecast_available.to_numpy())
            rows.append(dict(method=m, **{f"{s}_mae": float(np.abs(e).mean()) for s, e in err.items()},
                             **{f"{s}_mean_signed_error": float(e.mean()) for s, e in err.items()}))
        acc = pd.DataFrame(rows).set_index("method")
        for s in mae:
            mae[s][key] = acc[f"{s}_mae"]
            acc[f"{s}_skill_vs_seasonal_naive"] = 1 - acc[f"{s}_mae"] / acc.loc["Seasonal naive", f"{s}_mae"]
        write_csv(out / key / "accuracy.csv", acc.reset_index())
        per_origin[key] = dict(hindsight_cost=hindsight[key], cost_tie_band=band, cheapest=ranked.total.idxmin(),
                               joint_best=list(ranked.index[ranked.joint_best]), lowest_gap_mae=acc.gap_mae.idxmin(),
                               value_of_optimisation={m: float(ranked.total[f"{m} Rule"] - ranked.total[f"{m} LP"]) for m in METHODS})
    cost = pd.DataFrame(cost)
    mae = {s: pd.DataFrame(v) for s, v in mae.items()}
    result = overall(cost, hindsight)
    combined = pd.DataFrame(dict(plan=cost.index))
    for key in ORIGINS:
        ranked, _ = rank_origin(cost[key], hindsight[key])
        combined[f"{key}_total"], combined[f"{key}_excess"] = ranked.total.to_numpy(), ranked.excess.to_numpy()
        combined[f"{key}_rank"], combined[f"{key}_joint_best"] = ranked["rank"].to_numpy(), ranked.joint_best.to_numpy()
    combined["combined_excess"] = result["table"].combined_excess.to_numpy()
    combined["overall_rank"] = result["table"].overall_rank.to_numpy()
    write_csv(out / "combined.csv", combined)
    measures = improvements(cost, hindsight, mae)
    write_csv(out / "improvements.csv", measures)
    lp_worse = [f"{m} at {k}" for k in ORIGINS for m in METHODS if cost.loc[f"{m} LP", k] > cost.loc[f"{m} Rule", k]]
    decision = dict(per_origin=per_origin,
                    overall=dict(ranking=result["order"], first=result["first"], runner_up=result["runner_up"],
                                 margins_first_over_runner_up=result["margins_first_over_runner_up"],
                                 beats_runner_up_at_each_origin=result["beats_runner_up_at_each_origin"],
                                 joint_best_at_both=result["joint_best_at_both"], clear_winners=result["clear_winners"],
                                 statement=("no clear winner" if not result["clear_winners"] else "clear winner" if len(result["clear_winners"]) == 1
                                            else "joint winners; the rule does not separate them")),
                    best_forecast=best_forecast(mae["gap"]), lp_worse_than_rule=lp_worse,
                    hashes=dict(config=sha256(out / "config.json"), plans_frozen=sha256(out / "plans_frozen.csv")))
    write_text(out / "decision.json", json.dumps(decision, indent=1) + "\n")
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
