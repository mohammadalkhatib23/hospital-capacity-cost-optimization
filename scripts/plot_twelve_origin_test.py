"""Pre-registered test: ExtraTrees-36 vs Seasonal naive (with SARIMA-24 and SARIMA-36) across the 12 v6 origins.

Run with --project-root pointing to the full NHS workspace. Imports scripts/compare_methods_sep2025.py (forecast, LP and
scoring functions) and scripts/plot_sep2025_windows.py (module loader), both unchanged. extra_trees_24 reads that
module's ORIGIN and DATES constants, so they are set for each origin at run time and restored afterwards.

Order of work: (1) reproduce the published Sep 2025 window-comparison values; (2) write config.json/config.sha256 with
the design, endpoints, tie rules and hypothesis; (3) generate every forecast and plan, save plans.csv and its SHA-256;
(4) score against actuals once. --figures-only rebuilds the figures and README table from the saved CSVs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs/twelve_origin_forecast_test"
FR = "outputs/formulation_robustness"
ORIGINS = pd.date_range("2025-04-01", "2026-03-01", freq="MS")
SELECTION_ORIGIN = pd.Timestamp("2025-09-01")
MODELS = ("Seasonal naive", "ExtraTrees-36", "SARIMA-24", "SARIMA-36")
SHORT = {"Seasonal naive": "SN", "ExtraTrees-36": "ET-36", "SARIMA-24": "SARIMA-24", "SARIMA-36": "SARIMA-36"}
COLOURS = {"Seasonal naive": "#E69F00", "ExtraTrees-36": "#009E73", "SARIMA-24": "#0072B2", "SARIMA-36": "#0072B2"}
LINES = {"Seasonal naive": "-", "ExtraTrees-36": "-", "SARIMA-24": "-", "SARIMA-36": "--"}
COMPARISONS = (("ExtraTrees-36", "Seasonal naive"), ("SARIMA-24", "Seasonal naive"), ("SARIMA-36", "Seasonal naive"), ("ExtraTrees-36", "SARIMA-24"))
GAP_TIE_BEDS, COST_TIE_SHARE = 5.0, 0.01


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_compare():
    spec_path = REPO / "scripts/plot_sep2025_windows.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("_twelve_windows", spec_path)
    windows = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = windows
    spec.loader.exec_module(windows)
    return windows, windows.load_module("compare_methods_sep2025")


def target_months(origin):
    return pd.date_range(origin + pd.DateOffset(months=1), periods=6, freq="MS")


def window(pre, origin, n):
    w = pre.loc[:origin].iloc[-n:]
    assert len(w) == n and w.index[-1] == origin and w.index.equals(pd.date_range(w.index[0], origin, freq="MS"))
    return w


def forecast_origin(m, fe, data, origin):
    """All four forecasts for one origin from data up to and including the origin month only."""
    pre = data.loc[:origin].copy()
    assert pre.index.max() == origin
    dates = target_months(origin)
    A, O = m.A, m.O
    out, meta = {}, []
    prior = dates - pd.DateOffset(years=1)
    assert (prior <= origin).all()
    out["Seasonal naive"] = {t: pre.loc[prior, t].to_numpy(float) for t in (A, O)}
    saved = (m.ORIGIN, m.DATES)
    m.ORIGIN, m.DATES = origin, dates
    try:
        w36 = window(pre, origin, 36)
        et, rows = {}, {}
        for t in (A, O):
            et[t], rows[t] = m.extra_trees_24(fe, w36, t)  # asserts every feature date lies inside the window
        ok = min(rows.values()) >= 12
        if ok:
            out["ExtraTrees-36"] = et
        meta.append(dict(origin=origin, model="ExtraTrees-36", window_start=w36.index[0], window_end=origin, rows_available=rows[A],
                         rows_occupied=rows[O], status="ok" if ok else "excluded: fewer than 12 training rows"))
        for label, n in (("SARIMA-24", 24), ("SARIMA-36", 36)):
            w = window(pre, origin, n)
            res, status = {}, "ok"
            for t in (A, O):
                try:
                    res[t], info = m.sarima_24(w, t)
                    assert info["training_rows"] == n
                except SystemExit as exc:
                    status = f"excluded: {exc}"
            if status == "ok":
                out[label] = res
            meta.append(dict(origin=origin, model=label, window_start=w.index[0], window_end=origin, rows_available=n, rows_occupied=n, status=status))
    finally:
        m.ORIGIN, m.DATES = saved
    meta.append(dict(origin=origin, model="Seasonal naive", window_start=prior[0], window_end=prior[-1], rows_available=np.nan,
                     rows_occupied=np.nan, status="ok"))
    rows = [dict(origin=origin, model=k, horizon=h + 1, month=dates[h], forecast_occupied=v[O][h], forecast_available=v[A][h],
                 forecast_gap=v[O][h] / m.TAU - v[A][h]) for k, v in out.items() for h in range(6)]
    return rows, meta


def plan_rows(m, forecasts, origin):
    rows = []
    for model in MODELS:
        g = forecasts[(forecasts.origin == origin) & (forecasts.model == model)].sort_values("horizon")
        if not len(g):
            continue
        x = m.solve(g.forecast_gap.to_numpy())
        a, r = m.flows(x)
        rows += [dict(origin=origin, plan=f"{model} LP", horizon=h + 1, month=g.month.iloc[h], x=x[h], a=a[h], r=r[h]) for h in range(6)]
    return rows


def reproduce(m, fe, data):
    """Step 1: Sep 2025 must reproduce the published window comparison."""
    rows, _ = forecast_origin(m, fe, data, SELECTION_ORIGIN)
    fc = pd.DataFrame(rows)
    plans = pd.DataFrame(plan_rows(m, fc, SELECTION_ORIGIN))
    dates = target_months(SELECTION_ORIGIN)
    d = data.loc[dates, m.O].to_numpy(float) / m.TAU - data.loc[dates, m.A].to_numpy(float)
    saved_tot = pd.read_csv(REPO / "outputs/sep2025_window_comparison/totals.csv", float_precision="round_trip").set_index("plan").total
    saved_acc = pd.read_csv(REPO / "outputs/sep2025_window_comparison/accuracy.csv", float_precision="round_trip").set_index("model").gap_mae
    expect = {"ExtraTrees-36": (272, "16.00"), "SARIMA-24": (346, "16.108"), "SARIMA-36": (339, "16.11"), "Seasonal naive": (313, "16.137")}
    got = {}
    for model, (mae, cost) in expect.items():
        g = fc[fc.model == model].sort_values("horizon")
        gap_mae = float(np.abs(d - g.forecast_gap.to_numpy()).mean())
        total = m.score(plans[plans.plan == f"{model} LP"].sort_values("horizon").x.to_numpy(), d)["total"]
        assert round(gap_mae) == mae and f"{total / 1e6:.{len(cost.split('.')[1])}f}" == cost, (model, gap_mae, total)
        assert abs(total - saved_tot[f"{model} LP"]) <= 1e-6 * total and abs(gap_mae - saved_acc[model]) <= 1e-6, model
        got[model] = dict(gap_mae=gap_mae, lp_total=total)
    return got


def write_config(root, m):
    src = json.loads((root / "data/raw_v5/SOURCE.json").read_text(encoding="utf-8"))
    cfg = dict(
        name="twelve-origin forecast test (pre-registered)",
        origins=[f"{o:%Y-%m-%d}" for o in ORIGINS], selection_origin=f"{SELECTION_ORIGIN:%Y-%m-%d}",
        cutoff_rule="v6: outcomes up to and including the origin month; forecasts cover the six following months",
        data=dict(file=f"data/raw_v5/{src['file']}", sha256=src["sha256"], loader="formulation_robustness_helpers.v5b_load('2026-09-01')"),
        models={"Seasonal naive": "same calendar month one year earlier",
                "ExtraTrees-36": "ExtraTreesRegressor_target_only_features (forecasting_evaluation.py:759; 300 trees, min_samples_leaf 2, seed 42, "
                                 "16 target-only features, recursive six steps), occupied and available beds, 36 months ending at the origin",
                "SARIMA-24": "order (1,0,0)(0,1,0,12), trend n, StandardScaler, lbfgs, maxiter 400, all 24 months ending at the origin as labels",
                "SARIMA-36": "as SARIMA-24 with the 36 months ending at the origin"},
        exclusion_rules="SARIMA non-convergence, or fewer than 12 ExtraTrees training rows for a target: the model-origin is excluded from summaries",
        lp=dict(running=m.C, opening=m.A_PLUS, closing=m.A_MINUS, shortfall=m.P, cap=m.X_MAX, ramp=m.RAMP, x0=0, terminal_charge=0, tau=m.TAU),
        hindsight="saved v6 E0 Hindsight plans and scores", seasonal_naive_check="recomputed SN LP must match saved v6 plan and cost within 1e-6 relative",
        primary=dict(comparison="ExtraTrees-36 vs Seasonal naive, per origin",
                     E1="gap MAE (beds) over the six months: better if lower by more than 5 beds; tie if within 5 beds; otherwise loss",
                     E2="LP realised cost: better if lower by more than 1% of that origin's hindsight cost; tie within that band; otherwise loss"),
        secondary=[f"{a} vs {b}" for a, b in COMPARISONS[1:]],
        summaries=dict(S1="wins/ties/losses per comparison and endpoint", S2="mean gap MAE and mean LP cost per model",
                       S3="non-overlapping subsets: offset r = 0..5, origins r and r+6 (0-based in time order); ET-36 vs SN wins/ties/losses per subset",
                       S4="mean absolute gap error by horizon 1..6 per model", populations=["all 12 origins", "11 origins excluding 2025-09-01"]),
        hypothesis=("ExtraTrees-36 has lower gap MAE than Seasonal naive at a majority of origins (E1 wins > half of the origins) "
                    "and lower mean LP cost than Seasonal naive; assessed separately for all 12 and for the 11 excluding Sep 2025"),
        reused_code={p: sha(REPO / p) for p in ("scripts/compare_methods_sep2025.py", "scripts/plot_sep2025_windows.py")},
        amendments=[])
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "config.json"
    text = json.dumps(cfg, indent=1)
    if path.exists():
        assert path.read_text(encoding="utf-8") == text, "config.json differs from the frozen design"
        assert (OUT / "config.sha256").read_text().split()[0] == sha(path)
    else:
        path.write_text(text, encoding="utf-8")
        (OUT / "config.sha256").write_text(f"{sha(path)}  config.json\n", encoding="utf-8")
    return sha(path)


def classify(diff, band):
    """diff = challenger advantage (benchmark minus challenger); positive means the challenger is better."""
    return "win" if diff > band else "loss" if diff < -band else "tie"


def run(root):
    windows, m = load_compare()
    helpers, fe = m.module(root, "formulation_robustness_helpers"), m.module(root, "forecasting_evaluation")
    data = helpers.v5b_load("2026-09-01")
    repro = reproduce(m, fe, data)
    print("Step 1 reproduction passed:", json.dumps({k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in repro.items()}))
    config_hash = write_config(root, m)
    print("config.sha256", config_hash)

    rows, meta = [], []
    for origin in ORIGINS:
        r, mt = forecast_origin(m, fe, data, origin)
        rows += r
        meta += mt
    forecasts = pd.DataFrame(rows)
    plans = pd.DataFrame([row for o in ORIGINS for row in plan_rows(m, forecasts, o)])
    forecasts.to_csv(OUT / "forecasts.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    plans.to_csv(OUT / "plans.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    plans_hash = sha(OUT / "plans.csv")
    (OUT / "plans.sha256").write_text(f"{plans_hash}  plans.csv\n", encoding="utf-8")
    print("plans.sha256", plans_hash)

    # Scoring (once): actuals, saved v6 hindsight and Seasonal naive checks.
    saved_plans = pd.read_csv(root / f"{FR}/plans_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    saved_hind = pd.read_csv(root / f"{FR}/tables/hindsight_plans_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    saved_scores = pd.read_csv(root / f"{FR}/tables/scores_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    e0 = lambda f, o, p: f[f.origin.eq(o) & f.environment.eq("E0") & f.policy.eq(p)].iloc[0]
    xs = [f"x{k}" for k in range(1, 7)]
    cost_rows, totals, acc, sn_check = [], [], [], 0.0
    for origin in ORIGINS:
        dates = target_months(origin)
        d = data.loc[dates, m.O].to_numpy(float) / m.TAU - data.loc[dates, m.A].to_numpy(float)
        hx = e0(saved_hind, origin, "Hindsight")[xs].to_numpy(float)
        hcost = float(e0(saved_scores, origin, "Hindsight").cost)
        assert abs(m.score(hx, d)["total"] - hcost) <= 1e-6 * hcost
        g = plans[plans.origin == origin]
        items = [(p, g[g.plan == p].sort_values("horizon").x.to_numpy()) for p in g.plan.unique()] + [("Hindsight", hx)]
        for plan, x in items:
            a, r = m.flows(x)
            short, spare = np.maximum(d - x, 0), np.maximum(x - d, 0)
            for h in range(6):
                parts = dict(running=m.C * x[h], opening=m.A_PLUS * a[h], closing=m.A_MINUS * r[h], shortfall_cost=m.P * short[h])
                cost_rows.append(dict(origin=origin, plan=plan, horizon=h + 1, month=dates[h], shortfall=short[h], spare=spare[h], **parts,
                                      monthly_cost=sum(parts.values())))
            sc = m.score(x, d)
            totals.append(dict(origin=origin, plan=plan, operating=sc["operating"], shortfall_bed_months=sc["shortfall_bed_months"],
                               shortfall_cost=sc["shortfall_cost"], total=sc["total"], hindsight=hcost, excess_over_hindsight=sc["total"] - hcost))
            if plan == "Seasonal naive LP":
                ref_x = e0(saved_plans, origin, "Seasonal naive")[xs].to_numpy(float)
                ref_c = float(e0(saved_scores, origin, "Seasonal naive").cost)
                sn_check = max(sn_check, float(np.max(np.abs(x - ref_x) / np.maximum(np.abs(ref_x), 1))), abs(sc["total"] - ref_c) / ref_c)
        fo = forecasts[forecasts.origin == origin]
        for model in fo.model.unique():
            f = fo[fo.model == model].sort_values("horizon")
            err = d - f.forecast_gap.to_numpy()
            acc.append(dict(origin=origin, model=model, gap_mae=np.abs(err).mean(), mean_signed_gap_error=err.mean(),
                            occupied_mae=np.abs(data.loc[dates, m.O].to_numpy() - f.forecast_occupied.to_numpy()).mean(),
                            available_mae=np.abs(data.loc[dates, m.A].to_numpy() - f.forecast_available.to_numpy()).mean(),
                            **{f"abs_gap_error_h{h + 1}": abs(err[h]) for h in range(6)}))
    assert sn_check <= 1e-6, sn_check
    assert sha(OUT / "plans.csv") == plans_hash
    accuracy = pd.DataFrame(acc).merge(pd.DataFrame(meta)[["origin", "model", "window_start", "window_end", "rows_available", "rows_occupied", "status"]],
                                       on=["origin", "model"], how="left")
    pd.DataFrame(cost_rows).to_csv(OUT / "monthly_costs.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    pd.DataFrame(totals).to_csv(OUT / "totals.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    accuracy.to_csv(OUT / "accuracy.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    excluded = pd.DataFrame(meta).query("status != 'ok'")
    print("Seasonal naive LP vs saved v6, max relative difference", f"{sn_check:.3g}")
    print("exclusions:", "none" if excluded.empty else excluded.to_string())
    print(pd.DataFrame(meta)[["origin", "model", "rows_available", "rows_occupied"]].pivot(index="origin", columns="model", values="rows_occupied").to_string())


def per_origin():
    acc = pd.read_csv(OUT / "accuracy.csv", parse_dates=["origin"], float_precision="round_trip")
    tot = pd.read_csv(OUT / "totals.csv", parse_dates=["origin"], float_precision="round_trip")
    acc = acc[acc.status.eq("ok")]
    gap = acc.pivot(index="origin", columns="model", values="gap_mae")
    cost = tot[tot.plan.ne("Hindsight")].assign(model=lambda f: f.plan.str.replace(" LP", "")).pivot(index="origin", columns="model", values="total")
    cost = cost[gap.columns]
    hind = tot[tot.plan.eq("Hindsight")].set_index("origin").total
    return acc, gap, cost, hind


def summaries():
    acc, gap, cost, hind = per_origin()
    res = {}
    for label, keep in (("all", gap.index), ("excl", gap.index[gap.index != SELECTION_ORIGIN])):
        s = {}
        for a, b in COMPARISONS:
            idx = [o for o in keep if not (np.isnan(gap.loc[o, a]) or np.isnan(gap.loc[o, b]))]
            e1 = [classify(gap.loc[o, b] - gap.loc[o, a], GAP_TIE_BEDS) for o in idx]
            e2 = [classify(cost.loc[o, b] - cost.loc[o, a], COST_TIE_SHARE * hind[o]) for o in idx]
            s[f"{a} vs {b}"] = dict(n=len(idx), E1={k: e1.count(k) for k in ("win", "tie", "loss")}, E2={k: e2.count(k) for k in ("win", "tie", "loss")})
        s["mean_gap_mae"] = gap.loc[keep].mean().to_dict()
        s["mean_lp_cost"] = cost.loc[keep].mean().to_dict()
        s["mean_hindsight"] = float(hind.loc[keep].mean())
        horizon = acc[acc.origin.isin(keep)].groupby("model")[[f"abs_gap_error_h{h}" for h in range(1, 7)]].mean()
        s["S4"] = horizon.to_dict(orient="index")
        p = s["ExtraTrees-36 vs Seasonal naive"]
        s["hypothesis_held"] = bool(p["E1"]["win"] > p["n"] / 2 and s["mean_lp_cost"]["ExtraTrees-36"] < s["mean_lp_cost"]["Seasonal naive"])
        res[label] = s
    subsets = []
    for r in range(6):
        idx = [gap.index[r], gap.index[r + 6]]
        e1 = [classify(gap.loc[o, "Seasonal naive"] - gap.loc[o, "ExtraTrees-36"], GAP_TIE_BEDS) for o in idx]
        e2 = [classify(cost.loc[o, "Seasonal naive"] - cost.loc[o, "ExtraTrees-36"], COST_TIE_SHARE * hind[o]) for o in idx]
        subsets.append(dict(offset=r, origins=[f"{o:%b %Y}" for o in idx], E1={k: e1.count(k) for k in ("win", "tie", "loss")},
                            E2={k: e2.count(k) for k in ("win", "tie", "loss")}))
    res["S3"] = subsets
    return res


def readme_table():
    acc, gap, cost, hind = per_origin()
    s = summaries()
    head = ("| Origin | Gap MAE: SN / ET-36 / SARIMA-24 / SARIMA-36 | LP cost: SN / ET-36 / SARIMA-24 / SARIMA-36 | Hindsight | ET-36 vs SN (gap) | ET-36 vs SN (cost) |\n"
            "| --- | ---: | ---: | ---: | :---: | :---: |")
    fmt_g = lambda v: "-" if np.isnan(v) else f"{v:,.0f}"
    fmt_c = lambda v: "-" if np.isnan(v) else f"{v / 1e6:.2f}"
    lines = [head]
    for o in gap.index:
        e1 = classify(gap.loc[o, "Seasonal naive"] - gap.loc[o, "ExtraTrees-36"], GAP_TIE_BEDS)
        e2 = classify(cost.loc[o, "Seasonal naive"] - cost.loc[o, "ExtraTrees-36"], COST_TIE_SHARE * hind[o])
        label = f"{o:%b %Y}" + (" (selection origin)" if o == SELECTION_ORIGIN else "")
        lines.append(f"| {label} | {' / '.join(fmt_g(gap.loc[o, k]) for k in MODELS)} | {' / '.join(fmt_c(cost.loc[o, k]) for k in MODELS)} | "
                     f"{hind[o] / 1e6:.2f} | {e1} | {e2} |")
    for key, name in (("all", "all 12"), ("excl", "excluding Sep 2025")):
        r = s[key]
        lines.append(f"| **Mean, {name}** | {' / '.join(fmt_g(r['mean_gap_mae'][k]) for k in MODELS)} | "
                     f"{' / '.join(fmt_c(r['mean_lp_cost'][k]) for k in MODELS)} | {r['mean_hindsight'] / 1e6:.2f} | | |")
    for key, name in (("all", "all 12"), ("excl", "excluding Sep 2025")):
        p = s[key]["ExtraTrees-36 vs Seasonal naive"]
        wtl = lambda e: f"{e['win']} / {e['tie']} / {e['loss']}"
        lines.append(f"| **Win / tie / loss, {name}** | | | | {wtl(p['E1'])} | {wtl(p['E2'])} |")
    return "\n".join(lines)


def save(fig, stem, plt):
    fig.savefig(REPO / "figures" / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(REPO / "figures" / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def style(ax, plt):
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def figures(plt):
    acc, gap, cost, hind = per_origin()
    s = summaries()
    labels = [f"{o:%b}\n{o:%Y}" for o in gap.index]
    xpos = np.arange(len(gap.index))
    sel = list(gap.index).index(SELECTION_ORIGIN)

    for stem, frame, ylabel, title, scale in (
            ("twelve_origin_gap_mae", gap, "Gap MAE (beds)", "Gap MAE by origin (six months ahead)", 1.0),
            ("twelve_origin_excess_cost", cost.sub(hind, axis=0), "LP cost minus hindsight (million model units)", "LP cost above hindsight by origin", 1e6)):
        fig, ax = plt.subplots(figsize=(10, 4.8))
        ax.axvspan(sel - 0.4, sel + 0.4, color="#eeeeee", zorder=0)
        ax.text(sel, 1.0, "selection origin", transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=8, color="#555555")
        for model in MODELS:
            ax.plot(xpos, frame[model] / scale, color=COLOURS[model], ls=LINES[model], lw=2, marker="o", ms=5, label=model, zorder=3)
        if scale != 1.0:
            ax.axhline(0, color="black", lw=1.0, zorder=2)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}"))
        else:
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.set_xticks(xpos, labels)
        ax.set_xlabel("Forecast origin")
        ax.set_ylabel(ylabel)
        ax.set_ylim(bottom=0 if scale == 1.0 else min(-0.2, ax.get_ylim()[0]))
        style(ax, plt)
        ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2)
        ax.set_title(title, loc="left", fontweight="semibold", pad=16)
        fig.tight_layout()
        save(fig, stem, plt)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    groups = [("Gap MAE\nall 12", s["all"]["ExtraTrees-36 vs Seasonal naive"]["E1"]), ("Gap MAE\nexcl. Sep 2025", s["excl"]["ExtraTrees-36 vs Seasonal naive"]["E1"]),
              ("LP cost\nall 12", s["all"]["ExtraTrees-36 vs Seasonal naive"]["E2"]), ("LP cost\nexcl. Sep 2025", s["excl"]["ExtraTrees-36 vs Seasonal naive"]["E2"])]
    tones = {"win": "#009E73", "tie": "#b4b4b4", "loss": "#E69F00"}
    names = {"win": "ET-36 better", "tie": "Tie", "loss": "Seasonal naive better"}
    width = 0.26
    for k, outcome in enumerate(("win", "tie", "loss")):
        vals = [g[1][outcome] for g in groups]
        bars = ax.bar(np.arange(4) + (k - 1) * width, vals, width, color=tones[outcome], label=names[outcome], zorder=2)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.1, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(4), [g[0] for g in groups])
    ax.set_ylabel("Number of origins")
    ax.set_ylim(0, 12.5)
    style(ax, plt)
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    ax.set_title("ExtraTrees-36 vs Seasonal naive: wins, ties and losses", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "twelve_origin_win_counts", plt)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    width = 0.2
    for k, model in enumerate(MODELS):
        vals = [s["all"]["S4"][model][f"abs_gap_error_h{h}"] for h in range(1, 7)]
        ax.bar(np.arange(6) + (k - 1.5) * width, vals, width, color=COLOURS[model], label=model, zorder=2,
               hatch="////" if model == "SARIMA-36" else None, edgecolor="white" if model != "SARIMA-36" else "#0072B2",
               fill=model != "SARIMA-36")
    ax.set_xticks(range(6), [str(h) for h in range(1, 7)])
    ax.set_xlabel("Months ahead")
    ax.set_ylabel("Mean absolute gap error (beds)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    style(ax, plt)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2)
    ax.set_title("Mean absolute gap error by months ahead (12 origins; SARIMA-24: 10)", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "twelve_origin_error_by_horizon", plt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--figures-only", action="store_true")
    args = parser.parse_args()
    if not args.figures_only:
        run(args.project_root.resolve())
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "twelve-origin", "hatch.linewidth": 0.6})
    figures(plt)
    print(json.dumps(summaries(), indent=1, default=str))
    print(readme_table())


if __name__ == "__main__":
    main()
