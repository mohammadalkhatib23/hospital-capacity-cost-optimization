"""Generate the requested figures and README section from verified rolling files."""
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from rolling import OUT, REPO, METHODS, cc, write_json

COLORS={'Seasonal naive':'#E69F00','SARIMA-24':'#0072B2','ExtraTrees-24':'#009E73','Mean-24':'#CC79A7','Persistence':'#D55E00'}
STYLES={'Seasonal naive':'-','SARIMA-24':'--','ExtraTrees-24':'-.','Mean-24':':','Persistence':(0,(5,2,1,2))}
MARKERS={'Seasonal naive':'o','SARIMA-24':'s','ExtraTrees-24':'^','Mean-24':'D','Persistence':'v'}
LABELS={'all':'All retained origins','excluding_fable':'Excluding September 2025 and March 2026',
        'through_2024':'Origins through December 2024','from_2025':'Origins from January 2025'}
PREFIX='outputs/fair_comparison_rolling/'


def frame(out,name):
    return pd.read_csv(out/name,float_precision='round_trip')


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,row))+' |' for row in rows])


def plots(out,figdir):
    summary=frame(out,'summary.csv'); ranks=frame(out,'per_origin_ranks.csv'); coverage=frame(out,'coverage_by_horizon.csv')
    primary=summary[(summary.scope=='all')&(summary.penalty==5000)].sort_values('mean_excess_over_hindsight')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                         'axes.titleweight':'bold','axes.titlesize':14,'svg.fonttype':'none','savefig.facecolor':'white'})
    figdir.mkdir(parents=True,exist_ok=True)
    files=[]
    def save(fig,name):
        for suffix in ['png','svg']:
            p=figdir/f'{name}.{suffix}'
            fig.savefig(p,dpi=300,bbox_inches='tight')
            if suffix=='svg':
                # Matplotlib's Windows text export includes CRLF and spaces at
                # path-line ends. Normalize serialization without changing SVG.
                cc.write_text(p,'\n'.join(line.rstrip() for line in p.read_text(encoding='utf-8').splitlines())+'\n')
            files.append(dict(file=str(p.relative_to(REPO)).replace('\\','/'),sha256=cc.sha256(p)))
        plt.close(fig)
    f1=ranks[(ranks.scope=='all')&(ranks.penalty==5000)]
    cc.write_csv(out/'figure_F1_data.csv',f1)
    fig,ax=plt.subplots(figsize=(11.2,5.4),layout='constrained')
    for m in primary.method:
        g=f1[f1.method==m].sort_values('origin')
        ax.plot(pd.to_datetime(g.origin),g.excess_over_hindsight/1e6,label=m,color=COLORS[m],
                linestyle=STYLES[m],marker=MARKERS[m],markersize=4,linewidth=1.8)
    ax.set(title='Excess over hindsight varies across planning origins',ylabel='Excess cost (million model units)',xlabel='Planning origin')
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3));ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax.grid(axis='y',alpha=.22);ax.set_ylim(bottom=0)
    ax.legend(loc='upper center',bbox_to_anchor=(.5,-.16),ncol=5,frameon=False,fontsize=9)
    fig.suptitle('Protected plans | Shortfall penalty 5,000 | Excluded origins omitted',fontsize=10,fontweight='normal',y=1.04)
    save(fig,'F1_excess_by_origin')
    cc.write_csv(out/'figure_F2_data.csv',primary)
    fig,ax=plt.subplots(figsize=(9.5,4.6),layout='constrained')
    for i,row in enumerate(primary.itertuples()):
        center=row.mean_excess_over_hindsight/1e6
        artists=ax.errorbar(center,i,xerr=np.array([[center-row.subset_min_mean/1e6],[row.subset_max_mean/1e6-center]]),
                             fmt=MARKERS[row.method],color=COLORS[row.method],markersize=8,elinewidth=2,capsize=5)
        for bars in artists[2]:
            bars.set_linestyle(STYLES[row.method])
    ax.set_yticks(range(len(primary)),primary.method);ax.invert_yaxis();ax.set_xlim(left=0)
    ax.set(title='Mean excess cost and the range across offset subsets',xlabel='Excess cost (million model units)')
    ax.grid(axis='x',alpha=.22)
    fig.suptitle('Points: all-origin means | Bars: smallest to largest subset mean, not confidence intervals',fontsize=9.5,y=1.04)
    save(fig,'F2_mean_and_subset_range')
    f3=coverage[coverage.scope=='all']
    cc.write_csv(out/'figure_F3_data.csv',f3)
    fig,ax=plt.subplots(figsize=(9.5,5.1),layout='constrained')
    for m in primary.method:
        g=f3[f3.method==m].sort_values('horizon')
        ax.plot(g.horizon,g.coverage*100,label=m,color=COLORS[m],linestyle=STYLES[m],
                marker=MARKERS[m],markersize=6,markerfacecolor='white',linewidth=1.8)
    ax.axhline(90,color='#333333',linestyle=(0,(2,2)),linewidth=1,label='90% target')
    ax.set(title='Coverage is mostly above target, with some horizon shortfalls',xlabel='Months ahead',ylabel='Actual gap at or below the 0.9 quantile (%)',
           xticks=range(1,7),ylim=(75,103),yticks=[75,80,85,90,95,100])
    ax.grid(axis='y',alpha=.22);ax.legend(loc='upper center',bbox_to_anchor=(.5,-.17),ncol=3,frameon=False,fontsize=9)
    fig.suptitle(f'{int(primary.origins.iloc[0])} origin–horizon cases per point | Cases overlap across origins',fontsize=10,y=1.04)
    save(fig,'F3_coverage_by_horizon')
    write_json(out/'figure_inventory.json',files)


