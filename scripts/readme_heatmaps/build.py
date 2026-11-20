"""Build the README and its heatmaps from frozen plans and saved scores (no fitting or solving)."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
from PIL import Image
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.monthly_origins.figures import COLOURS, COST, METHODS, PLANS, cell_ink, heat_grid

DATA = ROOT / "outputs"
COMBO, MONTHLY, ROLL, FAIR, CLEAN = [
    DATA / name for name in ("forecast_combination", "monthly_origins", "fair_comparison_rolling", "fair_comparison", "clean_checkpoint")]
FIG = ROOT / "figures/readme_heatmaps"
CASE = "2025-09"
AVG, FOUR, SN, ET, SAR = "Combo", "Combo (4 methods)", "Seasonal naive", "ExtraTrees-24", "SARIMA-24"
NAMES = {AVG: "Average of five", FOUR: "Average of four"}
KEY = ["origin", "method", "plan"]
MKEY = ["origin", "month", "horizon"]
PARTS = ["running", "opening", "closing", "shortfall_cost"]
SOURCES = set()


def read(path):
    SOURCES.add(path)
    return pd.read_csv(path, float_precision="round_trip")


def document(path):
    SOURCES.add(path)
    return json.loads(path.read_text(encoding="utf-8"))


def verify_hashes(config):
    def check(path, expected):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, path
    for relative, digest in config["source_hashes"].items():
        check(ROOT / relative, digest)
    for base in [MONTHLY, COMBO, ROLL, FAIR, CLEAN]:
        for manifest in base.glob("*.sha256"):
            for line in manifest.read_text().splitlines():
                expected, name = line.split(maxsplit=1)
                check(base / name, expected)


def compare(left, right):
    """Compare two labelled series in key order, preserving missing/duplicate-key detection."""
    left, right = left.sort_index(), right.sort_index()
    assert left.index.is_unique and right.index.is_unique
    pd.testing.assert_index_equal(left.index, right.index, check_names=False)
    np.testing.assert_allclose(left, right, rtol=1e-6, atol=1e-6)


def label(m):
    return NAMES.get(m, m)


def date(value, full=False):
    return pd.Timestamp(value).strftime("%B %Y" if full else "%b %Y")


def money(v):
    return f"{v / 1e6:,.2f}m"


def beds(v):
    return f"{v:,.0f}"


def table(header, rows):
    return "\n".join("| " + " | ".join(map(str, row)) + " |" for row in [header, ["---"]*len(header), *rows])


def load_monthly(cfg, scores, actual):
    origins = cfg["populations"]["P3_all"]
    assert origins == list(pd.period_range("2025-04", "2026-04", freq="M").astype(str))
    old = read(MONTHLY / "monthly_costs.csv")
    avg = read(COMBO / "monthly_costs.csv")
    avg = avg[avg.origin.isin(origins) & avg.penalty.eq(5000) & avg.method.isin([AVG, FOUR])]
    monthly = pd.concat([old, avg[old.columns]], ignore_index=True)
    totals = scores[scores.origin.isin(origins)].copy()
    by_origin = totals.groupby("origin")
    assert by_origin.hindsight.nunique().eq(1).all()
    oracle = by_origin.hindsight.first().reset_index()
    oracle = oracle.assign(method="Hindsight", plan="Hindsight", total=oracle.hindsight)
    totals = pd.concat([totals, oracle], ignore_index=True).set_index(KEY, verify_integrity=True).sort_index()
    compare(monthly.groupby(KEY).monthly_cost.sum(), totals.total)
    assert ((totals.total >= totals.hindsight * (1 - 1e-6))).all()
    earlier = read(MONTHLY / "costs.csv").melt(id_vars=["origin","method"], value_vars=PLANS,
                                                var_name="plan", value_name="total").set_index(KEY)
    individuals = totals[totals.index.get_level_values("method").isin(METHODS)]
    compare(earlier.total, individuals.total)
    compare(totals.xs("Hindsight", level="method").total.droplevel("plan"),
            read(MONTHLY / "costs.csv").groupby("origin").Hindsight.first())
    for origin in origins:
        names = set(METHODS + [AVG])
        if origin in cfg["populations"]["P3_partial"]:
            names = names - {SAR, AVG} | {FOUR}
        g = monthly[monthly.origin == origin]
        expected = {(m,p) for m in names for p in PLANS} | {("Hindsight","Hindsight")}
        assert set(zip(g.method,g.plan)) == expected
        months = pd.date_range(pd.Timestamp(origin) + pd.offsets.MonthBegin(), periods=6, freq="MS").strftime("%Y-%m-%d")
        for _, plan in g.groupby(["method","plan"]):
            plan = plan.sort_values("horizon")
            assert list(plan.month) == list(months) and list(plan.horizon) == list(range(1,7))
    account = monthly.merge(actual, on=MKEY, validate="many_to_one")
    assert len(account) == len(monthly)
    audit_accounts(account, cfg["costs"])
    # Both historical case studies provide independent reference totals.
    for origin, subdir in [(CASE,"O1_sep2025"), ("2026-03","O2_mar2026")]:
        clean = read(CLEAN / subdir / "totals.csv")
        clean = clean.assign(plan=clean.decision.where(clean.method != "Hindsight","Hindsight"))
        fair = read(FAIR / subdir / "totals.csv")
        fair = fair[fair.penalty.eq(5000) & fair.kind.isin(["point","protected","hindsight"])].copy()
        fair["plan"] = fair.kind.map({"point":"LP","protected":"Protected","hindsight":"Hindsight"})
        for frame in [clean, fair]:
            saved = frame.set_index(["method","plan"], verify_integrity=True).total
            compare(totals.loc[origin].total.loc[saved.index], saved)
    return monthly, totals


def audit_accounts(frame, prices):
    short = (frame.actual_gap - frame.x).clip(lower=0)
    spare = (frame.x - frame.actual_gap).clip(lower=0)
    for name, expected in [("shortfall", short), ("spare", spare),
                           ("running", prices["running"]*frame.x), ("opening", prices["opening"]*frame.a),
                           ("closing", prices["closing"]*frame.r), ("shortfall_cost", prices["shortage"]*short),
                           ("monthly_cost", frame[PARTS].sum(axis=1))]:
        np.testing.assert_allclose(frame[name], expected, rtol=1e-6, atol=1e-6)


def primary(cfg, scores, actual, forecasts):
    """Per-origin accounts and where the Protected schedule added capacity relative to LP."""
    origins = cfg["populations"]["P1"]
    assert len(origins) == 23 and set(origins).isdisjoint(cfg["idea_formed_at"])
    rolling = read(ROLL / "plans.csv")
    rolling["plan"] = rolling.kind.map({"point":"LP", "protected":"Protected"})
    plans = pd.concat([rolling, read(COMBO / "plans.csv")], ignore_index=True)
    plans = plans[plans.origin.isin(origins) & plans.penalty.eq(5000) & plans.plan.isin(["LP","Protected"])
                  & plans.method.isin(METHODS+[AVG])].copy()
    plans = plans.merge(actual, on=MKEY, validate="many_to_one")
    assert len(plans) == len(origins)*6*6*2 and not plans.duplicated(KEY+["month"]).any()
    plans = plans.sort_values(KEY+["horizon"])
    change = plans.groupby(KEY).x.diff().fillna(plans.x)
    np.testing.assert_allclose(change.clip(lower=0),plans.a,rtol=1e-6,atol=1e-5)
    np.testing.assert_allclose((-change).clip(lower=0),plans.r,rtol=1e-6,atol=1e-5)
    prices = cfg["costs"]
    plans["shortfall"] = (plans.actual_gap - plans.x).clip(lower=0)
    plans["spare"] = (plans.x - plans.actual_gap).clip(lower=0)
    for col, units, price in zip(PARTS, ["x","a","r","shortfall"], ["running","opening","closing","shortage"]):
        plans[col] = plans[units] * prices[price]
    plans["monthly_cost"] = plans[PARTS].sum(axis=1)
    subset = scores[scores.origin.isin(origins) & scores.plan.isin(["LP","Protected"])].set_index(KEY)
    per_origin = plans.groupby(KEY)[PARTS+["shortfall","spare","monthly_cost"]].sum()
    compare(per_origin.monthly_cost, subset.total)
    budget = per_origin.groupby(["method","plan"]).mean()
    budget["operating"] = budget[PARTS[:3]].sum(axis=1)
    # Classify a month from LP capacity versus observed gap, before considering its added beds.
    pairs = plans.pivot(index=["origin","method","month"], columns="plan", values=["x","shortfall","spare"])
    delta = pairs.x.Protected - pairs.x.LP
    added = delta.clip(lower=0)
    placement = pd.DataFrame({name: added.where(mask,0).groupby("method").sum()
                              for name,mask in [("short",pairs.shortfall.LP > 0), ("spare",pairs.spare.LP > 0),
                                                ("balanced",(pairs.shortfall.LP == 0) & (pairs.spare.LP == 0))]})
    compare(placement.sum(axis=1), added.groupby("method").sum())
    assert (placement.loc[[AVG,SN,ET],"balanced"] == 0).all()
    margin = delta.groupby("method").mean()
    endpoints = read(COMBO / "endpoints.csv").query("population=='P1' and penalty==5000")
    ends = endpoints[endpoints.method.isin(METHODS+[AVG]) & endpoints.plan.isin(["LP","Protected"])]
    excess = ends.pivot(index="method", columns="plan", values="mean_excess")
    compare(ends.set_index(["method","plan"]).mean_excess,
            (per_origin.monthly_cost - subset.hindsight).groupby(["method","plan"]).mean())
    coverage = read(COMBO / "coverage.csv").query("population=='P1' and horizon==0").set_index("method")
    q = read(COMBO / "quantiles.csv")
    keys = MKEY + ["method"]
    q = q[q.origin.isin(origins)].merge(forecasts[keys+["forecast_gap"]],on=keys,validate="one_to_one")
    q = q.merge(actual,on=MKEY,validate="many_to_one")
    assert q.groupby("method").size().eq(6*len(origins)).all()
    compare((q.q90 - q.forecast_gap).groupby(q.method).mean(), coverage.mean_margin)
    compare((q.actual_gap <= q.q90).groupby(q.method).mean(), coverage.coverage)
    compare((q.actual_gap <= q.q90).groupby(q.method).sum(), coverage.covered)
    assert (coverage.months == len(origins)*6).all()
    assert list(excess.LP.sort_values().index) == [SN,AVG,ET,"Mean-24",SAR,"Persistence"]
    assert list(excess.Protected.sort_values().index[:2]) == [AVG,SN]
    assert margin.sort_values().index[:3].tolist() == [SN,ET,AVG] and coverage.mean_margin.idxmin() == AVG
    chain = read(COMBO / "decomposition_summary.csv").set_index("population").loc["P1"]
    np.testing.assert_allclose([excess.LP[SN]-excess.LP[AVG], excess.LP[AVG]-excess.Protected[AVG],
                                excess.Protected[SN]-excess.Protected[AVG]],
                               chain[["averaging_saving","protection_saving","saving_vs_protected_sn"]].astype(float))
    assert (money(-chain.averaging_saving),money(chain.protection_saving),money(excess.Protected[AVG]),
            money(excess.Protected[SN])) == ("1.70m","2.87m","3.13m","3.66m")
    protected = budget.xs("Protected",level="plan")
    assert (protected.loc[[SN,ET],["operating","shortfall","spare"]] > protected.loc[AVG,["operating","shortfall","spare"]]).all().all()
    return budget, placement, margin, coverage, excess, chain


def check_forecasts(actual, forecasts, origins):
    f = forecasts.merge(actual,on=MKEY,validate="many_to_one")
    assert len(f) == len(forecasts)
    singles = f[f.method.isin(METHODS)]
    avgs = f[f.method.isin([AVG,FOUR])].set_index(MKEY)
    compare(singles.groupby(MKEY).forecast_gap.mean(), avgs.forecast_gap)
    errors = (f.actual_gap - f.forecast_gap).groupby([f.origin,f.method]).mean()
    saved = read(COMBO / "origin_metrics.csv").set_index(["origin","method"]).signed_error
    compare(errors, saved)
    for o, direction in [("2025-02",-1),("2025-12",1)]:
        assert (np.sign(errors.loc[o].loc[METHODS]) == direction).all()
    cases = read(MONTHLY / "method_origin.csv").set_index(["origin","method"])
    compare(cases.signed_error, errors.loc[cases.index])
    points = singles[singles.origin.isin(origins)].sort_values(MKEY)
    def has_dip(group):
        changes = np.diff(group)
        signs = np.sign(changes[np.abs(changes) > 1e-6])
        return bool(((signs[:-1] < 0) & (signs[1:] > 0)).any())
    dips = points.groupby(["origin","method"]).forecast_gap.agg(has_dip)
    compare(dips.astype(int), cases.forecast_dip.astype(int))
    tol = 1e-6*cases.Rule
    opt = cases.Rule - cases.LP
    assert (opt >= -tol).all() and (cases.forecast_dip == (opt > tol)).all()
    evidence = document(MONTHLY / "interpretation.json")
    assert sum(opt > tol) == evidence["optimisation_by_dip"]["True"]["helped"] == 30
    assert sum(opt.abs() <= tol) == evidence["optimisation_by_dip"]["False"]["tied"] == 33
    gain = cases.LP - cases.Protected
    low = cases.signed_error > 0
    assoc = evidence["protection_vs_error"]["available"]
    for name, mask in [("low",low),("high",~low)]:
        counts = assoc["protection_sign"][name]
        assert counts["helped"] == int((gain[mask] > 0).sum()) and counts["hurt"] == int((gain[mask] < 0).sum())
    rho = spearmanr(cases.signed_error,gain).statistic
    assert ((low & (gain>0)).sum(),low.sum(),(~low & (gain>0)).sum(),(~low).sum()) == (26,29,8,34)
    assert np.isclose(rho,assoc["spearman_rho"]) and f"{rho:.3f}" == "0.926"
    peak = actual[actual.origin.isin(origins)].drop_duplicates("month").set_index("month").actual_gap
    assert peak.idxmax() == evidence["winter_peak_month"] == "2026-01-01"
    assert peak.index.str[:4][peak.index.str[5:7] == "01"].tolist() == ["2026"]
    return errors


def plan_summary(monthly, totals, cfg):
    rows = []
    for origin, t in totals.groupby(level="origin"):
        t = t.droplevel("origin")
        choices = t.drop("Hindsight",level="method")
        win = choices.total.idxmin()
        cost = choices.loc[win,"total"]
        tied = choices[choices.total <= cost*(1+cfg["conventions"]["score_rtol"])].index.tolist()
        winner = f"{label(win[0])} {win[1]}"
        if len(tied) > 1:
            assert origin == "2026-02" and set(tied) == {(AVG,"Rule"),(AVG,"LP")} and win == (AVG,"Rule")
            winner += " (LP ties within tolerance)"
        plans = choices.total.unstack()
        months = sorted(monthly[monthly.origin == origin].month.unique())
        rows.append(dict(origin=origin, winner=winner, method=win[0], plan=win[1], total=cost,
                         excess=cost-choices.loc[win,"hindsight"], helped=int((plans.LP > plans.Protected).sum()),
                         n=len(plans), start=months[0], end=months[-1]))
    return pd.DataFrame(rows).set_index("origin")


def heatmap(origin, frame, totals, summary, scales, folder):
    average = FOUR if FOUR in frame.method.values else AVG
    order = [(m,p) for m in [*METHODS,average] for p in PLANS] + [("Hindsight","Hindsight")]
    months = sorted(frame.month.unique())
    keys = pd.MultiIndex.from_tuples([(m,p,t) for m,p in order for t in months],names=["method","plan","month"])
    g = frame.set_index(["method","plan","month"]).reindex(keys)
    t = totals.reindex(pd.MultiIndex.from_tuples(order, names=["method","plan"]))
    values = g.monthly_cost.to_numpy().reshape(len(order),6)/1e6
    valid_rows = [m != SAR or average == AVG for m,_ in order]
    assert np.array_equal(np.isfinite(values),np.repeat(np.array(valid_rows)[:,None],6,axis=1))
    assert np.array_equal(np.isfinite(t.total),valid_rows)
    short = g.shortfall.to_numpy().reshape(values.shape)
    spare = g.spare.to_numpy().reshape(values.shape)
    fig = plt.figure(figsize=(13.1,10.3))
    grid = fig.add_gridspec(1,2,left=.193,right=.984,bottom=.115,top=.90,
                            width_ratios=[6,1.48],wspace=.032)
    heading = (f"{date(origin,True)} origin: monthly cost of every plan "
               f"(forecasting {date(months[0],True)} - {date(months[-1],True)})")
    fig.text(.193,.963,heading,ha="left",va="center",weight="bold",fontsize=13.7)
    annotations = Counter()
    for j, (data, scale) in enumerate(zip((values,t.total.to_numpy()[:,None]/1e6),scales)):
        ax = fig.add_subplot(grid[0,j])
        im = ax.imshow(data,norm=scale,cmap=COST,aspect="auto",interpolation="nearest")
        assert im.norm is scale and scale.vmin == 0 and np.nanmax(data) <= scale.vmax
        rownames = [f"{label(m)} / {p}" if m != "Hindsight" else m for m,p in order]
        heat_grid(ax,["Total"] if j else [date(v).replace(" ","\n") for v in months],
                  [""]*len(order) if j else rownames)
        if not j:
            for tick,(m,_) in zip(ax.get_yticklabels(),order):
                tick.set_color(COLOURS.get(m,"black"))
                tick.set_fontweight("semibold")
        for i,k in np.ndindex(data.shape):
            cost = data[i,k]
            if np.isnan(cost):
                note, ink = "not available", "#484848"
            else:
                if j:
                    excess = (t.total.iloc[i]-t.hindsight.iloc[i])/1e6
                    second = f"+{excess:.2f}m vs hindsight"
                else:
                    second = f"short {beds(short[i,k])}" if round(short[i,k]) else f"spare {beds(spare[i,k])}"
                note, ink = f"{cost:.2f}m\n{second}", cell_ink(COST,scale,cost)
            ax.text(k,i,note,ha="center",va="center",color=ink,fontsize=9.0,linespacing=1.15)
            annotations.update(note.splitlines())
        for boundary in np.arange(2.5,len(order)-1,3):
            ax.axhline(boundary,color="#474747",lw=1.25)
        if j:
            idx = order.index((summary.method,summary.plan))
            ax.add_patch(Rectangle((-.48,idx-.46),.96,.92,fill=False,ec="white",lw=5))
            ax.add_patch(Rectangle((-.48,idx-.46),.96,.92,fill=False,ec="black",lw=3))
        box = ax.get_position()
        cax = fig.add_axes([box.x0,.072,box.width,.012])
        bar = fig.colorbar(im,cax=cax,orientation="horizontal")
        bar.set_ticks(np.linspace(0,scale.vmax,3 if j else 5))
        bar.set_label(("Total" if j else "Monthly")+" cost (million model units)",fontsize=9)
        bar.ax.tick_params(labelsize=8)
    fig.text(.193,.012,"Border = lowest unrounded forecast-based cost. Hindsight uses the actual gaps.",
             color="#414141",fontsize=9)
    stem = folder / f"origin_{origin}"
    fig.savefig(stem.with_suffix(".png"),dpi=300,facecolor="white")
    fig.savefig(stem.with_suffix(".svg"),dpi=300,facecolor="white",metadata={"Date":None})
    plt.close(fig)
    svg = stem.with_suffix(".svg")
    svg.write_bytes(("\n".join(l.rstrip() for l in svg.read_text(encoding="utf-8").splitlines())+"\n").encode())
    texts = Counter(el.text for el in ElementTree.parse(svg).iter("{http://www.w3.org/2000/svg}text"))
    assert all(texts[s] == count for s,count in annotations.items()) and texts[heading] == 1
    with Image.open(stem.with_suffix(".png")) as png:
        assert all(abs(d-300)<.01 for d in png.info["dpi"])


def figure(origin):
    where = f"figures/readme_heatmaps/origin_{origin}"
    return f"![Monthly costs, shortages and spare beds for the {date(origin,True)} origin]({where}.png)\n\n[Vector heatmap]({where}.svg)"


def september(rows, totals, errors, cfg):
    t = totals.loc[CASE].total.unstack()
    src = rows[rows.origin == CASE]
    sums = src.groupby(["method","plan"])[["shortfall","spare"]].sum()
    sar = src[src.method == SAR].pivot(index="month",columns="plan",values="x")
    lift = sar.Protected-sar.LP
    scenarios = read(COMBO / "banks.csv")
    bank = scenarios[scenarios.origin == CASE].groupby(["method","horizon"]).backtest_origin.nunique()
    assert len(bank)==36 and bank.nunique()==1 and bank.iloc[0] == 28
    # Check the requested worked paragraph at its stated precision.
    assert round(bank.iloc[0],-1)==30 and (round(lift.min(),-2),round(lift.max(),-2))==(700,1400)
    assert (round(sums.loc[(SAR,"LP"),"spare"]), round(sums.loc[(SAR,"Protected"),"spare"])) == (3320,9291)
    pers = "Persistence"
    assert round(sums.loc[(pers,"LP"),"shortfall"]) == 4276 and sums.loc[(pers,"Protected"),"shortfall"] == 0
    assert [money(t.loc[m,p]) for m in [SAR,pers] for p in ["LP","Protected"]] == ["16.11m","19.46m","33.21m","17.55m"]
    assert (cfg["costs"]["shortage"],cfg["costs"]["running"]) == (5000,500)
    paragraph = ("Without protection, the optimiser plans for one number per month: the forecast. "
                 "With protection, it plans against about 30 possible futures, each made by adding one of that "
                 "method's past six-month forecast errors to the forecast, and picks the single schedule with the "
                 "lowest average cost across them. Because a missing bed costs 5,000 and a spare bed about 500, "
                 "that schedule sits above the forecast. In September 2025 it lifted SARIMA-24's plan by roughly "
                 "700-1,400 beds a month. SARIMA-24 already forecast high, so those beds were not needed: spare "
                 "bed-months rose from 3,320 to 9,291 and cost rose from 16.11m to 19.46m. Persistence forecast "
                 "too low: without protection it was 4,276 bed-months short and cost 33.21m; with protection it "
                 "was never short and cost 17.55m. Same origin, same tool, opposite result, because protection is "
                 "built around the forecast. It adds a margin; it cannot tell whether the forecast is too high or too low.")
    sep_rows = []
    for m in [*METHODS,AVG]:
        signed = errors.loc[(CASE,m)]
        cells = []
        for p in ["LP","Protected"]:
            v = sums.loc[(m,p)]
            cells.append(f"{money(t.loc[m,p])} · {beds(v.shortfall)} / {beds(v.spare)}")
        effect = t.loc[m,"LP"]-t.loc[m,"Protected"]
        sep_rows.append([label(m), f"{'Too low' if signed>0 else 'Too high'} ({signed:+,.1f})",
                         *cells, f"{effect/1e6:+.2f}m"])
    for m in ["Mean-24",pers]:
        g = src[(src.method==m)&(src.plan=="LP")].nlargest(2,"monthly_cost")
        assert set(g.month)=={"2026-01-01","2026-02-01"} and (g.shortfall>0).all()
    for m in [SN,SAR]:
        g = src[(src.method==m)&src.month.eq("2025-12-01")].set_index("plan")
        assert g.loc["Rule","r"] > 0 and g.loc["LP","r"] == 0 and g.loc["LP","x"] > g.loc["Rule","x"]
    assert errors.loc[(CASE,SAR)]<0<errors.loc[(CASE,pers)] and t.Protected.dropna().idxmin()==AVG
    assert t.loc[SAR,"LP"] < t.loc[SN,"LP"] < t.loc[AVG,"Protected"]
    walk = (f"January and February were the two most expensive LP months for Mean-24 and Persistence, when their flat "
            f"forecasts left beds short. In December, Seasonal naive and SARIMA-24 kept beds open with LP that the Rule "
            f"would have closed, saving {money(t.loc[SN,'Rule']-t.loc[SN,'LP'])} and {money(t.loc[SAR,'Rule']-t.loc[SAR,'LP'])} "
            f"over the window. Protection removed Persistence's shortages, while it added spare beds to a SARIMA-24 "
            f"schedule that already covered demand. The average ran {beds(errors.loc[(CASE,AVG)])} beds low, and its cost fell from "
            f"{money(t.loc[AVG,'LP'])} with LP to {money(t.loc[AVG,'Protected'])} with protection, cheapest among the "
            f"Protected plans. SARIMA-24 LP still had the lowest overall cost at {money(t.loc[SAR,'LP'])}.")
    return paragraph, table(["Forecast","Direction (signed error in beds)","LP cost · short / spare",
                             "Protected cost · short / spare","Saving from protection"],sep_rows), walk


def averaging_section(primary_results):
    budget, placement, margin, cov, excess, chain = primary_results
    first = []
    for m in excess.LP.sort_values().index:
        q = cov.loc[m]
        first.append([label(m),money(excess.loc[m,"LP"]),money(excess.loc[m,"Protected"]),beds(margin[m]),
                      beds(q.mean_margin),f"{q.coverage:.1%} ({int(q.covered)}/{int(q.months)})"])
    line = table(["Forecast (ranked by LP cost)","Mean LP excess","Mean Protected excess",
                  "Extra planned beds","Forecast uncertainty margin (beds)","90% upper forecast coverage"],first)
    comp_rows = []
    for m in [AVG,SN,ET]:
        for p in ["LP","Protected"]:
            v = budget.loc[(m,p)]
            extra = placement.loc[m]
            comp_rows.append([label(m),p,money(v.operating),money(v.running),money(v.opening),money(v.closing),money(v.shortfall_cost),
                              f"{beds(v.shortfall)} / {beds(v.spare)}",
                              f"{beds(extra['short'])} / {beds(extra['spare'])}" if p=="Protected" else "—"])
    diag = table(["Forecast","Plan","Operating total","Running","Opening","Closing","Shortfall cost","Short / spare bed-months",
                  "Added in LP-short / LP-spare months"],comp_rows)
    a,s,e = [budget.loc[(m,"Protected")] for m in [AVG,SN,ET]]
    why = (f"Against Seasonal naive Protected, the protected average spent {money(s.operating-a.operating)} less on "
           f"running, opening and closing combined and {money(s.shortfall_cost-a.shortfall_cost)} less on shortages "
           f"per origin. Against ExtraTrees-24 Protected, lower shortfall charges accounted for "
           f"{money(e.shortfall_cost-a.shortfall_cost)} of its {money(e.monthly_cost-a.monthly_cost)} saving. "
           f"The protected average left {beds(a.shortfall)} bed-months short and {beds(a.spare)} spare per origin, "
           f"fewer of both than either protected comparator.")
    return "\n\n".join([
        "## Did averaging the forecasts help?",
        "The primary test used 23 origins, excluding September 2025 and March 2026, where the idea was formed. "
        "Excess cost is the six-month bill above that origin's Hindsight bill. Each origin counts equally.",
        line,
        "Extra planned beds is mean Protected capacity minus LP capacity per month. The forecast uncertainty "
        "margin is the distance from the point forecast to its 90% upper forecast. Coverage counts how often "
        "the actual gap was at or below that upper forecast.",
        f"With LP alone the average ranked second. Seasonal naive cost {money(-chain.averaging_saving)} less per origin. "
        "ExtraTrees-24, Mean-24, SARIMA-24 and Persistence followed in that order. Protecting the average then saved "
        f"{money(chain.protection_saving)}, bringing its mean excess to {money(chain.combo_protected_excess)} "
        f"against {money(chain.sn_protected_excess)} for Seasonal naive Protected.",
        "Protecting the average produced the lowest mean cost. It had the smallest forecast uncertainty margin "
        "and the third-smallest increase in planned capacity.",
        "### Where the costs and added beds went",
        "Costs are millions per six-month origin, rounded after averaging. Operating total combines running, opening "
        "and closing. Short and spare are also means per origin. The last column totals "
        "the positive increases in planned bed-months over all 23 origins, grouped by whether the LP schedule "
        "was short or spare against actual demand that month. All added beds in a short month go in the first "
        "group, including any beyond what was needed to cover its shortage.",
        diag, why,
        "All five forecasts ran high in February 2025 and low in December 2025. Averaging preserved the direction "
        "of those shared misses, so the cost depended on how the protected schedule covered them.",
    ])


def window_caption(origin, summary, monthly, totals, errors):
    m = summary.method
    g = monthly[(monthly.origin==origin)&(monthly.method==m)]
    plan = {p:v.set_index("month").sort_index() for p,v in g.groupby("plan")}
    lp, protected, rule = [plan[p] for p in ["LP","Protected","Rule"]]
    t = totals.loc[(origin,m)].total
    bias = errors.loc[(origin,m)]
    direction = f"{beds(abs(bias))} beds {'low' if bias>0 else 'high'} on average"
    peak = lp.monthly_cost.idxmax()
    if summary.plan == "Protected":
        shortage = lp.shortfall.idxmax()
        return (f"{label(m)} ran {direction}. Its largest LP bill was {money(lp.loc[peak,'monthly_cost'])} in "
                f"{date(peak)}, and protection cut its largest monthly shortage from {beds(lp.loc[shortage,'shortfall'])} "
                f"to {beds(protected.loc[shortage,'shortfall'])} beds in {date(shortage)}. Across the six months "
                f"that protected schedule saved {money(t.LP-t.Protected)} against its own LP.")
    if summary.plan == "Rule":
        return (f"The average ran {direction}. Its opening month, {date(rule.monthly_cost.idxmax())}, had its "
                f"largest Rule bill ({money(rule.monthly_cost.max())}). Rule and LP tied within solver tolerance, "
                f"while protection added {money(t.Protected-t.LP)}.")
    dip = (lp.x-rule.x).idxmax()
    return (f"{label(m)} ran {direction}, and its most expensive LP month was {date(peak)} "
            f"({money(lp.loc[peak,'monthly_cost'])}). Keeping {beds(lp.loc[dip,'x']-rule.loc[dip,'x'])} more beds "
            f"open than the Rule in {date(dip)} helped LP save {money(t.Rule-t.LP)} across the window. "
            f"Protection added {money(t.Protected-t.LP)} to this method's bill.")


def make_readme(cfg, monthly, totals, summary, errors, scales, primary_results):
    saa, sep_table, walk = september(monthly, totals, errors, cfg)
    periods = cfg["populations"]
    assert len(periods["P3_all"]) == 13 and len(periods["P3"]) == 11 and len(periods["P1"]) == 23
    assert cfg["methods"] == METHODS and cfg["forecast"]["window_months"] == 24
    assert cfg["forecast"]["horizon_months"] == 6 and cfg["forecast"]["occupancy_target"] == .90
    assert cfg["costs"] == dict(running=500,opening=550,closing=300,shortage=5000,
                               capacity=15000,ramp=5000,initial=0,terminal_charge=0,validated=False)
    assert cfg["idea_formed_at"] == ["2025-09","2026-03"]
    assert (cfg["conventions"]["score_rtol"] == 1e-6 and len(METHODS)==5 and
            cfg["populations"]["P3_partial"] == ["2025-10","2026-01"])
    available = read(MONTHLY / "availability.csv")
    missing = available[available.status != "available"].set_index("origin").failed_target.to_dict()
    assert missing == {"2025-10":"occupied","2026-01":"available"}
    materials = []
    for base in sorted({s.parent for s in SOURCES}):
        links = [f"[{s.name}]({s.relative_to(ROOT).as_posix()})" for s in sorted(SOURCES) if s.parent==base]
        materials.append(f"- `{base.relative_to(ROOT).as_posix()}/` — " + ", ".join(links))

    blocks = [
        "# Frozen bed-capacity plans: forecast, optimise, protect", "## Question",
        "How many extra NHS England general and acute beds should be open if the next six months must be planned "
        "today? At each origin we freeze a schedule using five forecasts and their equal-weight average. "
        "Does optimising the schedule improve on following the forecast, and when does protection against error pay? "
        "Does averaging the forecasts improve the plan?",
        "## Method",
        "An origin is the last observed month. Each method forecasts occupied and available beds separately "
        "from a 24-month training window, then we fix all six monthly decisions. The planning gap is "
        "**occupied / 0.90 − available**, the extra capacity needed for a 90% occupancy target.",
        "- **Mean-24** repeats the training average.\n"
        "- **Persistence** repeats the latest observation.\n"
        "- **Seasonal naive** repeats the same calendar month from the previous year.\n"
        "- **SARIMA-24** uses a fixed seasonal time-series model.\n"
        "- **ExtraTrees-24** uses a fixed tree ensemble with lagged observations and calendar features.\n"
        "- **Average of five** gives each method equal weight for occupied beds and for available beds. "
        "October 2025 and January 2026 use an **Average of four** because SARIMA-24 failed to converge.",
        "**Rule** follows the forecast gap within the limits. **LP** uses the same forecast and chooses the "
        "lowest-cost schedule across the window. Running costs 500 per bed-month, opening 550 per bed, "
        "closing 300 per bed and shortfall 5,000 per missing bed-month. Beds carry over from month to month. "
        "Capacity starts at zero, is capped at 15,000 and can change by up to 5,000 beds per month. There is "
        "no terminal closure charge. **Hindsight** uses the actual gaps and the same constraints. Costs are "
        "illustrative model units (validated: False).",
        "### How protection (SAA) works", saa, sep_table,
        "Signed error is actual gap minus forecast gap, averaged across the window. Short and spare sum "
        "bed-months across September's six target months. A positive protection effect means Protected cost less than LP.",
        "## How to read the heatmaps",
        f"Each method has Rule, LP and Protected rows. Each monthly cell shows realised cost in millions and "
        f"how many beds were short or spare against the actual gap, rounded to whole beds. Pale yellow is cheaper "
        f"and deep red more expensive. All heatmaps use **0–{scales[0].vmax:.0f}m for monthly cells** and "
        f"**0–{scales[1].vmax:.0f}m for totals**. The Total column also shows the extra cost over Hindsight. "
        "The thick border marks the cheapest available forecast-based plan before rounding. Grey cells mark unavailable forecasts.",
        "## Main origin: September 2025", figure(CASE), walk,
        "## Protection depends on the forecast",
        "![Protection saving versus mean forecast error, individual methods](figures/monthly_origins/protection_vs_error_direction.png)"
        "\n\n[Vector scatter plot](figures/monthly_origins/protection_vs_error_direction.svg)",
        "Each dot represents one of the five individual methods at one monthly origin. Protection helped in "
        "**26 of 29** cases where the forecast ran low and **8 of 34** where it ran high "
        "(Spearman rank correlation **0.926**). Its margin is sized from that method's past errors, and a high "
        "six-month average can still conceal a monthly shortage. The direction of the next error is unknown "
        "when the plan is frozen, so we judge protection by what it saves across windows.",
        averaging_section(primary_results),
        "## Every origin: April 2025 to April 2026",
        table(["Origin","Forecast months","Cheapest available plan","Excess over hindsight","Protection helped most?"],
              [[date(o),f"{date(r.start)} – {date(r.end)}",r.winner,money(r.excess),
                f"{'Yes' if 2*r.helped>r.n else 'Half' if 2*r.helped==r.n else 'No'} ({r.helped} of {r.n})"]
               for o,r in summary.iterrows()]),
        "Protection helped a forecast if its Protected plan cost less than its own LP. Counts include the average. "
        "The two origins with a missing SARIMA-24 fit compare only the plans available then. In February 2026 "
        "the average's Rule had the lowest unrounded cost and its LP tied within the saved relative tolerance of 1e-6.",
    ]
    for o,r in summary.iterrows():
        blocks.append(f"<details><summary><b>{date(o)} origin</b> - cheapest: {r.winner}; protection helped "
                      f"{r.helped} of {r.n} forecasts</summary>\n\n{figure(o)}\n\n"
                      f"{window_caption(o,r,monthly,totals,errors)}\n\n</details>")
    blocks += [
        "## Findings",
        "- **Optimisation paid when the forecast dipped.** Across the five individual methods it saved money in "
        "30 of 30 forecasts with a fall followed by a rise, and tied in all 33 without a dip. It never materially cost more.\n"
        "- **Protection depended on the forecast.** It saved money in 26 of 29 under-forecasts and 8 of 34 over-forecasts.\n"
        "- **The cheapest plan changed with the window.** SARIMA-24 LP led in September 2025 and Seasonal naive Protected "
        "led in March 2026.\n"
        "- **Averaging alone ranked second.** Seasonal naive had the cheapest LP on average in the primary test. "
        "With protection, the average had the lowest mean cost. Its protected plan had fewer short and spare "
        "bed-months than protected Seasonal naive and protected ExtraTrees-24.\n"
        "- **The protection margins were conservative here.** The average's 90% upper forecast covered "
        "137 of 138 origin-months, or 99.3%.",
        "## Limitations",
        "- Adjacent origins share up to five target months. The 13 monthly origins represent about two to three "
        "independent six-month windows, and the 23 primary origins about four.\n"
        "- The monthly set contains a single winter peak in January 2026.\n"
        "- Univariate forecasts use national monthly data. Local bottlenecks, staffing constraints and daily peaks are hidden.\n"
        "- Costs are illustrative. Plan rankings depend on the assumed shortage price.",
        "**Saved numerical sources**", "\n".join(materials),
    ]
    assert summary.loc[CASE,"winner"] == "SARIMA-24 LP" and summary.loc["2026-03","winner"] == "Seasonal naive Protected"
    assert summary.loc["2026-02","winner"] == "Average of five Rule (LP ties within tolerance)"
    caps = primary_results[3].loc[AVG]
    assert (caps.covered,caps.months,f"{caps.coverage:.1%}") == (137,138,"99.3%")
    return "\n\n".join(blocks) + "\n"


def main():
    cfg = document(COMBO / "config.json")
    verify_hashes(cfg)
    actual = read(COMBO / "actuals.csv")
    np.testing.assert_allclose(actual.actual_gap, actual.actual_occupied/.90-actual.actual_available,rtol=0,atol=1e-7)
    forecasts = read(COMBO / "forecasts.csv")
    scores = read(COMBO / "costs.csv")
    scores = scores[scores.method.isin([*METHODS,AVG,FOUR]) & scores.penalty.eq(cfg["costs"]["shortage"])]
    diagnostic = primary(cfg,scores,actual,forecasts)
    monthly, totals = load_monthly(cfg,scores,actual)
    errors = check_forecasts(actual,forecasts,cfg["populations"]["P3_all"])
    summary = plan_summary(monthly,totals,cfg)
    scales = [Normalize(0,np.ceil(v/1e6)) for v in (monthly.monthly_cost.max(),totals.total.max())]
    text = make_readme(cfg,monthly,totals,summary,errors,scales,diagnostic)
    plt.rcParams.update({"font.family":"DejaVu Sans", "svg.fonttype":"none","svg.hashsalt":"readme-heatmaps"})
    with TemporaryDirectory(dir=ROOT / "figures") as temp:
        folder = Path(temp)
        for o,r in summary.iterrows():
            heatmap(o,monthly[monthly.origin==o],totals.loc[o],r,scales,folder)
        assert len(list(folder.glob("*.png"))) == len(list(folder.glob("*.svg"))) == len(summary)
        for link in re.findall(r"\]\(([^)]+)\)",text):
            path = ROOT / link
            assert ((folder/path.name) if path.parent==FIG else path).is_file(), link
        assert text.count("<details><summary>") == text.count("</summary>") == text.count("</details>") == len(summary)
        FIG.mkdir(exist_ok=True)
        for image in folder.iterdir():
            image.replace(FIG / image.name)
    (ROOT / "README.md").write_bytes(text.encode("utf-8"))
    print(f"Verified {len(totals)} six-month schedules and {len(monthly)} monthly accounts. "
          f"All {len(summary)} PNG/SVG pairs use shared scales and match the saved values.")


if __name__=="__main__":
    main()
