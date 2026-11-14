"""Independent verification, stopping on any failed scientific check."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
import numpy as np
import pandas as pd
import value as v

CHECKS=[]


def record(name,detail):
    CHECKS.append(dict(check=name,status='PASS',detail=detail))
    v.jsave(v.OUT/'verification_checks.json',CHECKS)
    print('PASS',name,detail,flush=True)


def close(a,b,rtol=1e-6,atol=1e-6):
    a,b=np.asarray(a,dtype=float),np.asarray(b,dtype=float)
    assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all()
    assert np.allclose(a,b,rtol=rtol,atol=atol),float(np.abs(a-b).max())


def preservation():
    manifest=json.loads((v.OUT/'prior_files_manifest.json').read_text())
    changed=[p for p,digest in manifest.items() if v.cc.sha256(v.REPO/p)!=digest]
    assert not changed,changed
    record('Original files unchanged',f'{len(manifest)} hashes match, including old scripts, outputs and pre-existing untracked files')


def pre():
    cfg=v.check_config();v.cc.verify_hashes(v.OUT/'rule_plans.sha256')
    rules=v.cc.read_csv(v.OUT/'rule_plans.csv')
    fit=json.loads((v.SOURCE/'fit_log.json').read_text())
    assert len(rules)==25*5*6
    for (origin,method),g in rules.groupby(['origin','method']):
        g=g.sort_values('month');o=pd.Timestamp(origin)
        assert list(g.month)==list(v.cc.target_months(o))
        assert fit[origin]['data_last_month']==origin and fit[origin]['window'][-1]==origin
        assert fit[origin]['window'][0]==f'{o-pd.DateOffset(months=23):%Y-%m}'
        for entry in fit[origin]['sarima'].values():
            assert entry['label_end']==origin
        for entry in fit[origin]['extra_trees'].values():
            assert entry['last_row']==origin and entry['earliest_month_used']==fit[origin]['window'][0]
        fc=v.cc.read_csv(v.SOURCE/origin/'forecasts.csv')
        fc=fc[fc.method==method].sort_values('month')
        assert list(fc.month)==list(g.month)
        x=g.x.to_numpy();a=g.a.to_numpy();r=g.r.to_numpy()
        assert (x>=0).all() and (x<=15000).all() and (a>=0).all() and (a<=5000).all() and (r>=0).all() and (r<=5000).all()
        close(x,np.r_[0.,x[:-1]]+a-r,rtol=0)
        assert np.array_equal(x,v.cc.rule_plan(fc.forecast_gap)),f'{origin}/{method}: Rule regeneration differs'
    record('Hashes, cutoff provenance and deterministic Rule regeneration','All 125 Rule schedules reproduce bit-for-bit; source forecast and plan hashes verified; target dates and fitting cutoffs agree')
    record('Rule feasibility','All caps, up/down ramps, positive flows, month order and zero starting capacity checked')
    preservation()


def post(data_dir):
    cfg=v.check_config();v.cc.verify_hashes(v.OUT/'rule_plans.sha256')
    data=v.cc.load(data_dir,cutoff=pd.Timestamp('2026-10-01'))
    assert data.index[-1]==pd.Timestamp('2026-10-01') and not data.isna().any().any()
    costs=v.read(v.OUT/'costs.csv');monthly=v.cc.read_csv(v.OUT/'monthly_costs.csv');rules=v.cc.read_csv(v.OUT/'rule_plans.csv')
    saved=v.read(v.SOURCE/'totals.csv')
    assert len(costs)==125 and len(monthly)==125*3*6
    maximum=0.
    for row in costs.itertuples():
        actual=data.loc[v.cc.target_months(pd.Timestamp(row.origin))]
        d=actual.occupied.to_numpy()/.9-actual.available.to_numpy()
        saved_h=float(saved[(saved.origin==row.origin)&(saved.kind=='hindsight')&(saved.penalty==5000)].total.iloc[0])
        assert row.Hindsight==saved_h
        for plan in ['Rule','LP','Protected']:
            g=monthly[(monthly.origin==row.origin)&(monthly.method==row.method)&(monthly.plan==plan)].sort_values('month')
            x=g.x.to_numpy();change=np.diff(np.r_[0.,x]);a=np.maximum(change,0);r=np.maximum(-change,0)
            short=np.maximum(d-x,0);expected=500*x+550*a+300*r+5000*short
            close(g.running,500*x);close(g.opening,550*a);close(g.closing,300*r)
            close(g.shortfall,short);close(g.shortfall_cost,5000*short);close(g.monthly_cost,expected)
            total=float(getattr(row,plan));close([total],[expected.sum()])
            maximum=max(maximum,abs(total-expected.sum())/total)
            assert row.Hindsight<=total+1e-6*abs(row.Hindsight)
            if plan=='Rule':
                old=rules[(rules.origin==row.origin)&(rules.method==row.method)].sort_values('month').x.to_numpy()
                assert np.array_equal(old,x)
            else:
                kind='point' if plan=='LP' else 'protected'
                source=float(saved[(saved.origin==row.origin)&(saved.method==row.method)&(saved.kind==kind)&(saved.penalty==5000)].total.iloc[0])
                assert source==total
        close([row.value_optimisation,row.value_protection,row.total_value],[row.Rule-row.LP,row.LP-row.Protected,row.Rule-row.Protected])
    record('Independent cost reconstruction and hindsight bounds',f'375 schedules; maximum relative cost difference {maximum:.3g}; every monthly sum reconciles; reused scores unchanged')
    summary=v.read(v.OUT/'summary.csv');diag=v.read(v.OUT/'diagnostics.csv');subset=v.read(v.OUT/'subset_metrics.csv')
    per_diag=v.read(v.OUT/'diagnostics_by_origin.csv')
    for (origin,method,plan),g in monthly.groupby(['origin','method','plan']):
        g=g.sort_values('month');event_count=0
        for i in range(6):
            if g.r.iloc[i]>1e-6:
                event_count+=int(any(g.a.iloc[j]>1e-6 for j in range(i+1,min(i+3,6))))
        row=per_diag[(per_diag.origin==origin)&(per_diag.method==method)&(per_diag.plan==plan)].iloc[0]
        assert row.close_then_reopen==event_count
        close([row.opening_cost,row.closing_cost,row.running_cost,row.shortfall_bed_months],
              [g.opening.sum(),g.closing.sum(),g.running.sum(),g.shortfall.sum()])
    for row in summary.itertuples():
        origins=cfg['scopes'][row.scope]
        g=costs[costs.origin.isin(origins)&(True if row.method=='Pooled' else costs.method==row.method)]
        assert row.plan_count==len(g) and row.origin_count==len(origins)
        for name in ['Rule','LP','Protected','Hindsight']:
            close([getattr(row,'mean_'+name)],[sum(g[name])/len(g)],rtol=1e-12)
        differences={'optimisation':g.Rule-g.LP,'protection':g.LP-g.Protected,'total_value':g.Rule-g.Protected}
        for name,values in differences.items():
            close([getattr(row,f'{name}_{s}') for s in ['mean','median','min','max']],
                  [np.mean(values),np.median(values),min(values),max(values)],rtol=1e-12)
        oc=[sum(g.value_optimisation>1e-6*g.Rule.abs()),sum(g.value_optimisation.abs()<=1e-6*g.Rule.abs()),sum(g.value_optimisation < -1e-6*g.Rule.abs())]
        pc=[sum(g.value_protection>.01*g.Hindsight),sum(g.value_protection.abs()<=.01*g.Hindsight),sum(g.value_protection < -.01*g.Hindsight)]
        assert [row.lp_cheaper,row.lp_equal,row.lp_dearer]==oc
        assert [row.protection_helped,row.protection_tie,row.protection_hurt]==pc
        den=float((g.Rule-g.Hindsight).sum())
        close([row.share_denominator,row.optimisation_share,row.protection_share,row.remaining_share],
              [den,(g.Rule-g.LP).sum()/den,(g.LP-g.Protected).sum()/den,(g.Protected-g.Hindsight).sum()/den],rtol=1e-12)
        close([row.optimisation_share+row.protection_share+row.remaining_share],[1.],rtol=1e-12)
        subvalues=[]
        for offset in range(6):
            os=origins[offset::6];sg=g[g.origin.isin(os)]
            expected=[float(sg.value_optimisation.mean()),float(sg.value_protection.mean())];subvalues.append(expected)
            actual=subset[(subset.scope==row.scope)&(subset.method==row.method)&(subset.offset==offset)].iloc[0]
            assert actual.origins==';'.join(os)
            close([actual.optimisation_mean,actual.protection_mean],expected,rtol=1e-12)
            dates=pd.to_datetime(os)
            assert all((dates[i+1].year-dates[i].year)*12+dates[i+1].month-dates[i].month>=6 for i in range(len(dates)-1))
        subvalues=np.array(subvalues)
        close([row.optimisation_subset_min,row.optimisation_subset_max,row.protection_subset_min,row.protection_subset_max],
              [subvalues[:,0].min(),subvalues[:,0].max(),subvalues[:,1].min(),subvalues[:,1].max()],rtol=1e-12)
        for plan in ['Rule','LP']:
            dg=per_diag[per_diag.origin.isin(origins)&(per_diag.plan==plan)&(True if row.method=='Pooled' else per_diag.method==row.method)]
            dr=diag[(diag.scope==row.scope)&(diag.method==row.method)&(diag.plan==plan)].iloc[0]
            for col in ['opening_cost','closing_cost','running_cost','shortfall_bed_months','close_then_reopen']:
                close([dr['mean_'+col]],[dg[col].mean()],rtol=1e-12)
    record('M1-M7, including pooled and excluded-anchor analyses','Independent means, medians, extremes, counts, ratios of sums, event counter, and all six subset values/ranges agree')
    expected=json.loads((v.OUT/'expectations.json').read_text())
    assert expected['E2']['held']==bool((costs.value_optimisation>=-costs.optimisation_band).all())
    assert expected['E2']['lp_dearer_cases']==int((costs.value_optimisation < -costs.optimisation_band).sum())
    base=summary[summary.scope=='all'].set_index('method')
    assert expected['E3']['held']==all(base.loc[m,'optimisation_mean']>0 for m in ['Seasonal naive','SARIMA-24','ExtraTrees-24'])
    record('Expectations evaluated without gating results',f"E1={expected['E1']['held']}, E2={expected['E2']['held']}, E3={expected['E3']['held']}; adverse cases retained")
    v.cc.verify_hashes(v.OUT/'rule_plans.sha256');v.verify_sources();preservation()


def tests():
    command=[sys.executable,'-B','-m','pytest','-p','no:cacheprovider','scripts/clean_checkpoint/test_pipeline.py',
             'scripts/fair_comparison/test_fair.py','scripts/fair_comparison_rolling','scripts/optimisation_value','-q','--disable-warnings']
    run=subprocess.run(command,cwd=v.REPO,capture_output=True,text=True)
    v.cc.write_text(v.OUT/'pytest.txt',' '.join(command)+'\n'+run.stdout+run.stderr)
    assert run.returncode==0,run.stdout+run.stderr
    record('Existing and new pytest suites',run.stdout.strip().splitlines()[-1])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['pre','post','tests']);parser.add_argument('--data-dir',type=Path)
    args=parser.parse_args()
    if (v.OUT/'verification_checks.json').exists():CHECKS=json.loads((v.OUT/'verification_checks.json').read_text())
    try:
        if args.stage=='post':post(args.data_dir.resolve())
        else:globals()[args.stage]()
    except BaseException as exc:
        CHECKS.append(dict(check=args.stage,status='FAIL',detail=f'{type(exc).__name__}: {exc}'))
        v.jsave(v.OUT/'verification_checks.json',CHECKS)
        v.jsave(v.OUT/'failure.json',dict(stage=args.stage,error=f'{type(exc).__name__}: {exc}'))
        raise