def build(out=OUT,figdir=REPO/'figures/fair_comparison_rolling'):
    checks=json.loads((out/'verification_checks.json').read_text())
    assert checks and all(c['status']=='PASS' for c in checks)
    assert any(c['check']=='Registered decision rules and every summary table' for c in checks)
    cfg=json.loads((out/'config.json').read_text()); decisions=json.loads((out/'decisions.json').read_text())
    summary=frame(out,'summary.csv'); cover=frame(out,'coverage_by_horizon.csv'); subsets=frame(out,'subset_means.csv')
    primary=summary[(summary.scope=='all')&(summary.penalty==5000)].set_index('method')
    audit=[]
    def fmt(value,source,field,form='d'):
        if form=='d':shown=str(int(value))
        elif form=='m':shown=f'{float(value)/1e6:.2f}'
        elif form=='pct':shown=f'{float(value):.1%}'
        elif form=='bed':shown=f'{float(value):.1f}'
        else:shown=str(value)
        audit.append(dict(source=source,field=field,value=value.item() if isinstance(value,np.generic) else value,display=shown,format=form))
        return shown
    def result_table(scope):
        rows=[]
        g=summary[(summary.scope==scope)&(summary.penalty==5000)].sort_values('mean_excess_over_hindsight')
        for row in g.itertuples():
            ref=f'{scope}/5000/{row.method}'
            get=lambda col,form='d':fmt(getattr(row,col),'summary.csv',ref+'/'+col,form)
            rows.append([row.method,get('mean_excess_over_hindsight','m'),get('joint_best_count'),
                         '/'.join(get(c) for c in ['wins_vs_seasonal_naive','ties_vs_seasonal_naive','losses_vs_seasonal_naive']),
                         get('mean_pinball_090','bed'),get('coverage_090','pct')+' ('+get('covered_090')+'/'+get('months')+')',
                         get('mean_value_of_protection','m')])
        return table(['Method','Mean excess over hindsight (m)','Joint-best origins','Win/tie/loss vs Seasonal naive',
                      'Mean pinball loss at 0.9 (beds)','0.9 coverage','Mean value of protection (m)'],rows)
    origins=cfg['origins']; n=len(origins)
    excluded=[v for v in cfg['origin_records'] if v['status']=='excluded']
    small=[v for v in excluded if v['bank_paths']<cfg['minimum_paths']]
    convergence=cfg['planning_origin_convergence']['excluded']
    lines=['## Fair comparison across many origins','',
           'Two six-month windows were not enough to establish a stable ranking. We repeat the same fair comparison at every eligible monthly origin, using the same forecasting methods and protection rule.','',
           '### Method','',
           f'Each method uses {fmt(cfg["window"],"config.json","window")} months of training and predicts the next {fmt(cfg["horizon"],"config.json","horizon")} months. '
           f'An origin is eligible only when its training and target windows are complete and it has at least {fmt(cfg["minimum_paths"],"config.json","minimum_paths")} complete historical error paths. '
           'The models, raw paired error paths, SAA capacity model and realized-cost scoring are unchanged. '
           'Both SARIMA targets must converge; otherwise the origin is excluded for all methods before scoring. '
           'The last month with both bed observations is October 2026; later blank template rows are ignored. '
           'Every plan starts with no extra capacity and stays fixed for its whole horizon.','',
           f'Of {fmt(len(cfg["origin_records"]),"config.json","origin_records.length")} training/target candidates, '
           f'{fmt(len(excluded),"config.json","excluded.length")} were excluded: '
           f'{fmt(len(small),"config.json","insufficient_history.length")} from November 2022 through January 2024 had too few error paths, and '
           f'{fmt(len(convergence),"config.json","planning_origin_convergence.excluded.length")} failed the planning-origin convergence check below. '
           f'This leaves {fmt(n,"config.json","origins.length")} origins.','',
           table(['Excluded planning origin','Nonconvergent target','Treatment'],
                 [[e['origin'],', '.join(e['failed_targets']),'Excluded for all five methods'] for e in convergence]),'',
           'Retained origins and their error-bank sizes (paths per method):','',
           table(['Year','Origin month (retained paths)'],[[year,', '.join(pd.Timestamp(r['origin']).strftime('%b')+' ('+fmt(r['bank_paths'],'config.json',r['origin']+'/bank_paths')+')'
                      for r in cfg['origin_records'] if r['status']=='included' and r['origin'].startswith(year))] for year in sorted({o[:4] for o in origins})]),'',
           'Historical backtests at January 2023 (available beds) and October 2025 (occupied beds) are also dropped for every method, under the original rule. '
           'The [complete origin inventory]('+PREFIX+'origin_inventory.csv) includes all exclusions.','',
           'The primary comparison uses Protected plans at a shortfall penalty of 5,000. Point plans at 5,000 provide the reference for the value of protection. '
           'Protected plans and their own hindsight benchmarks are also computed at 2,500 and 10,000; 10,000 is indicative because the historical tail samples are small. '
           'To save computation, this extension omits the original bias-corrected, centered-error, service-level and 1,000-penalty variants, and Point-plan penalty sensitivities.','',
           'The rules were registered before new scoring:','',
           '- At each origin, Protected plans within 1% of that origin’s hindsight cost of the cheapest plan are joint best.',
           '- Rank methods by mean excess over hindsight, giving each origin equal weight.',
           '- For each reporting scope, sort retained origins and take every sixth origin from each of six starting offsets. Members within a subset have non-overlapping target windows; the subsets overlap each other.',
           '- A clear winner must have the lowest mean excess, the lowest subset mean in at least four of six subsets, and more wins than losses against the overall runner-up. Pairwise ties use the same 1% band.',
           '- Call the result robust only if the same clear winner or top method holds at all three penalties.',
           '- Apply the rules again after excluding the two original origins, and separately before and from January 2025.','',
           'Config SHA-256: `'+cc.sha256(out/'config.json')+'`.',
           'Frozen plans SHA-256: `'+cc.sha256(out/'plans.csv')+'`.','',
           'The earlier `1d93530a...` registration was superseded before scoring to add the authorized planning-origin convergence rule. '
           'The approved blank-row correction and registration history are recorded in [verification.md]('+PREFIX+'verification.md). '
           'The rules were fixed before this run was scored, but the wider research is not blind: earlier results and some outcome months had already been inspected.','',
           '### Results','',result_table('all'),'',
           'Costs are millions of illustrative model units per six-month plan. Lower excess and pinball loss are better. '
           'Positive value of protection means the Point plan cost more than its matching Protected plan. '
           'Joint-best counts can sum to more than the number of origins. Seasonal naive compared with itself is always a tie. '
           'Coverage counts are origin–horizon cases, not distinct independent months.','',
           '![Excess over hindsight by origin](figures/fair_comparison_rolling/F1_excess_by_origin.png)','',
           '![Mean excess and range across six offset subsets](figures/fair_comparison_rolling/F2_mean_and_subset_range.png)','',
           'Error bars show the range of the six subset means. They are descriptive ranges, not confidence intervals.','',
           '![Coverage of the 0.9 quantile by horizon](figures/fair_comparison_rolling/F3_coverage_by_horizon.png)','',
           'Coverage above 90% indicates that the upper forecast met or exceeded the actual gap more often than targeted. '
           'It does not prove that every capacity margin was unnecessary, or that the SAA plan equals the monthly quantile.','',
           '### Results outside the original two origins and by period','']
    for scope in ['excluding_fable','through_2024','from_2025']:
        decision=decisions['scopes'][scope]['penalties']['5000']; sample=cfg['scopes'][scope]
        lines += ['#### '+LABELS[scope]+' — '+fmt(len(sample),'config.json',scope+'/origins.length')+' origins','',result_table(scope),'']
        w=decision['wins_ties_losses_vs_runner']
        lines += [f"Top method: **{decision['top']}**; runner-up: **{decision['runner_up']}**. "
                  f"Lowest subset mean in {fmt(decision['subset_wins'][decision['top']],'decisions.json',scope+'/primary/subset_wins')} of six subsets; "
                  'wins/ties/losses against the runner-up: '+ '/'.join(fmt(w[k],'decisions.json',scope+'/primary/'+k) for k in ['wins','ties','losses'])+'. '
                  f"Registered outcome: **{decision['clear_winner'] or 'no clear winner'}**.",'']
    lines += ['<details>','<summary>Coverage by horizon for every reporting scope</summary>','']
    for scope in cfg['scopes']:
        rows=[]
        for m in METHODS:
            g=cover[(cover.scope==scope)&(cover.method==m)].sort_values('horizon')
            rows.append([m]+[fmt(row.coverage,'coverage_by_horizon.csv',f'{scope}/{m}/{row.horizon}/coverage','pct') for row in g.itertuples()])
        lines += ['**'+LABELS[scope]+'**','',table(['Method']+[f'H{h}' for h in range(1,7)],rows),'']
    lines += ['</details>','',
              'Full [subset means]('+PREFIX+'subset_means.csv), [per-origin ranks]('+PREFIX+'per_origin_ranks.csv), '
              '[pairwise wins and cost differences]('+PREFIX+'pairwise.csv), [coverage by horizon]('+PREFIX+'coverage_by_horizon.csv), '
              'and [all summaries]('+PREFIX+'summary.csv) cover every reporting scope and penalty.','',
              '### Robustness','']
    robustness=[]
    for scope in cfg['scopes']:
        for p in cfg['plans']['protected_penalties']:
            d=decisions['scopes'][scope]['penalties'][str(int(p))]
            robustness.append([LABELS[scope],f'{int(p):,}'+(' (indicative)' if p==10000 else ''),d['top'],d['clear_winner'] or 'No clear winner'])
    lines += [table(['Scope','Shortfall penalty','Top method','Clear winner'],robustness),'',
              'Across all origins, the top method changes at the indicative high penalty, so the primary result is **not robust across all penalties**. '
              'For origins from January 2025, Seasonal naive remains top at all penalties, but the clear-winner condition is not met at 10,000.','',
              '### Answer','']
    d=decisions['scopes']['all']['penalties']['5000'];w=d['wins_ties_losses_vs_runner']
    sn=primary.loc['Seasonal naive'];et=primary.loc['ExtraTrees-24']
    answer=(f"Seasonal naive is the clear winner under the registered primary rule: mean excess {fmt(sn.mean_excess_over_hindsight,'summary.csv','all/Seasonal naive/mean_excess','m')}m versus "
            f"{fmt(et.mean_excess_over_hindsight,'summary.csv','all/ExtraTrees-24/mean_excess','m')}m for ExtraTrees-24, lowest mean in "
            f"{fmt(d['subset_wins']['Seasonal naive'],'decisions.json','all/primary/subset_wins')} of six subsets, and "
            f"{fmt(w['wins'],'decisions.json','all/primary/wins')} wins, {fmt(w['ties'],'decisions.json','all/primary/ties')} ties and {fmt(w['losses'],'decisions.json','all/primary/losses')} losses against that runner-up. "
            'The original Seasonal-naive-first ranking holds after removing the two original origins, but the clear-winner label does not; it holds in the later-period split and not in the earlier split. '
            'The ranking is not robust across penalties, because ExtraTrees-24 is top at the indicative 10,000 penalty, with no clear winner. '
            'The upper forecasts generally over-cover overall, but Seasonal naive under-covers at H5 and H6, Persistence at H1, and SARIMA-24 at H5, so calibration is uneven by horizon. '
            'Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).')
    lines += [answer,'']
    section='\n'.join(lines)
    cc.write_text(out/'readme_section.md',section)
    write_json(out/'readme_numeric_audit.json',audit)
    # Data snapshots and checksums make every plot and table independently reviewable.
    write_json(out/'report_source_hashes.json',{name:cc.sha256(out/name) for name in
               ['config.json','plans.csv','summary.csv','decisions.json','coverage_by_horizon.csv','subset_means.csv','per_origin_ranks.csv','pairwise.csv']})
    plots(out,figdir)
    print('Generated README section and three PNG/SVG figure pairs',flush=True)


if __name__=='__main__':
    build()
