"""Value of Rule -> LP -> Protected, reusing frozen forecasts and plans.

No study forecast is fitted and no LP is solved here. The only new decision rule
is the existing clean-checkpoint rule_plan, imported without modification.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'scripts/clean_checkpoint'))
import pipeline as cc
import numpy as np
import pandas as pd

OUT=REPO/'outputs/optimisation_value'
SOURCE=REPO/'outputs/fair_comparison_rolling'
METHODS=list(cc.METHODS)
ANCHORS={'2025-09':'O1_sep2025','2026-03':'O2_mar2026'}
REL=1e-6
FLOW_TOL=1e-6
PLAN_TOL=1e-3


def jsave(path,value):
    cc.write_text(path,json.dumps(value,indent=2,allow_nan=False)+'\n')


def read(path):
    return pd.read_csv(path,float_precision='round_trip')


def close_reopen_events(x):
    """Count each removal once if an addition follows within the next 2 months."""
    added,removed=cc.flows(x)
    return sum(bool(removed[t]>FLOW_TOL and (added[t+1:t+3]>FLOW_TOL).any()) for t in range(len(removed)))


def gap_shares(rule,lp,protected,hindsight):
    r,l,p,h=(np.asarray(v,dtype=float) for v in [rule,lp,protected,hindsight])
    denominator=float((r-h).sum())
    if denominator==0:
        return dict(denominator=0.,optimisation=np.nan,protection=np.nan,remaining=np.nan)
    return dict(denominator=denominator,optimisation=float((r-l).sum()/denominator),
                protection=float((l-p).sum()/denominator),remaining=float((p-h).sum()/denominator))


def counts(value,band):
    v,b=np.asarray(value),np.asarray(band)
    return dict(helped=int((v>b).sum()),tie=int((np.abs(v)<=b).sum()),hurt=int((v < -b).sum()))


def verify_sources():
    cc.verify_hashes(SOURCE/'config.sha256')
    cc.verify_hashes(SOURCE/'plans.sha256')
    c=json.loads((SOURCE/'config.json').read_text())
    for path,digest in c['source_hashes'].items():
        assert cc.sha256(REPO/path)==digest,f'changed source {path}'
    return c


def snapshot_sources():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=REPO).decode().split('\0')
    files+=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=REPO).decode().split('\0')
    prior={p:cc.sha256(REPO/p) for p in files if p and not p.startswith(('scripts/optimisation_value/','outputs/optimisation_value/','figures/optimisation_value/')) and (REPO/p).is_file()}
    jsave(OUT/'prior_files_manifest.json',prior)


def phase0(data_dir):
    cfg=verify_sources()
    assert not (OUT/'phase0.json').exists(),'phase 0 already recorded'
    snapshot_sources()
    rules=[]
    for origin in ANCHORS:
        fc=cc.read_csv(SOURCE/origin/'forecasts.csv')
        for method in METHODS:
            g=fc[fc.method==method].sort_values('month')
            x=cc.rule_plan(g.forecast_gap)
            a,r=cc.flows(x)
            rules.extend(dict(origin=origin,method=method,month=month,x=x[h],a=a[h],r=r[h]) for h,month in enumerate(g.month))
    cc.write_csv(OUT/'phase0_rule_plans.csv',pd.DataFrame(rules))
    data=cc.load(data_dir,cutoff=pd.Timestamp(cfg['last_published_month']))
    comparisons=[]
    for origin,key in ANCHORS.items():
        oldfc=cc.read_csv(REPO/'outputs/clean_checkpoint'/key/'forecasts.csv')
        fc=cc.read_csv(SOURCE/origin/'forecasts.csv')
        assert oldfc[['method','month']].equals(fc[['method','month']])
        difference=float(np.abs(oldfc.filter(like='forecast_').to_numpy()-fc.filter(like='forecast_').to_numpy()).max())
        assert difference<=1e-6,f'forecast reproduction {difference}'
        old=read(REPO/'outputs/clean_checkpoint'/key/'totals.csv')
        months=cc.target_months(pd.Timestamp(origin));actual=data.loc[months]
        gap=cc.gap(actual.occupied,actual.available).to_numpy()
        for method in METHODS:
            x=np.array([row['x'] for row in rules if row['origin']==origin and row['method']==method])
            cost=cc.totals_row(cc.cost_table(x,gap))['total']
            expected=float(old[(old.method==method)&(old.decision=='Rule')].total.iloc[0])
            rel=abs(cost-expected)/abs(expected)
            assert rel<=REL,f'{origin}/{method}: relative reproduction difference {rel}'
            comparisons.append(dict(origin=origin,method=method,expected_total=expected,reproduced_total=cost,relative_difference=rel,forecast_max_difference_beds=difference))
    cc.write_csv(OUT/'phase0_reproduction.csv',pd.DataFrame(comparisons))
    totals=read(SOURCE/'totals.csv');summary=read(SOURCE/'summary.csv')
    text=(REPO/'README.md').read_text(encoding='utf-8')
    checked=[]
    for row in summary[(summary.scope=='all')&(summary.penalty==5000)].itertuples():
        t=totals[(totals.method==row.method)&(totals.penalty==5000)]
        lp=t[t.kind=='point'].set_index('origin').loc[cfg['origins']]
        protected=t[t.kind=='protected'].set_index('origin').loc[cfg['origins']]
        excess=float((protected.total-protected.hindsight).mean())
        value=float((lp.total-protected.total).mean())
        assert np.isclose(excess,row.mean_excess_over_hindsight,rtol=1e-12,atol=1e-6)
        assert np.isclose(value,row.mean_value_of_protection,rtol=1e-12,atol=1e-6)
        expected='| '+' | '.join([row.method,f'{excess/1e6:.2f}',str(row.joint_best_count),
                   f'{row.wins_vs_seasonal_naive}/{row.ties_vs_seasonal_naive}/{row.losses_vs_seasonal_naive}',
                   f'{row.mean_pinball_090:.1f}',f'{row.coverage_090:.1%} ({row.covered_090}/{row.months})',f'{value/1e6:.2f}'])+' |'
        assert expected in text,f'rolling README row differs for {row.method}'
        checked.append(dict(method=row.method,mean_lp_cost=float(lp.total.mean()),mean_protected_cost=float(protected.total.mean()),
                            displayed_mean_excess=excess,displayed_mean_protection_value=value))
    cc.write_csv(OUT/'phase0_saved_costs_check.csv',pd.DataFrame(checked))
    result=dict(status='PASS',max_rule_cost_relative_difference=max(r['relative_difference'] for r in comparisons),
                max_forecast_difference_beds=max(r['forecast_max_difference_beds'] for r in comparisons),
                reproduction_rows=len(comparisons),readme_rows=len(checked),
                note='The rolling README reports mean Protected excess over hindsight and mean LP-minus-Protected, rather than raw mean totals; both were independently reconstructed from loaded totals and checked.',
                source_config_sha256=cc.sha256(SOURCE/'config.json'),source_plans_sha256=cc.sha256(SOURCE/'plans.csv'))
    jsave(OUT/'phase0.json',result)
    print(json.dumps(result,indent=2),flush=True)


def register(data_dir):
    assert json.loads((OUT/'phase0.json').read_text())['status']=='PASS'
    assert not (OUT/'config.json').exists() and not (OUT/'rule_plans.csv').exists()
    source=verify_sources();origins=source['origins']
    scopes={'all':origins,'excluding_original_two':[o for o in origins if o not in ANCHORS]}
    inputs=['config.json','config.sha256','plans.csv','plans.sha256','totals.csv','monthly_costs.csv','fit_log.json','summary.csv']
    inputs += [f'{o}/forecasts.csv' for o in origins]
    hashes={f'outputs/fair_comparison_rolling/{name}':cc.sha256(SOURCE/name) for name in inputs}
    hashes.update({p:cc.sha256(REPO/p) for p in ['scripts/clean_checkpoint/pipeline.py','scripts/optimisation_value/value.py']})
    config=dict(name='Value of optimisation and protection across 25 origins',source_commit='d81495a65ccc6c467bd3a72727de547fb2e49753',
                source_config_sha256=cc.sha256(SOURCE/'config.json'),source_plans_sha256=cc.sha256(SOURCE/'plans.csv'),
                data=source['data'],last_published_month=source['last_published_month'],origins=origins,methods=METHODS,
                exclusions=source['planning_origin_convergence']['excluded'],earlier_insufficient_origins=15,
                scopes=scopes,subsets={scope:{str(r):os[r::6] for r in range(6)} for scope,os in scopes.items()},
                window=24,horizon=6,costs=dict(running=500,opening=550,closing=300,shortfall=5000),
                limits=dict(cap=15000,ramp=5000,x0=0,terminal_charge=0),source_hashes=hashes,
                plans=dict(Rule='Use imported clean_checkpoint.pipeline.rule_plan on each saved point gap; never reimplement or refit.',
                           LP='Reuse saved Point LP at p=5000 and its existing score.',Protected='Reuse saved raw-error SAA at p=5000 and its existing score.',
                           Hindsight='Reuse the saved actual-gap LP at p=5000 and its existing score.'),
                metrics=dict(M1='Mean Rule, LP, Protected and Hindsight realized costs over origin-method cases.',
                  M2='Rule - LP: mean, median, min, max, cheaper/equal/dearer counts; equal iff abs difference <= 1e-6 * abs(Rule cost).',
                  M3='LP - Protected: mean, median, min, max; helped/tie/hurt with 1% of origin hindsight cost as tie band.',
                  M4='Rule - Protected: mean, median, min, max.',
                  M5='Ratios of sums over the reporting group: sum(Rule-LP), sum(LP-Protected), sum(Protected-Hindsight), each divided by sum(Rule-Hindsight). No clipping. Undefined if denominator zero.',
                  M6='Mean opening, closing, running cost, shortfall bed-months and close-then-reopen events per plan for Rule and LP.',
                  M7='M2 and M3 means for each of six offsets, every sixth retained origin within the scope; ranges are min/max subset means, not confidence intervals; all metrics also exclude Sep2025 and Mar2026.'),
                conventions=dict(relative_cost_tolerance=REL,solver_schedule_tolerance_beds=PLAN_TOL,
                  event_flow_tolerance_beds=FLOW_TOL,
                  close_then_reopen='Count each removal month once if at least one addition occurs in the next 1 or 2 months; only within the 6-month plan; flow must exceed 1e-6 beds.',
                  pooled='Equal weight per method-origin case; repeat each origin hindsight for every method to match the numerator; pooled shares are ratios of sums.',
                  zero_denominator='Shares recorded as NaN with denominator 0, never replaced by zero or clipped.',
                  expectation_E1='Schedule equality within inherited CBC precision 1e-3 beds; report maximum raw difference.',
                  expectation_E2='No realized LP cost increase beyond the M2 relative tolerance.',
                  expectation_E3='Strictly positive mean Rule-minus-LP for Seasonal naive, SARIMA-24 and ExtraTrees-24.',
                  expectations_are_not_stop_conditions=True),
                procedure=['Phase 0 reproduces the two previously evaluated anchors, as explicitly required; it is the only pre-registration Rule scoring.',
                           'Register before any new rolling Rule scoring; save and hash all Rule plans before loading their scoring outcomes.',
                           'Reuse all other plans and scores. No study model refits and no new LP solves.',
                           'Compute and report the full registered metrics, including expectation failures.'],
                disclosures=['The original outcomes and rolling comparison have already been inspected; this is not a blind evaluation.',
                             'Neighbouring origins overlap; subset ranges are descriptive. Costs are illustrative model units (validated: False).'])
    jsave(OUT/'config.json',config)
    cc.write_text(OUT/'config.sha256',f"{cc.sha256(OUT/'config.json')}  config.json\n")
    print('CONFIG SHA256',cc.sha256(OUT/'config.json'),flush=True)


def check_config():
    cc.verify_hashes(OUT/'config.sha256');verify_sources()
    cfg=json.loads((OUT/'config.json').read_text())
    for name,digest in cfg['source_hashes'].items():
        assert cc.sha256(REPO/name)==digest,f'input changed: {name}'
    return cfg


def plan():
    cfg=check_config()
    assert not (OUT/'rule_plans.csv').exists(),'Rule plans already frozen'
    rows=[]
    for origin in cfg['origins']:
        fc=cc.read_csv(SOURCE/origin/'forecasts.csv')
        for method in METHODS:
            g=fc[fc.method==method].sort_values('month')
            assert list(g.month)==list(cc.target_months(pd.Timestamp(origin)))
            x=cc.rule_plan(g.forecast_gap);a,r=cc.flows(x)
            rows.extend(dict(origin=origin,method=method,horizon=h+1,month=m,forecast_gap=float(g.forecast_gap.iloc[h]),
                             x=x[h],a=a[h],r=r[h]) for h,m in enumerate(g.month))
    cc.write_csv(OUT/'rule_plans.csv',pd.DataFrame(rows))
    cc.write_text(OUT/'rule_plans.sha256',f"{cc.sha256(OUT/'rule_plans.csv')}  rule_plans.csv\n")
    print('RULE PLANS SHA256',cc.sha256(OUT/'rule_plans.csv'),flush=True)


def score(data_dir):
    cfg=check_config();cc.verify_hashes(OUT/'rule_plans.sha256')
    assert not (OUT/'costs.csv').exists(),'already scored'
    rules=cc.read_csv(OUT/'rule_plans.csv')
    data=cc.load(data_dir,cutoff=pd.Timestamp(cfg['last_published_month']))
    old_totals=read(SOURCE/'totals.csv');old_monthly=cc.read_csv(SOURCE/'monthly_costs.csv')
    rows=[];monthly=[]
    for origin in cfg['origins']:
        months=cc.target_months(pd.Timestamp(origin));actual=data.loc[months]
        assert len(actual)==6 and not actual.isna().any().any()
        d=cc.gap(actual.occupied,actual.available).to_numpy()
        saved=old_totals[(old_totals.origin==origin)&(old_totals.penalty==5000)]
        hind=float(saved[saved.kind=='hindsight'].total.iloc[0])
        for method in METHODS:
            g=rules[(rules.origin==origin)&(rules.method==method)].sort_values('month')
            table=cc.cost_table(g.x.to_numpy(),d)
            rule_total=cc.totals_row(table)
            cost=dict(origin=origin,method=method,Rule=rule_total['total'],Hindsight=hind)
            for kind,label in [('point','LP'),('protected','Protected')]:
                cost[label]=float(saved[(saved.method==method)&(saved.kind==kind)].total.iloc[0])
            cost.update(value_optimisation=cost['Rule']-cost['LP'],value_protection=cost['LP']-cost['Protected'],
                        total_value=cost['Rule']-cost['Protected'],optimisation_band=REL*abs(cost['Rule']),protection_band=.01*hind)
            rows.append(cost)
            monthly.append(table.assign(origin=origin,method=method,plan='Rule',month=months,horizon=range(1,7)))
            for kind,label in [('point','LP'),('protected','Protected')]:
                original=old_monthly[(old_monthly.origin==origin)&(old_monthly.method==method)&(old_monthly.kind==kind)&(old_monthly.penalty==5000)].sort_values('month')
                cols=['x','a','r','shortfall','spare','running','opening','closing','shortfall_cost','monthly_cost','origin','method','month','horizon']
                monthly.append(original[cols].assign(plan=label))
        cc.write_csv(OUT/origin/'actuals.csv',actual.reset_index().assign(gap=d))
    costs=pd.DataFrame(rows);monthlies=pd.concat(monthly,ignore_index=True)
    cc.write_csv(OUT/'costs.csv',costs);cc.write_csv(OUT/'monthly_costs.csv',monthlies)
    for origin in cfg['origins']:
        cc.write_csv(OUT/origin/'costs.csv',costs[costs.origin==origin])
        cc.write_csv(OUT/origin/'monthly_costs.csv',monthlies[monthlies.origin==origin])
    cc.verify_hashes(OUT/'rule_plans.sha256');verify_sources()
    print('SCORING COMPLETE: all source and Rule hashes unchanged',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['phase0','register','plan','score']);p.add_argument('--data-dir',type=Path)
    args=p.parse_args()
    try:
        if args.stage=='plan':plan()
        else:globals()[args.stage](args.data_dir.resolve())
    except BaseException as exc:
        jsave(OUT/'failure.json',dict(stage=args.stage,error=f'{type(exc).__name__}: {exc}'))
        raise
