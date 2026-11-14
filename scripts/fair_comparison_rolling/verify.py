"""Independent checks of registered inputs, frozen plans and realized accounting.

The pre stage does not load evaluation actuals. Any failed check is recorded and
stops execution. The post stage also reruns two pre-selected complete origins.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
import numpy as np
import pandas as pd
import rolling as r

OUT=r.OUT
CHECKS=[]


def record(name,detail):
    CHECKS.append(dict(check=name,status='PASS',detail=detail))
    r.write_json(OUT/'verification_checks.json',CHECKS)
    print('PASS',name,detail,flush=True)


def assert_close(actual,expected,atol=1e-6,rtol=0):
    a,e=np.asarray(actual,dtype=float),np.asarray(expected,dtype=float)
    assert a.shape==e.shape,(a.shape,e.shape)
    assert np.isfinite(a).all() and np.isfinite(e).all()
    assert np.allclose(a,e,atol=atol,rtol=rtol),f'max difference {np.max(np.abs(a-e))}'


def csv(name):
    return pd.read_csv(OUT/name,float_precision='round_trip')


def preservation(allow_readme=False):
    original=json.loads((OUT/'prior_files_manifest.json').read_text())
    changed=[name for name,digest in original.items() if (not allow_readme or name!='README.md') and r.cc.sha256(r.REPO/name)!=digest]
    assert not changed,changed
    record('Preservation of pre-existing files',f'{len(original)} original file hashes checked; README exemption={allow_readme}')


def pre(data_dir):
    config=r.check_registration(OUT,data_dir)
    r.cc.verify_hashes(OUT/'plans.sha256')
    assert json.loads((OUT/'phase0_result.json').read_text())['status']=='PASS'
    source=r.verify_source(data_dir)
    raw=pd.read_excel(data_dir/source['file'],sheet_name=r.cc.SHEET,header=None)
    observed,placeholders=r.publication_inventory(raw)
    del raw
    assert observed.max()==pd.Timestamp('2026-10-01') and len(observed)==71
    record('Source, registered config and frozen plan hashes',f"config {r.cc.sha256(OUT/'config.json')}; plans {r.cc.sha256(OUT/'plans.csv')}")
    record('Approved publication boundary',f'{len(observed)} complete observations; {len(placeholders)} blank trailing rows ignored; no internal blanks')
    audits=json.loads((OUT/'planning_convergence.json').read_text())
    included=[a['origin'] for a in audits if a['status']=='included']
    assert included==config['origins']
    excluded=[dict(origin=a['origin'],failed_targets=a['failed_targets']) for a in audits if a['status']=='excluded']
    assert excluded==config['planning_origin_convergence']['excluded']
    for a in audits:
        assert a['data_cutoff']==a['origin']
        assert a['status']=='excluded' if a['failed_targets'] else a['status']=='included'
    record('Planning-origin exclusions before scoring',json.dumps(excluded))
    plans=r.cc.read_csv(OUT/'plans.csv')
    assert len(plans)==len(included)*5*4*6
    tol=config['verification']['solver_plan_tolerance_beds']
    for (origin,method,kind,p),g in plans.groupby(['origin','method','kind','penalty']):
        g=g.sort_values('month'); x=g.x.to_numpy(); changes=np.diff(np.r_[0.,x])
        assert list(g.month)==list(r.cc.target_months(pd.Timestamp(origin)))
        assert np.isfinite(g[['x','a','r']].to_numpy()).all()
        assert (x>=-tol).all() and (x<=15000+tol).all() and (np.abs(changes)<=5000+tol).all()
        assert_close(g.a,np.maximum(changes,0),tol)
        assert_close(g.r,np.maximum(-changes,0),tol)
        assert (kind=='point' and p==5000) or (kind=='protected' and p in r.PENALTIES)
    record('All frozen plans feasible',f'{len(plans)//6} six-month plans; cap, ramp, zero start, flows, dates and finiteness')
    master=r.read_bank(OUT/'cache/backtests.csv')
    fit=json.loads((OUT/'fit_log.json').read_text())
    total_paths=0
    for origin in included:
        o=pd.Timestamp(origin)
        r.assert_observed_window(observed,o-pd.DateOffset(months=23),r.cc.target_months(o)[-1])
        bank=r.read_bank(OUT/origin/'backtests.csv')
        r.compare_banks(bank,r.bank_for_origin(master,o))
        n=bank.backtest_origin.nunique(); total_paths+=n
        assert n>=9 and (bank.month<=o).all()
        b=pd.to_datetime(bank.backtest_origin); start=pd.to_datetime(bank.window_start)
        assert (start==b-pd.DateOffset(months=23)).all() and (start>=observed[0]).all()
        assert (b<bank.month).all() and (b<=o-pd.DateOffset(months=6)).all()
        assert_close(bank.error_occupied,bank.actual_occupied-bank.forecast_occupied)
        assert_close(bank.error_available,bank.actual_available-bank.forecast_available)
        assert_close(bank.error_gap,bank.error_occupied/.9-bank.error_available)
        assert fit[origin]['data_last_month']==origin
        assert fit[origin]['window']==[f'{o-pd.DateOffset(months=23):%Y-%m}',origin]
        for log in fit[origin]['sarima'].values():
            assert log['converged'] and log['label_end']==origin and log['labels']==24
        for log in fit[origin]['extra_trees'].values():
            assert log['training_rows']==12 and log['last_row']==origin
            assert log['earliest_month_used']==fit[origin]['window'][0]
        fc=r.cc.read_csv(OUT/origin/'forecasts.csv')
        scenarios=r.cc.read_csv(OUT/origin/'scenarios.csv')
        quant=r.cc.read_csv(OUT/origin/'quantiles.csv')
        for method in r.METHODS:
            point=fc[fc.method==method].sort_values('month').forecast_gap.to_numpy()
            errors=bank[bank.method==method].pivot(index='backtest_origin',columns='horizon',values='error_gap').sort_index().to_numpy()
            actual_scen=scenarios[scenarios.method==method].pivot(index='backtest_origin',columns='horizon',values='scenario_gap').sort_index().to_numpy()
            assert_close(actual_scen,point[None,:]+errors)
            rank=min(n,math.ceil(round((n+1)*.9,9)))
            q=quant[quant.method==method].sort_values('month')
            assert (q['rank']==rank).all()
            assert_close(q.quantile_gap,np.sort(actual_scen,axis=0)[rank-1])
    record('Cutoff, training and shared-cache isolation',f'{len(included)} origins and {total_paths} reused whole paths checked; each path ends by its planning origin')
    record('Raw paired scenarios and 0.9 quantiles','All methods/origins match independent reconstruction; no centering or clipping')
    sample=config['verification']['rerun_sample_origins']
    max_equivalence=0.
    for origin in sample:
        fc=r.cc.read_csv(OUT/origin/'forecasts.csv')
        for method in r.METHODS:
            point=fc[fc.method==method].forecast_gap.to_numpy()
            x=r.cc.lp_plan(point)[0]
            saa=r.fair.saa_plan(np.tile(point,(9,1)),5000)
            max_equivalence=max(max_equivalence,float(np.abs(x-saa).max()))
            assert_close(saa,x,atol=tol)
    record('Identical-scenario Protected equals Point',f'{sample}; max difference {max_equivalence:.3g} beds')
    preservation()
    return config


def post(data_dir):
    config=r.check_registration(OUT,data_dir)
    r.cc.verify_hashes(OUT/'plans.sha256')
    totals=csv('totals.csv'); monthly=r.cc.read_csv(OUT/'monthly_costs.csv')
    frozen=r.cc.read_csv(OUT/'plans.csv')
    accuracy=csv('accuracy.csv'); horizon=r.cc.read_csv(OUT/'horizon_accuracy.csv')
    tol=config['verification']['cost_relative_tolerance']
    data=r.cc.load(data_dir,cutoff=pd.Timestamp(config['last_published_month']))
    max_cost_error=0.
    for (origin,method,kind,penalty),group in monthly.groupby(['origin','method','kind','penalty']):
        group=group.sort_values('month'); x=group.x.to_numpy()
        months=r.cc.target_months(pd.Timestamp(origin)); actual=data.loc[months]
        assert list(group.month)==list(months) and not actual.isna().any().any()
        d=actual.occupied.to_numpy()/.9-actual.available.to_numpy()
        change=np.diff(np.r_[0.,x]); a=np.maximum(change,0); rem=np.maximum(-change,0)
        short=np.maximum(d-x,0); operating=500*x+550*a+300*rem; costs=operating+penalty*short
        assert_close(group.a,a); assert_close(group.r,rem)
        assert_close(group.shortfall,short); assert_close(group.spare,np.maximum(x-d,0))
        assert_close(group.shortfall_cost,penalty*short,rtol=tol)
        assert_close(group.monthly_cost,costs,rtol=tol)
        total=totals[(totals.origin==origin)&(totals.method==method)&(totals.kind==kind)&(totals.penalty==penalty)]
        assert len(total)==1
        t=total.iloc[0]
        assert_close([t.total,t.operating,t.shortfall_cost,t.shortfall_bed_months],
                     [costs.sum(),operating.sum(),penalty*short.sum(),short.sum()],rtol=tol)
        max_cost_error=max(max_cost_error,abs(float(t.total-costs.sum()))/max(abs(t.total),1))
        assert t.hindsight<=t.total+tol*max(abs(t.hindsight),1)
        assert_close([t.excess_over_hindsight],[t.total-t.hindsight],rtol=tol)
        if kind!='hindsight':
            f=frozen[(frozen.origin==origin)&(frozen.method==method)&(frozen.kind==kind)&(frozen.penalty==penalty)].sort_values('month')
            assert np.array_equal(x,f.x.to_numpy())
        assert (x>=-1e-3).all() and (x<=15000+1e-3).all() and (np.abs(change)<=5000+1e-3).all()
    assert len(totals)==len(config['origins'])*23
    record('Independent accounting and hindsight lower bound',f'{len(totals)} plans including hindsight; max relative cost difference {max_cost_error:.3g}; monthly costs reconcile')
    for row in accuracy.itertuples():
        g=horizon[(horizon.origin==row.origin)&(horizon.method==row.method)].sort_values('month')
        assert len(g)==6
        d=data.loc[g.month].occupied.to_numpy()/.9-data.loc[g.month].available.to_numpy()
        err=d-g.point_gap.to_numpy(); delta=d-g.quantile_gap.to_numpy()
        loss=np.where(delta>=0,.9*delta,-.1*delta)
        assert_close(g.actual_gap,d)
        assert_close(g.pinball_090,loss)
        assert np.array_equal(g.covered_090.to_numpy(),d<=g.quantile_gap.to_numpy())
        assert_close([row.gap_mae,row.gap_mean_signed_error,row.pinball_090],
                     [np.abs(err).mean(),err.mean(),loss.mean()])
        assert row.covered_090==int((delta<=0).sum()) and row.months==6
    record('Forecast errors, pinball and coverage','Recomputed directly from actual observations and saved point/quantile forecasts')
    max_repro=0.
    for origin,key in r.FABLE.items():
        old=pd.read_csv(r.REPO/'outputs/fair_comparison'/key/'totals.csv',float_precision='round_trip')
        new=totals[totals.origin==origin]
        for method in r.METHODS:
            select=lambda t:float(t[(t.method==method)&(t.kind=='protected')&(t.penalty==5000)].total.iloc[0])
            expected,actual=select(old),select(new)
            assert_close([actual],[expected],rtol=tol)
            max_repro=max(max_repro,abs(actual-expected)/abs(expected))
        oldfc=r.cc.read_csv(r.REPO/'outputs/fair_comparison'/key/'forecasts.csv')
        newfc=r.cc.read_csv(OUT/origin/'forecasts.csv')
        assert_close(newfc.filter(like='forecast_'),oldfc.filter(like='forecast_'))
    record('Fable origins reproduced inside full run',f'max relative primary protected-cost difference {max_repro:.3g}')
    max_forecast,max_bank,max_plan=0.,0.,0.
    for origin in config['verification']['rerun_sample_origins']:
        o=pd.Timestamp(origin); history=r.cc.load(data_dir,cutoff=o)
        rerun_fc,_=r.cc.forecast_origin(history,o)
        saved_fc=r.cc.read_csv(OUT/origin/'forecasts.csv')
        max_forecast=max(max_forecast,float(np.abs(rerun_fc.filter(like='forecast_').to_numpy()-saved_fc.filter(like='forecast_').to_numpy()).max()))
        assert_close(rerun_fc.filter(like='forecast_'),saved_fc.filter(like='forecast_'))
        bank,dropped=r.fair.build_bank(history,o)
        max_bank=max(max_bank,r.compare_banks(bank,r.read_bank(OUT/origin/'backtests.csv')))
        for method in r.METHODS:
            point=rerun_fc[rerun_fc.method==method].forecast_gap.to_numpy()
            scen=r.fair.scenarios(point,r.fair.error_paths(bank,method))
            for kind,p,x in [('point',5000,r.cc.lp_plan(point)[0])]+[('protected',p,r.fair.saa_plan(scen,p)) for p in r.PENALTIES]:
                old=frozen[(frozen.origin==origin)&(frozen.method==method)&(frozen.kind==kind)&(frozen.penalty==p)].sort_values('month').x.to_numpy()
                max_plan=max(max_plan,float(np.abs(old-x).max()))
                assert_close(x,old,atol=1e-3)
        print('RERUN COMPLETE',origin,flush=True)
    record('Complete reruns at two registered sample origins',f'max forecast {max_forecast:.3g} beds; bank {max_bank:.3g}; plan {max_plan:.3g}')
    r.cc.verify_hashes(OUT/'plans.sha256')
    preservation()


def rules():
    """Independent loop-based summary checks, not calls to summarize.decide."""
    config=json.loads((OUT/'config.json').read_text())
    totals=csv('totals.csv'); acc=csv('accuracy.csv'); h=csv('horizon_accuracy.csv')
    summaries=csv('summary.csv'); subsets=csv('subset_means.csv'); cover=csv('coverage_by_horizon.csv')
    rankrows=csv('per_origin_ranks.csv'); pairrows=csv('pairwise.csv')
    decisions=json.loads((OUT/'decisions.json').read_text())
    for scope,origins in config['scopes'].items():
        for p in r.PENALTIES:
            costs={}; floors={}; means={}; subset_wins={}
            for origin in origins:
                t=totals[(totals.origin==origin)&(totals.penalty==p)]
                floors[origin]=float(t[t.kind=='hindsight'].total.iloc[0])
                costs[origin]={m:float(t[(t.kind=='protected')&(t.method==m)].total.iloc[0]) for m in r.METHODS}
            for m in r.METHODS:
                means[m]=sum(costs[o][m]-floors[o] for o in origins)/len(origins)
            subset_values=[]
            for offset in range(6):
                os=origins[offset::6]
                # Every sixth retained origin is at least six calendar months apart.
                dates=pd.to_datetime(os)
                assert all((dates[i+1].year-dates[i].year)*12+dates[i+1].month-dates[i].month>=6 for i in range(len(dates)-1))
                subset_values.append({m:sum(costs[o][m]-floors[o] for o in os)/len(os) for m in r.METHODS})
            for m in r.METHODS:
                subset_wins[m]=sum(v[m]==min(v.values()) for v in subset_values)
                sr=summaries[(summaries.scope==scope)&(summaries.penalty==p)&(summaries.method==m)].iloc[0]
                joint=sum(costs[o][m]<=min(costs[o].values())+.01*floors[o] for o in origins)
                a=acc[(acc.origin.isin(origins))&(acc.method==m)]
                w=sum(costs[o][m]<costs[o]['Seasonal naive']-.01*floors[o] for o in origins)
                l=sum(costs[o][m]>costs[o]['Seasonal naive']+.01*floors[o] for o in origins)
                assert_close([sr.mean_excess_over_hindsight,sr.mean_pinball_090,sr.coverage_090],
                             [means[m],a.pinball_090.mean(),a.covered_090.sum()/(6*len(origins))],rtol=1e-12)
                assert [sr.joint_best_count,sr.wins_vs_seasonal_naive,sr.ties_vs_seasonal_naive,sr.losses_vs_seasonal_naive,sr.subset_lowest_count]==[joint,w,len(origins)-w-l,l,subset_wins[m]]
                assert sr.covered_090==a.covered_090.sum() and sr.months==6*len(origins)
                sv=[v[m] for v in subset_values]
                assert_close([sr.subset_min_mean,sr.subset_max_mean],[min(sv),max(sv)],rtol=1e-12)
                if p==5000:
                    point=totals[(totals.origin.isin(origins))&(totals.method==m)&(totals.kind=='point')]
                    expected=(point.total.sum()-sum(costs[o][m] for o in origins))/len(origins)
                    assert_close([sr.mean_value_of_protection],[expected],rtol=1e-12)
                for offset in range(6):
                    row=subsets[(subsets.scope==scope)&(subsets.penalty==p)&(subsets.method==m)&(subsets.offset==offset)].iloc[0]
                    assert_close([row.mean_excess_over_hindsight],[sv[offset]],rtol=1e-12)
                    assert row.origin_list==';'.join(origins[offset::6])
                for other in r.METHODS:
                    pr=pairrows[(pairrows.scope==scope)&(pairrows.penalty==p)&(pairrows.method==m)&(pairrows.comparator==other)].iloc[0]
                    wins=sum(costs[o][m]<costs[o][other]-.01*floors[o] for o in origins)
                    losses=sum(costs[o][m]>costs[o][other]+.01*floors[o] for o in origins)
                    assert [pr.wins,pr.ties,pr.losses]==[wins,len(origins)-wins-losses,losses]
                for origin in origins:
                    row=rankrows[(rankrows.scope==scope)&(rankrows.penalty==p)&(rankrows.method==m)&(rankrows.origin==origin)].iloc[0]
                    assert bool(row.joint_best)==(costs[origin][m]<=min(costs[origin].values())+.01*floors[origin])
                    assert row['rank']==1+sum(value<costs[origin][m] for value in costs[origin].values())
            order=sorted(r.METHODS,key=lambda m:(means[m],r.METHODS.index(m)))
            top,runner=order[:2]
            w=sum(costs[o][top]<costs[o][runner]-.01*floors[o] for o in origins)
            l=sum(costs[o][top]>costs[o][runner]+.01*floors[o] for o in origins)
            clear=top if means[top]<means[runner] and subset_wins[top]>=4 and w>l else None
            d=decisions['scopes'][scope]['penalties'][str(int(p))]
            assert d['ranking']==order and d['clear_winner']==clear and d['subset_wins']==subset_wins
            assert d['wins_ties_losses_vs_runner']==dict(wins=w,ties=len(origins)-w-l,losses=l)
        for m in r.METHODS:
            for step in range(1,7):
                g=h[(h.origin.isin(origins))&(h.method==m)&(h.horizon==step)]
                cr=cover[(cover.scope==scope)&(cover.method==m)&(cover.horizon==step)].iloc[0]
                assert cr.covered==g.covered_090.sum() and cr.months==len(g)
                assert_close([cr.coverage],[g.covered_090.mean()])
        d=decisions['scopes'][scope]
        same_top=len({v['top'] for v in d['penalties'].values()})==1
        winners=[v['clear_winner'] for v in d['penalties'].values()]
        same_clear=len(set(winners))==1 and all(winners)
        assert d['robustness']['same_top_method']==same_top and d['robustness']['same_clear_winner']==same_clear
    record('Registered decision rules and every summary table','Independent loops reproduce all four scopes, three penalties, six offset subsets, ranks, pairwise counts, coverage and protection value')


def tests():
    command=[sys.executable,'-B','-m','pytest','-p','no:cacheprovider','scripts/clean_checkpoint/test_pipeline.py',
             'scripts/fair_comparison/test_fair.py','scripts/fair_comparison_rolling','-q','--disable-warnings']
    run=subprocess.run(command,cwd=r.REPO,capture_output=True,text=True)
    r.cc.write_text(OUT/'pytest.txt',' '.join(command)+'\n'+run.stdout+run.stderr)
    assert run.returncode==0,run.stdout+run.stderr
    record('Original and rolling pytest suites',run.stdout.strip().splitlines()[-1])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['pre','post','rules','tests'])
    parser.add_argument('--data-dir',type=Path)
    args=parser.parse_args()
    if (OUT/'verification_checks.json').exists():
        CHECKS=json.loads((OUT/'verification_checks.json').read_text())
    try:
        if args.stage in ['pre','post']:
            globals()[args.stage](args.data_dir.resolve())
        else:
            globals()[args.stage]()
    except BaseException as exc:
        CHECKS.append(dict(check=args.stage,status='FAIL',detail=f'{type(exc).__name__}: {exc}'))
        r.write_json(OUT/'verification_checks.json',CHECKS)
        r.write_json(OUT/'failure.json',dict(stage=args.stage,error=f'{type(exc).__name__}: {exc}'))
        raise
