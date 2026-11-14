"""Registered metrics M1-M7 and descriptive expectations E1-E3."""
import json
import sys
sys.dont_write_bytecode=True
import numpy as np
import pandas as pd
from value import OUT,SOURCE,METHODS,cc,read,jsave,check_config,counts,gap_shares,close_reopen_events,PLAN_TOL


def analyse():
    cfg=check_config();cc.verify_hashes(OUT/'rule_plans.sha256')
    costs=read(OUT/'costs.csv');monthly=cc.read_csv(OUT/'monthly_costs.csv')
    diagnostics=[]
    for (origin,method,plan),g in monthly.groupby(['origin','method','plan']):
        g=g.sort_values('month')
        diagnostics.append(dict(origin=origin,method=method,plan=plan,opening_cost=float(g.opening.sum()),
            closing_cost=float(g.closing.sum()),running_cost=float(g.running.sum()),
            shortfall_bed_months=float(g.shortfall.sum()),close_then_reopen=close_reopen_events(g.x.to_numpy())))
    diag=pd.DataFrame(diagnostics);cc.write_csv(OUT/'diagnostics_by_origin.csv',diag)
    summaries=[];subsets=[];diag_summary=[]
    for scope,origins in cfg['scopes'].items():
        for method in METHODS+['Pooled']:
            select=lambda df:df[df.origin.isin(origins)&(True if method=='Pooled' else df.method==method)]
            g=select(costs);shares=gap_shares(g.Rule,g.LP,g.Protected,g.Hindsight)
            oc=counts(g.value_optimisation,g.optimisation_band);pc=counts(g.value_protection,g.protection_band)
            result=dict(scope=scope,method=method,origin_count=len(origins),plan_count=len(g),
                        **{f'mean_{name}':float(g[name].mean()) for name in ['Rule','LP','Protected','Hindsight']},
                        lp_cheaper=oc['helped'],lp_equal=oc['tie'],lp_dearer=oc['hurt'],
                        protection_helped=pc['helped'],protection_tie=pc['tie'],protection_hurt=pc['hurt'],
                        share_denominator=shares['denominator'],optimisation_share=shares['optimisation'],
                        protection_share=shares['protection'],remaining_share=shares['remaining'])
            for metric,col in [('optimisation','value_optimisation'),('protection','value_protection'),('total_value','total_value')]:
                for stat in ['mean','median','min','max']:
                    result[f'{metric}_{stat}']=float(getattr(g[col],stat)())
            vals=[]
            for offset in range(6):
                os=origins[offset::6];sg=g[g.origin.isin(os)]
                vals.append(dict(scope=scope,method=method,offset=offset,origins=';'.join(os),origin_count=len(os),plan_count=len(sg),
                                 optimisation_mean=float(sg.value_optimisation.mean()),protection_mean=float(sg.value_protection.mean())))
            subsets.extend(vals)
            for metric in ['optimisation','protection']:
                result[f'{metric}_subset_min']=min(v[metric+'_mean'] for v in vals)
                result[f'{metric}_subset_max']=max(v[metric+'_mean'] for v in vals)
            summaries.append(result)
            for plan in ['Rule','LP']:
                dg=select(diag);dg=dg[dg.plan==plan]
                diag_summary.append(dict(scope=scope,method=method,plan=plan,plan_count=len(dg),
                    **{f'mean_{col}':float(dg[col].mean()) for col in ['opening_cost','closing_cost','running_cost','shortfall_bed_months','close_then_reopen']}))
    summary=pd.DataFrame(summaries)
    cc.write_csv(OUT/'summary.csv',summary);cc.write_csv(OUT/'subset_metrics.csv',pd.DataFrame(subsets));cc.write_csv(OUT/'diagnostics.csv',pd.DataFrame(diag_summary))
    rules=cc.read_csv(OUT/'rule_plans.csv');saved=cc.read_csv(SOURCE/'plans.csv')
    equality=[]
    for method in ['Mean-24','Persistence']:
        diff=[]
        for origin in cfg['origins']:
            x=rules[(rules.origin==origin)&(rules.method==method)].sort_values('month').x.to_numpy()
            y=saved[(saved.origin==origin)&(saved.method==method)&(saved.kind=='point')&(saved.penalty==5000)].sort_values('month').x.to_numpy()
            diff.append(float(np.abs(x-y).max()))
        equality.append(dict(method=method,maximum_difference_beds=max(diff),origins_matching=sum(v<=PLAN_TOL for v in diff),origins=len(diff)))
    adverse=costs[costs.value_optimisation < -costs.optimisation_band]
    cc.write_csv(OUT/'lp_dearer_cases.csv',adverse)
    base=summary[summary.scope=='all'].set_index('method')
    e3={m:float(base.loc[m,'optimisation_mean']) for m in ['Seasonal naive','SARIMA-24','ExtraTrees-24']}
    expectations=dict(E1=dict(held=all(e['origins_matching']==e['origins'] for e in equality),tolerance_beds=PLAN_TOL,methods=equality),
                      E2=dict(held=adverse.empty,lp_dearer_cases=len(adverse),total_cases=len(costs)),
                      E3=dict(held=all(v>0 for v in e3.values()),mean_values=e3),not_stop_conditions=True)
    jsave(OUT/'expectations.json',expectations)
    best={}
    for scope in cfg['scopes']:
        g=summary[(summary.scope==scope)&(summary.method!='Pooled')]
        candidates=[dict(method=row.method,plan=plan,mean_cost=float(getattr(row,'mean_'+plan))) for row in g.itertuples() for plan in ['Rule','LP','Protected']]
        best[scope]=sorted(candidates,key=lambda row:row['mean_cost'])
    jsave(OUT/'combination_ranking.json',best)
    print(summary[summary.scope=='all'].to_string(index=False),flush=True)
    print(json.dumps(expectations,indent=2),flush=True)


if __name__=='__main__':
    analyse()
