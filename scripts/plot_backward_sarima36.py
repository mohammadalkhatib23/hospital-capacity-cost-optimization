"""Pre-registered backward test: SARIMA-36 vs Seasonal naive at every feasible origin before April 2025.

Run with --project-root pointing to the full NHS workspace. Imports scripts/compare_methods_sep2025.py (SARIMA, LP and
scoring functions, via the twelve-origin script's loader) and scripts/plot_twelve_origin_test.py (windows, target months,
tie classification), both unchanged.

Order of work: (0) origin set by rule; (1) reproduce SARIMA-36 and Seasonal naive at Apr 2025 from the twelve-origin
outputs; (2) write config.json/config.sha256; (3) generate every forecast and plan, save plans.csv and plans.sha256, then
score once. --figures-only rebuilds the figures and README table from the saved CSVs.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs/backward_sarima36_test"
TWELVE = REPO / "outputs/twelve_origin_forecast_test"
LAST_ORIGIN = pd.Timestamp("2025-03-01")
V6_FIRST_TARGET = pd.Timestamp("2025-05-01")
PERIOD_A_END = pd.Timestamp("2024-12-01")
MODELS = ("Seasonal naive", "SARIMA-36")
COLOURS = {"Seasonal naive": "#E69F00", "SARIMA-36": "#0072B2"}
GAP_TIE_BEDS, COST_TIE_SHARE = 5.0, 0.01


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_twelve():
    spec = importlib.util.spec_from_file_location("_backward_twelve", REPO / "scripts/plot_twelve_origin_test.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def origin_set(data):
    """Every origin with 36 published months ending at it, up to and including Mar 2025."""
    first = data.index[35]
    origins = pd.date_range(first, LAST_ORIGIN, freq="MS")
    assert all(len(data.loc[:o]) >= 36 for o in origins) and len(data.loc[:origins[0]]) == 36
    return origins


def forecast(t, m, data, origin):
    pre = data.loc[:origin].copy()
    assert pre.index.max() == origin
    dates = t.target_months(origin)
    prior = dates - pd.DateOffset(years=1)
    assert (prior <= origin).all() and (prior >= pre.index[0]).all()
    out = {"Seasonal naive": {k: pre.loc[prior, k].to_numpy(float) for k in (m.A, m.O)}}
    w = t.window(pre, origin, 36)  # 36 months ending at the origin, contiguous
    status, res = "ok", {}
    for k in (m.A, m.O):
        try:
            res[k], info = m.sarima_24(w, k)
            assert info["training_rows"] == 36
        except SystemExit as exc:
            status = f"excluded: {exc}"
    if status == "ok":
        out["SARIMA-36"] = res
    rows = [dict(origin=origin, model=name, horizon=h + 1, month=dates[h], forecast_occupied=v[m.O][h], forecast_available=v[m.A][h],
                 forecast_gap=v[m.O][h] / m.TAU - v[m.A][h]) for name, v in out.items() for h in range(6)]
    return rows, dict(origin=origin, window_start=w.index[0], window_end=origin, sarima_labels=36, status=status)


def reproduce(t, m, data):
    origin = pd.Timestamp("2025-04-01")
    rows, _ = forecast(t, m, data, origin)
    fc = pd.DataFrame(rows)
    dates = t.target_months(origin)
    d = data.loc[dates, m.O].to_numpy(float) / m.TAU - data.loc[dates, m.A].to_numpy(float)
    acc = pd.read_csv(TWELVE / "accuracy.csv", parse_dates=["origin"], float_precision="round_trip")
    tot = pd.read_csv(TWELVE / "totals.csv", parse_dates=["origin"], float_precision="round_trip")
    got = {}
    for model in MODELS:
        g = fc[fc.model == model].sort_values("horizon").forecast_gap.to_numpy()
        mae = float(np.abs(d - g).mean())
        total = m.score(m.solve(g), d)["total"]
        ref_mae = float(acc[acc.origin.eq(origin) & acc.model.eq(model)].gap_mae.iloc[0])
        ref_tot = float(tot[tot.origin.eq(origin) & tot.plan.eq(f"{model} LP")].total.iloc[0])
        assert abs(mae - ref_mae) <= 1e-6 and abs(total - ref_tot) <= 1e-6 * ref_tot, (model, mae, ref_mae, total, ref_tot)
        got[model] = dict(gap_mae=mae, lp_total=total)
    return got


def write_config(root, m, origins):
    src = json.loads((root / "data/raw_v5/SOURCE.json").read_text(encoding="utf-8"))
    overlapping = [o for o in origins if o + pd.DateOffset(months=6) >= V6_FIRST_TARGET]
    cfg = dict(
        name="backward test: SARIMA-36 vs Seasonal naive before April 2025 (pre-registered)",
        origin_rule="every month o with 36 published months ending at o (v6 cut-off: up to and including o), up to and including 2025-03-01",
        origins=[f"{o:%Y-%m-%d}" for o in origins],
        overlapping_origins=[f"{o:%Y-%m-%d}" for o in overlapping],
        overlap_rule="an origin overlaps if any of its six target months is May 2025 or later (the v6 origins' targets)",
        periods={"A": "origins up to 2024-12-01", "B": "origins 2025-01-01 to 2025-03-01"},
        data=dict(file=f"data/raw_v5/{src['file']}", sha256=src["sha256"], loader="formulation_robustness_helpers.v5b_load('2026-09-01')"),
        models={"SARIMA-36": "order (1,0,0)(0,1,0,12), trend n, StandardScaler, lbfgs, maxiter 400, all 36 months ending at the origin as labels",
                "Seasonal naive": "same calendar month one year earlier"},
        exclusion_rule="SARIMA non-convergence for either target excludes that origin",
        lp=dict(running=m.C, opening=m.A_PLUS, closing=m.A_MINUS, shortfall=m.P, cap=m.X_MAX, ramp=m.RAMP, x0=0, terminal_charge=0, tau=m.TAU),
        hindsight="the same LP solved per origin with the actual gaps",
        primary="per origin LP realised cost, SARIMA-36 vs Seasonal naive: win if lower by more than 1% of that origin's hindsight cost; tie within that band; otherwise loss",
        secondary=["gap MAE: win if lower by more than 5 beds; tie within 5 beds", "occupied MAE and available MAE (reported)"],
        summaries=dict(S1="wins/ties/losses on cost and on gap MAE", S2="mean cost, mean gap MAE, mean SARIMA-36 minus SN cost",
                       S3="non-overlapping subsets: every sixth origin, offsets r = 0..5 (0-based in time order), win/tie/loss on cost",
                       S4="cost differences sorted with cumulative sum",
                       populations=["all backward origins", "Period A", "Period B", "non-overlapping origins only"]),
        hypothesis="SARIMA-36 has lower LP cost than Seasonal naive at a majority of backward origins (cost wins > half) and lower mean cost; "
                   "assessed for all backward origins, Period A and Period B",
        reused_code={p: sha(REPO / p) for p in ("scripts/compare_methods_sep2025.py", "scripts/plot_twelve_origin_test.py")},
        amendments=[])
    OUT.mkdir(parents=True, exist_ok=True)
    path, text = OUT / "config.json", json.dumps(cfg, indent=1)
    if path.exists():
        assert path.read_text(encoding="utf-8") == text, "config.json differs from the frozen design"
    else:
        path.write_text(text, encoding="utf-8")
        (OUT / "config.sha256").write_text(f"{sha(path)}  config.json\n", encoding="utf-8")
    assert (OUT / "config.sha256").read_text().split()[0] == sha(path)
    return sha(path)


def run(root):
    t = load_twelve()
    _, m = t.load_compare()
    helpers = m.module(root, "formulation_robustness_helpers")
    data = helpers.v5b_load("2026-09-01")
    origins = origin_set(data)
    print("origins:", ", ".join(f"{o:%b %Y}" for o in origins))
    print("Step 1 reproduction passed:", json.dumps({k: {kk: round(v, 3) for kk, v in d.items()} for k, d in reproduce(t, m, data).items()}))
    print("config.sha256", write_config(root, m, origins))

    rows, meta = [], []
    for o in origins:
        r, mt = forecast(t, m, data, o)
        rows += r
        meta.append(mt)
    fc = pd.DataFrame(rows)
    plans = []
    for o in origins:
        for model in MODELS:
            g = fc[(fc.origin == o) & (fc.model == model)].sort_values("horizon")
            if len(g):
                x = m.solve(g.forecast_gap.to_numpy())
                a, r = m.flows(x)
                plans += [dict(origin=o, plan=f"{model} LP", horizon=h + 1, month=g.month.iloc[h], x=x[h], a=a[h], r=r[h]) for h in range(6)]
    plans = pd.DataFrame(plans)
    fc.to_csv(OUT / "forecasts.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    plans.to_csv(OUT / "plans.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    plans_hash = sha(OUT / "plans.csv")
    (OUT / "plans.sha256").write_text(f"{plans_hash}  plans.csv\n", encoding="utf-8")
    print("plans.sha256", plans_hash)

    totals, acc = [], []
    for o in origins:
        dates = t.target_months(o)
        occ, av = data.loc[dates, m.O].to_numpy(float), data.loc[dates, m.A].to_numpy(float)
        d = occ / m.TAU - av
        hx = m.solve(d)
        hcost = m.score(hx, d)["total"]
        g = plans[plans.origin == o]
        for plan, x in [(p, g[g.plan == p].sort_values("horizon").x.to_numpy()) for p in g.plan.unique()] + [("Hindsight", hx)]:
            sc = m.score(x, d)
            totals.append(dict(origin=o, plan=plan, operating=sc["operating"], shortfall_bed_months=sc["shortfall_bed_months"],
                               shortfall_cost=sc["shortfall_cost"], total=sc["total"], hindsight=hcost, excess_over_hindsight=sc["total"] - hcost,
                               **({f"x{h + 1}": x[h] for h in range(6)} if plan == "Hindsight" else {})))
        for model in fc[fc.origin == o].model.unique():
            f = fc[(fc.origin == o) & (fc.model == model)].sort_values("horizon")
            acc.append(dict(origin=o, model=model, gap_mae=np.abs(d - f.forecast_gap.to_numpy()).mean(),
                            mean_signed_gap_error=(d - f.forecast_gap.to_numpy()).mean(),
                            occupied_mae=np.abs(occ - f.forecast_occupied.to_numpy()).mean(), available_mae=np.abs(av - f.forecast_available.to_numpy()).mean()))
    assert sha(OUT / "plans.csv") == plans_hash
    totals = pd.DataFrame(totals)
    assert (totals.total >= totals.hindsight * (1 - 1e-9)).all()
    accuracy = pd.DataFrame(acc).merge(pd.DataFrame(meta), on="origin", how="left")
    totals.to_csv(OUT / "totals.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    accuracy.to_csv(OUT / "accuracy.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    bad = [x for x in meta if x["status"] != "ok"]
    print("exclusions:", "none" if not bad else bad)


def frame():
    t = load_twelve()
    acc = pd.read_csv(OUT / "accuracy.csv", parse_dates=["origin"], float_precision="round_trip")
    tot = pd.read_csv(OUT / "totals.csv", parse_dates=["origin"], float_precision="round_trip")
    cfg = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    ok = acc.groupby("origin").status.first().eq("ok")
    origins = [o for o in pd.to_datetime(cfg["origins"]) if ok.get(o, False)]
    gap = acc.pivot(index="origin", columns="model", values="gap_mae").loc[origins]
    cost = tot[tot.plan.ne("Hindsight")].assign(model=lambda f: f.plan.str.replace(" LP", "")).pivot(index="origin", columns="model", values="total").loc[origins]
    hind = tot[tot.plan.eq("Hindsight")].set_index("origin").total.loc[origins]
    f = pd.DataFrame(dict(sn_gap=gap["Seasonal naive"], s36_gap=gap["SARIMA-36"], sn_cost=cost["Seasonal naive"], s36_cost=cost["SARIMA-36"], hindsight=hind))
    f["diff"] = f.s36_cost - f.sn_cost
    f["cost_result"] = [t.classify(-x, COST_TIE_SHARE * h) for x, h in zip(f["diff"], f.hindsight)]
    f["gap_result"] = [t.classify(a - b, GAP_TIE_BEDS) for a, b in zip(f.sn_gap, f.s36_gap)]
    f["period"] = np.where(f.index <= PERIOD_A_END, "A", "B")
    over = set(pd.to_datetime(cfg["overlapping_origins"]))
    f["overlapping"] = [o in over for o in f.index]
    return f, cfg


def summaries():
    f, _ = frame()
    pops = {"All backward": f, "Period A": f[f.period == "A"], "Period B": f[f.period == "B"], "Non-overlapping": f[~f.overlapping]}
    out = {}
    for name, g in pops.items():
        wtl = lambda col: {k: int((g[col] == k).sum()) for k in ("win", "tie", "loss")}
        cost_w = wtl("cost_result")
        out[name] = dict(n=len(g), cost=cost_w, gap=wtl("gap_result"), mean_sn_cost=g.sn_cost.mean(), mean_s36_cost=g.s36_cost.mean(),
                         mean_sn_gap=g.sn_gap.mean(), mean_s36_gap=g.s36_gap.mean(), mean_diff=g["diff"].mean(),
                         hypothesis_held=bool(cost_w["win"] > len(g) / 2 and g.s36_cost.mean() < g.sn_cost.mean()))
    out["S3"] = [dict(offset=r, origins=[f"{o:%b %Y}" for o in f.index[r::6]],
                      cost={k: int((f.cost_result.iloc[r::6] == k).sum()) for k in ("win", "tie", "loss")}) for r in range(6)]
    s4 = f[["diff", "cost_result"]].sort_values("diff").copy()
    s4["cumulative"] = s4["diff"].cumsum()
    out["S4"] = s4
    return out


def readme_table():
    f, _ = frame()
    s = summaries()
    lines = ["| Origin | Period | Gap MAE: SN / SARIMA-36 | LP cost: SN / SARIMA-36 | Hindsight | SARIMA-36 - SN | Cost result |",
             "| --- | :---: | ---: | ---: | ---: | ---: | :---: |"]
    for o, r in f.iterrows():
        label = f"{o:%b %Y}" + (" (overlapping)" if r.overlapping else "")
        lines.append(f"| {label} | {r.period} | {r.sn_gap:,.0f} / {r.s36_gap:,.0f} | {r.sn_cost / 1e6:.2f} / {r.s36_cost / 1e6:.2f} | "
                     f"{r.hindsight / 1e6:.2f} | {r['diff'] / 1e6:+.2f} | {r.cost_result} |")
    for name in ("All backward", "Period A", "Period B", "Non-overlapping"):
        x = s[name]
        c, g = x["cost"], x["gap"]
        lines.append(f"| **{name} (n = {x['n']})** | | mean {x['mean_sn_gap']:,.0f} / {x['mean_s36_gap']:,.0f}; W/T/L {g['win']} / {g['tie']} / {g['loss']} | "
                     f"mean {x['mean_sn_cost'] / 1e6:.2f} / {x['mean_s36_cost'] / 1e6:.2f} | | mean {x['mean_diff'] / 1e6:+.2f} | "
                     f"{c['win']} / {c['tie']} / {c['loss']} |")
    return "\n".join(lines)


def style(ax):
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def save(fig, stem, plt):
    fig.savefig(REPO / "figures" / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(REPO / "figures" / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def figures(plt):
    f, _ = frame()
    s = summaries()
    tw = pd.read_csv(TWELVE / "totals.csv", parse_dates=["origin"], float_precision="round_trip")
    tw = tw[tw.plan.isin(["Seasonal naive LP", "SARIMA-36 LP"])].pivot(index="origin", columns="plan", values="excess_over_hindsight")
    excess = pd.concat([pd.DataFrame({"Seasonal naive": (f.sn_cost - f.hindsight), "SARIMA-36": (f.s36_cost - f.hindsight)}),
                        pd.DataFrame({"Seasonal naive": tw["Seasonal naive LP"], "SARIMA-36": tw["SARIMA-36 LP"]})]) / 1e6
    xpos = np.arange(len(excess))
    nb = len(f)
    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.axvspan(nb - 0.5, len(excess) - 0.5, color="#eeeeee", zorder=0)
    ax.text((nb + len(excess) - 1) / 2, 1.0, "selection origins (v6, Apr 2025 - Mar 2026)", transform=ax.get_xaxis_transform(), ha="center",
            va="bottom", fontsize=8.5, color="#555555")
    for model in MODELS:
        ax.plot(xpos, excess[model], color=COLOURS[model], lw=2, marker="o", ms=4, label=model, zorder=3)
    ax.axhline(0, color="black", lw=1)
    ax.set_xticks(xpos, [f"{o:%b}\n{o:%y}" for o in excess.index], fontsize=8)
    ax.set_xlabel("Forecast origin")
    ax.set_ylabel("LP cost minus hindsight (million model units)")
    style(ax)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_title("LP cost above hindsight: backward origins and selection origins", loc="left", fontweight="semibold", pad=16)
    fig.tight_layout()
    save(fig, "backward_excess_cost", plt)

    fig, ax = plt.subplots(figsize=(11, 4.8))
    xb = np.arange(nb)
    band = COST_TIE_SHARE * f.hindsight.to_numpy() / 1e6
    ax.fill_between(np.concatenate([[-0.5], xb, [nb - 0.5]]), -np.concatenate([[band[0]], band, [band[-1]]]),
                    np.concatenate([[band[0]], band, [band[-1]]]), step="mid", color="#dddddd", zorder=0, label="Tie band (1% of hindsight)")
    colours = ["#0072B2" if v < 0 else "#E69F00" for v in f["diff"]]
    ax.bar(xb, f["diff"] / 1e6, 0.7, color=colours, zorder=2)
    for x, v in zip(xb, f["diff"] / 1e6):
        ax.text(x, v + (0.04 if v >= 0 else -0.04), f"{v:+.2f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=7.5)
    ax.axhline(0, color="black", lw=1)
    ax.set_xticks(xb, [f"{o:%b}\n{o:%y}" for o in f.index], fontsize=8)
    ax.set_xlabel("Forecast origin")
    ax.set_ylabel("SARIMA-36 minus Seasonal naive cost (million)")
    style(ax)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color="#0072B2", label="SARIMA-36 cheaper"), Patch(color="#E69F00", label="Seasonal naive cheaper"),
                       Patch(color="#dddddd", label="Tie band (1% of hindsight)")], loc="upper right", frameon=False, fontsize=9)
    ax.set_title("SARIMA-36 minus Seasonal naive LP cost, backward origins", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "backward_cost_difference", plt)

    fig, ax = plt.subplots(figsize=(11, 4.8))
    groups = [(f"{lab}\n{name}", s[name][key]) for key, lab in (("cost", "LP cost"), ("gap", "Gap MAE")) for name in ("All backward", "Period A", "Period B")]
    tones = {"win": "#0072B2", "tie": "#b4b4b4", "loss": "#E69F00"}
    names = {"win": "SARIMA-36 better", "tie": "Tie", "loss": "Seasonal naive better"}
    width = 0.26
    for k, outcome in enumerate(("win", "tie", "loss")):
        vals = [g[1][outcome] for g in groups]
        bars = ax.bar(np.arange(len(groups)) + (k - 1) * width, vals, width, color=tones[outcome], label=names[outcome], zorder=2)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.1, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(groups)), [g[0] for g in groups])
    ax.set_ylabel("Number of origins")
    ax.set_ylim(0, max(s["All backward"]["n"], 1) + 1.5)
    ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style(ax)
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    ax.set_title("SARIMA-36 vs Seasonal naive: wins, ties and losses (backward origins)", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "backward_win_counts", plt)


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
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "backward-sarima36"})
    figures(plt)
    s = summaries()
    for k, v in s.items():
        if k == "S4":
            print((v.assign(diff=v["diff"] / 1e6, cumulative=v.cumulative / 1e6)).round(3).to_string())
        else:
            print(k, v)
    print(readme_table())


if __name__ == "__main__":
    main()
