"""Publication built only from saved optimisation_value outputs."""
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
from value import OUT,REPO,METHODS,cc,read,jsave

FIG=REPO/'figures/optimisation_value'
COLORS={'Seasonal naive':'#E69F00','SARIMA-24':'#0072B2','ExtraTrees-24':'#009E73','Mean-24':'#CC79A7','Persistence':'#D55E00'}
STYLES={'Seasonal naive':'-','SARIMA-24':'--','ExtraTrees-24':'-.','Mean-24':':','Persistence':(0,(5,2,1,2))}
MARKERS={'Seasonal naive':'o','SARIMA-24':'s','ExtraTrees-24':'^','Mean-24':'D','Persistence':'v'}
DISPLAY=['Mean-24','Persistence','Seasonal naive','SARIMA-24','ExtraTrees-24']
LINK='outputs/optimisation_value/'


def money(x):
    rounded=round(float(x)/1e6,2)
    return f'{0. if rounded==0 else rounded:.2f}'


def pct(x):
    rounded=round(float(x)*100,1)
    return f'{0. if rounded==0 else rounded:.1f}%'


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,r))+' |' for r in rows])


def plots():
    cfg=json.loads((OUT/'config.json').read_text());summary=read(OUT/'summary.csv');costs=read(OUT/'costs.csv')
    main=summary[summary.scope=='all'].set_index('method')
    FIG.mkdir(parents=True,exist_ok=True);files=[]
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                         'svg.fonttype':'none','savefig.facecolor':'white'})
    def save(fig,name):
        for ext in ['png','svg']:
            path=FIG/f'{name}.{ext}';fig.savefig(path,dpi=300,bbox_inches='tight')
            if ext=='svg':cc.write_text(path,'\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines())+'\n')
            files.append(dict(file=str(path.relative_to(REPO)).replace('\\','/'),sha256=cc.sha256(path)))
        plt.close(fig)
    cc.write_csv(OUT/'chain_figure_data.csv',main.loc[DISPLAY].reset_index()[['method','mean_Rule','mean_LP','mean_Protected','mean_Hindsight']])
    fig,axes=plt.subplots(1,5,figsize=(15,4.5),sharey=True,layout='constrained')
    for ax,method in zip(axes,DISPLAY):
        row=main.loc[method];ys=[row['mean_'+p]/1e6 for p in ['Rule','LP','Protected']]
        ax.step([0,1,2],ys,where='mid',color=COLORS[method],linestyle=STYLES[method],linewidth=2)
        ax.plot([0,1,2],ys,linestyle='none',marker=MARKERS[method],markersize=6,color=COLORS[method])
        ax.axhline(row.mean_Hindsight/1e6,color='#444444',linestyle='--',linewidth=1.2)
        for x,y in enumerate(ys):ax.annotate(f'{y:.2f}',(x,y),xytext=(0,8),textcoords='offset points',ha='center',fontsize=9)
        ax.set(title=method,xticks=[0,1,2],xticklabels=['Rule','LP','Protected'],xlim=(-.25,2.25),ylim=(0,32))
        ax.grid(axis='y',alpha=.2)
    axes[0].set_ylabel('Mean realized cost (million model units)')
    fig.suptitle('What each planning step changes',fontsize=16,fontweight='bold',y=1.09)
    fig.legend(handles=[Line2D([0],[0],color='#444444',linestyle='--',label=f'Hindsight: {money(main.iloc[0].mean_Hindsight)}m')],
               loc='lower center',bbox_to_anchor=(.5,-.09),frameon=False)
    save(fig,'chain_by_method')
    cc.write_csv(OUT/'optimisation_by_origin_figure_data.csv',costs[['origin','method','value_optimisation','optimisation_band']])
    fig,ax=plt.subplots(figsize=(10.2,5.0),layout='constrained')
    for y,method in enumerate(DISPLAY):
        g=costs[costs.method==method].sort_values('origin')
        offset=np.linspace(-.22,.22,len(g))
        ax.scatter(g.value_optimisation/1e6,y+offset,marker=MARKERS[method],s=42,facecolors='none',edgecolors=COLORS[method],linewidths=1.2)
    ax.axvline(0,color='#444444',linestyle='--',linewidth=1.2)
    ax.set(yticks=range(5),yticklabels=DISPLAY,xlabel='Rule cost minus LP cost (million model units)',title='Optimisation value at every retained origin')
    ax.invert_yaxis();ax.grid(axis='x',alpha=.2)
    fig.suptitle(f'{len(cfg["origins"])} markers per method; vertical spacing separates origins, not values',fontsize=10,y=1.03)
    save(fig,'value_of_optimisation_by_origin')
    jsave(OUT/'figure_inventory.json',files)


def build():
    checks=json.loads((OUT/'verification_checks.json').read_text());assert all(c['status']=='PASS' for c in checks)
    assert any(c['check']=='Existing and new pytest suites' for c in checks)
    cfg=json.loads((OUT/'config.json').read_text());summary=read(OUT/'summary.csv');diag=read(OUT/'diagnostics.csv')
    expectations=json.loads((OUT/'expectations.json').read_text());ranking=json.loads((OUT/'combination_ranking.json').read_text())
    primary=summary[summary.scope=='all'].set_index('method');pooled=primary.loc['Pooled'];best=ranking['all'][0]
    def main_table(scope):
        rows=[]
        g=summary[summary.scope==scope].set_index('method')
        for method in DISPLAY+['Pooled']:
            r=g.loc[method]
            rows.append([method,*[money(r['mean_'+p]) for p in ['Rule','LP','Protected','Hindsight']],
                         f'{money(r.optimisation_mean)} ({int(r.lp_cheaper)}/{int(r.lp_equal)}/{int(r.lp_dearer)})',
                         f'{money(r.protection_mean)} ({int(r.protection_helped)}/{int(r.protection_tie)}/{int(r.protection_hurt)})',
                         f'{pct(r.optimisation_share)} / {pct(r.protection_share)}'])
        return table(['Method','Rule','LP','Protected','Hindsight','Optimisation value (C/E/D)','Protection value (help/tie/hurt)','Gap closed: optimisation / protection'],rows)
    def why_table(scope):
        rows=[]
        g=diag[diag.scope==scope].set_index(['method','plan'])
        for method in DISPLAY+['Pooled']:
            a,b=g.loc[(method,'Rule')],g.loc[(method,'LP')]
            rows.append([method,*[money(a[c])+' -> '+money(b[c]) for c in ['mean_opening_cost','mean_closing_cost','mean_running_cost']],
                         f'{a.mean_close_then_reopen:.2f} -> {b.mean_close_then_reopen:.2f}',
                         f'{a.mean_shortfall_bed_months:.1f} -> {b.mean_shortfall_bed_months:.1f}'])
        return table(['Method','Opening cost','Closing cost','Running cost','Close-then-reopen events','Shortfall bed-months'],rows)
    lines=['## Does optimisation help? Forecast alone vs optimisation vs protection','',
      'Does turning a forecast into an optimised capacity plan reduce the cost once real demand is known? '
      'We separate the value of optimisation from the additional value of protection against forecast errors. '
      'Earlier evidence compared forecast-following and optimisation at only two origins; this extends that comparison to the full retained set.','',
      '### Method','',
      f'We reuse the saved forecasts at all {len(cfg["origins"])} origins, with no model refitting. '
      '**Rule** follows the forecast gap, subject to the capacity and monthly ramp limits. '
      '**LP** chooses the cheapest schedule for that same point forecast. '
      '**Protected** optimises across that method’s saved historical-error scenarios. '
      'The LP, Protected and hindsight plans and scores are reused unchanged at a shortfall penalty of 5,000. '
      'Every plan starts from zero additional capacity and stays fixed for six months; hindsight knows the actual gaps.','',
      'Config SHA-256: `'+cc.sha256(OUT/'config.json')+'`.',
      'Rule-plan SHA-256: `'+cc.sha256(OUT/'rule_plans.csv')+'`.','',
      'All new Rule plans were written and hashed before full-run scoring. Phase 0 first reproduced the two already evaluated anchors. '
      'The expectations and metrics were fixed before new scoring, but this is not a blind experiment: the forecasts and other plans had already been evaluated.','',
      '### Mean costs and value added','',main_table('all'),'',
      'All costs and benefits are **millions of illustrative model units per six-month plan**. '
      'Optimisation value is Rule minus LP; C/E/D counts origins where LP is cheaper/equal/dearer, using a relative tolerance of 1e-6 against Rule cost. '
      'Protection value is LP minus Protected; help/tie/hurt uses 1% of that origin’s hindsight cost as the tie band. '
      'Gap-closed shares divide each summed benefit by the summed Rule-minus-hindsight gap; they are not averages of individual percentages. '
      'Pooled gives equal weight to each method-origin case and repeats each origin’s hindsight once per method. '
      'Tiny negative optimisation values for the two flat methods are solver rounding; rounded zeros above do not change the saved raw values.','',
      '![Mean cost from forecast alone to optimisation and protection](figures/optimisation_value/chain_by_method.png)','',
      '![Value of optimisation at each origin, grouped by method](figures/optimisation_value/value_of_optimisation_by_origin.png)','',
      'Positive values mean LP was cheaper. Each marker is one origin; small vertical offsets only separate overlapping markers.','',
      '### Why the LP helps','',
      'The table shows **Rule -> LP**. Opening, closing and running costs are mean millions per plan; the other columns are mean counts per plan. '
      'A close-then-reopen event counts a removal month once when an addition follows within the next two months.','',why_table('all'),'',
      'For the three changing forecasts, LP makes fewer close-then-reopen moves and spends less on opening and closing. '
      'It keeps more capacity running through dips, which raises running cost but also lowers realised shortfalls in these trials. '
      'The flat forecasts give the same schedules within solver precision, so there is no material optimisation benefit to add. '
      'This is observed performance, not a guarantee: LP minimises forecast cost and could be worse on different realised outcomes.','',
      f'Protection helped in **{int(pooled.protection_helped)}** cases and hurt in **{int(pooled.protection_hurt)}** cases, yet its pooled mean benefit was **{money(pooled.protection_mean)}m**. '
      f'Its median benefit was **{money(pooled.protection_median)}m**. Larger gains in some windows outweighed more frequent losses; a positive average does not mean protection helped every method at every origin.','',
      '### Distribution and robustness','',
      'Medians and extremes show how uneven the benefits were. Each entry below is median [minimum, maximum], in millions of model units.','',
      table(['Method','Optimisation value','Protection value','Total Rule-minus-Protected value'],
        [[m]+[f'{money(primary.loc[m,k+"_median"])} [{money(primary.loc[m,k+"_min"])}, {money(primary.loc[m,k+"_max"])}]'
              for k in ['optimisation','protection','total_value']] for m in DISPLAY+['Pooled']]),'',
      'Each range below is the smallest to largest mean across the six offset subsets. Within a subset, origins have non-overlapping target windows; subsets overlap each other. These are descriptive ranges, not confidence intervals.','',
      table(['Method','All origins: optimisation','All origins: protection','Excluding original two: optimisation','Excluding original two: protection'],
        [[m]+[f'{money(summary[(summary.scope==scope)&(summary.method==m)].iloc[0][metric+"_subset_min"])} to '
               f'{money(summary[(summary.scope==scope)&(summary.method==m)].iloc[0][metric+"_subset_max"])}'
               for scope in ['all','excluding_original_two'] for metric in ['optimisation','protection']] for m in DISPLAY+['Pooled']]),'',
      f'#### Excluding September 2025 and March 2026: {len(cfg["scopes"]["excluding_original_two"])} origins','',main_table('excluding_original_two'),'',
      '<details>','<summary>Rule versus LP diagnostics without the original two origins</summary>','',why_table('excluding_original_two'),'','</details>','',
      'The [full metric table]('+LINK+'summary.csv) includes every mean, median, minimum, maximum and gap-closed share for both scopes. '
      '[Subset results]('+LINK+'subset_metrics.csv), [per-origin costs]('+LINK+'costs.csv), '
      '[diagnostics]('+LINK+'diagnostics_by_origin.csv) and [verification]('+LINK+'verification.md) provide the supporting detail.','',
      '### Pre-stated expectations','',
      '- **E1 held to solver precision:** Rule and LP agree for Mean-24 and Persistence at every origin; the largest capacity difference is below 0.001 beds.',
      f'- **E2 held in this sample:** LP was cheaper in {int(pooled.lp_cheaper)} cases, equal within tolerance in {int(pooled.lp_equal)}, and materially dearer in {int(pooled.lp_dearer)}.',
      '- **E3 held:** mean optimisation value was positive for Seasonal naive, SARIMA-24 and ExtraTrees-24.','',
      '### Answer','',
      f'Optimisation lowered mean cost by {money(primary.loc["Seasonal naive","optimisation_mean"])}m for Seasonal naive, '
      f'{money(primary.loc["SARIMA-24","optimisation_mean"])}m for SARIMA-24 and {money(primary.loc["ExtraTrees-24","optimisation_mean"])}m for ExtraTrees-24, '
      'with no material benefit for the flat Mean-24 and Persistence forecasts. '
      'The LP reduced opening and closing costs and realised shortfalls by keeping capacity through dips, despite higher running costs. '
      f'Protection added a further {money(pooled.protection_mean)}m per method-origin case on average; pooled over all cases, optimisation closed '
      f'{pct(pooled.optimisation_share)} of the Rule-to-hindsight gap, protection closed {pct(pooled.protection_share)}, and {pct(pooled.remaining_share)} remained. '
      f'The cheapest observed combination was {best["method"]} + {best["plan"]}, at {money(best["mean_cost"])}m per plan, though protection hurt more cases than it helped. '
      'Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).','']
    cc.write_text(OUT/'readme_section.md','\n'.join(lines))
    jsave(OUT/'report_source_hashes.json',{name:cc.sha256(OUT/name) for name in ['config.json','rule_plans.csv','summary.csv','diagnostics.csv','subset_metrics.csv','expectations.json','combination_ranking.json']})
    plots()
    print('README section and two PNG/SVG figure pairs prepared',flush=True)


if __name__=='__main__':build()
