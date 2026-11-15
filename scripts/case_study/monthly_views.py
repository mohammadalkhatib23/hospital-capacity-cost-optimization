"""Add monthly views to the two-origin case study, using saved outputs only.

No fitting, optimisation, plan generation or score-file writes. Monthly cost
components are arithmetic on saved schedules and must match saved scores.
Only README.md and the eight new *monthly* PNG/SVG files are written.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
from PIL import Image

from build import load_hindsight

ROOT = Path(__file__).resolve().parents[2]
FIG = ROOT / 'figures/case_study'
METHODS = ['Mean-24', 'Persistence', 'Seasonal naive', 'SARIMA-24', 'ExtraTrees-24']
COLORS = dict(zip(METHODS, ['#CC79A7', '#D55E00', '#E69F00', '#0072B2', '#009E73']))
ORIGINS = [('2025-09', 'O1_sep2025', 'sep2025', 'September 2025'),
           ('2026-03', 'O2_mar2026', 'mar2026', 'March 2026')]
FAIR, CLEAN, VALUE = [f'outputs/{name}/' for name in ['fair_comparison', 'clean_checkpoint', 'optimisation_value']]
SOURCES, FIGURES = {}, []
ANSWER = ("At both origins, optimisation never materially increased cost compared with following the forecast, and it saved up to 3.48m, mainly by keeping beds open through forecast dips. "
          "The cheapest plan was SARIMA-24 with optimisation in September 2025 (16.11m, close to Seasonal naive with optimisation at 16.14m) and Seasonal naive with protection in March 2026 (13.56m). "
          "Protection helped the methods that under-forecast in each window and added cost for those whose forecasts ran high; because the methods switched direction between the two windows, protection's effect flipped for every method. "
          "These are two six-month windows, so they show how the methods behaved, not a general ranking.")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    SOURCES[path] = sha(ROOT / path)
    return pd.read_csv(ROOT / path, float_precision='round_trip')


def close(a, b, rtol=1e-6, atol=1e-6):
    np.testing.assert_allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), rtol=rtol, atol=atol)


def verify_hashes():
    count = 0
    for base in [FAIR, CLEAN, VALUE]:
        for manifest in (ROOT / base).glob('*.sha256'):
            for line in manifest.read_text().splitlines():
                expected, name = line.split(maxsplit=1)
                assert sha(manifest.parent / name.lstrip('*')) == expected, (manifest, name)
                count += 1
    cfg = json.loads((ROOT / FAIR / 'config.json').read_text(encoding='utf-8'))
    assert {k: cfg['costs'][k] for k in ['running', 'opening', 'closing', 'shortfall_base']} == {
        'running': 500, 'opening': 550, 'closing': 300, 'shortfall_base': 5000}
    print(f'PASS: {count} saved hash-manifest entries', flush=True)
    return cfg


def load_cases(cfg):
    frozen = read(FAIR + 'plans_frozen.csv')
    rules = read(VALUE + 'rule_plans.csv')
    cases = []
    for origin, key, stem, title in ORIGINS:
        base = FAIR + key + '/'
        actual = read(base + 'actuals.csv').sort_values('month').reset_index(drop=True)
        forecasts = read(base + 'forecasts.csv')
        accuracy = read(base + 'accuracy.csv').set_index('method')
        totals = read(base + 'totals.csv')
        totals = totals[(totals.penalty == 5000) & totals.kind.isin(['point', 'protected', 'hindsight'])]
        scored = read(base + 'plans.csv')
        costs = read(VALUE + origin + '/costs.csv').set_index('method')
        monthly = read(VALUE + origin + '/monthly_costs.csv')
        clean_totals = read(CLEAN + key + '/totals.csv').set_index('plan')
        clean_actual = read(CLEAN + key + '/actuals.csv').sort_values('month')
        close(actual[['occupied', 'available', 'gap']], clean_actual[['occupied', 'available', 'gap']])
        close(actual.gap, actual.occupied / .9 - actual.available, rtol=0, atol=1e-8)
        dates = pd.date_range(pd.Timestamp(origin) + pd.DateOffset(months=1), periods=6, freq='MS')
        assert list(actual.month) == list(dates.strftime('%Y-%m-%d'))
        assert cfg['origins'][key]['forecast_months'] == list(dates.strftime('%Y-%m'))
        hindsight, hindsight_total = load_hindsight(key, reader=read)
        c = dict(origin=origin, key=key, stem=stem, title=title, actual=actual,
                 forecasts=forecasts, costs=costs, schedules={}, accuracy=accuracy)
        for method in METHODS + ['Hindsight']:
            for plan in (['Hindsight'] if method == 'Hindsight' else ['Rule', 'LP', 'Protected']):
                if plan == 'Hindsight':
                    schedule = hindsight.copy()
                    saved_monthly = hindsight
                    total = float(hindsight_total.total)
                elif plan == 'Rule':
                    schedule = rules[(rules.origin == origin) & (rules.method == method)].sort_values('month').reset_index(drop=True)
                    saved_monthly = monthly[(monthly.method == method) & (monthly.plan == plan)].sort_values('month')
                    total = float(costs.loc[method, plan])
                    close(total, clean_totals.loc[method + ' Rule', 'total'], rtol=1e-6, atol=0)
                else:
                    kind = 'point' if plan == 'LP' else 'protected'
                    row = totals[(totals.method == method) & (totals.kind == kind)].iloc[0]
                    schedule = frozen[(frozen.origin == key) & (frozen.method == method) &
                                      (frozen.kind == kind) & (frozen.penalty == 5000)].sort_values('month').reset_index(drop=True)
                    saved_monthly = scored[scored.plan == row.plan].sort_values('month')
                    total = float(row.total)
                    close(total, costs.loc[method, plan])
                assert list(schedule.month) == list(actual.month) == list(saved_monthly.month)
                x = schedule.x.to_numpy()
                step = np.diff(np.r_[0., x])
                a, r = np.maximum(step, 0), np.maximum(-step, 0)
                short, spare = np.maximum(actual.gap.to_numpy() - x, 0), np.maximum(x - actual.gap.to_numpy(), 0)
                operating = 500 * x + 550 * a + 300 * r
                realised = operating + 5000 * short
                close(schedule[['x', 'a', 'r']], saved_monthly[['x', 'a', 'r']])
                close(a, schedule.a); close(r, schedule.r)
                close(realised, saved_monthly.monthly_cost)
                close(short, saved_monthly.shortfall); close(spare, saved_monthly.spare)
                close(realised.sum(), total, rtol=1e-6, atol=0)
                assert float(hindsight_total.total) <= total + 1e-6 * abs(total)
                g = pd.DataFrame(dict(month=actual.month, x=x, a=a, r=r, shortfall=short, spare=spare,
                                      operating=operating, shortfall_cost=5000 * short, monthly_cost=realised))
                c['schedules'][method, plan] = g
            if method != 'Hindsight':
                f = forecasts[forecasts.method == method].sort_values('month')
                assert list(f.month) == list(actual.month)
                error = actual.gap.to_numpy() - f.forecast_gap.to_numpy()
                close([np.abs(error).mean(), error.mean()], accuracy.loc[method, ['gap_mae', 'gap_mean_signed_error']])
                if method in ['Mean-24', 'Persistence']:
                    close(c['schedules'][method, 'Rule'].x, c['schedules'][method, 'LP'].x, rtol=0, atol=.001)
        cases.append(c)
    # Gate the exact requested Answer before any README or figure is written.
    september, march = cases
    claims = [(march['costs'].loc['Seasonal naive', 'Rule'] - march['costs'].loc['Seasonal naive', 'LP'], '3.48'),
              (september['costs'].loc['SARIMA-24', 'LP'], '16.11'),
              (september['costs'].loc['Seasonal naive', 'LP'], '16.14'),
              (march['costs'].loc['Seasonal naive', 'Protected'], '13.56')]
    for value, expected in claims:
        assert f'{value / 1e6:.2f}' == expected, ('Answer value differs', value, expected)
    for c, best in zip(cases, [('SARIMA-24', 'LP'), ('Seasonal naive', 'Protected')]):
        assert min((float(c['costs'].loc[m, p]), m, p) for m in METHODS for p in ['Rule', 'LP', 'Protected'])[1:] == best
        assert (c['costs'].value_optimisation >= -c['costs'].optimisation_band).all()
        assert np.array_equal(c['accuracy'].loc[METHODS].gap_mean_signed_error > 0,
                              c['costs'].loc[METHODS].value_protection > 0)
    assert np.all(september['costs'].loc[METHODS].value_protection.to_numpy() *
                  march['costs'].loc[METHODS].value_protection.to_numpy() < 0)
    print('PASS: four Answer values; 32 monthly schedules reconcile to saved totals; Hindsight agrees across scored sources and is a lower bound', flush=True)
    return cases


def save(fig, stem):
    for extension in ['png', 'svg']:
        path = FIG / (stem + '.' + extension)
        fig.savefig(path, dpi=300, bbox_inches='tight')
        if extension == 'svg':
            path.write_bytes(('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines()) + '\n').encode('utf-8'))
        FIGURES.append(path)
    plt.close(fig)


def plot_plans(c):
    fig, axes = plt.subplots(1, 5, figsize=(16.4, 4.9), sharey=True, layout='constrained')
    months = pd.to_datetime(c['actual'].month)
    labels = [d.strftime('%b\n%Y') for d in months]
    high = max(float(g.x.max()) for g in c['schedules'].values())
    high = max(high, float(c['actual'].gap.max()), float(c['forecasts'].forecast_gap.max()))
    for ax, method in zip(axes, METHODS):
        color = COLORS[method]
        f = c['forecasts'][c['forecasts'].method == method].sort_values('month')
        ax.plot(range(6), f.forecast_gap, color=color, linestyle='--', linewidth=3.4, alpha=.45, zorder=1)
        for plan, style, marker, size in [('Rule', ':', 'o', 7), ('LP', '-', 's', 3.5), ('Protected', '-.', '^', 6)]:
            g = c['schedules'][method, plan]
            ax.plot(range(6), g.x, color=color, linestyle=style, linewidth=1.6, marker=marker, markersize=size,
                    markerfacecolor=color if plan == 'Protected' else 'white', markeredgewidth=1.4, zorder=3)
        ax.plot(range(6), c['actual'].gap, color='black', linewidth=2.6, zorder=4)
        ax.set(title=method, xticks=range(6), xticklabels=labels, ylim=(0, high * 1.10), xlim=(-.2, 5.2))
        ax.tick_params(axis='x', labelsize=8)
        ax.grid(axis='y', alpha=.18)
    axes[0].set_ylabel('Beds')
    fig.suptitle(c['title'] + ': forecast, actual gap and frozen capacity plans', fontsize=15)
    handles = [Line2D([0], [0], color='black', linewidth=2.6, label='Actual gap'),
               Line2D([0], [0], color='#666666', linestyle='--', linewidth=3, alpha=.65, label='Forecast gap'),
               Line2D([0], [0], color='#666666', linestyle=':', marker='o', markerfacecolor='white', markersize=7, label='Rule capacity'),
               Line2D([0], [0], color='#666666', linestyle='-', marker='s', markerfacecolor='white', markersize=4, label='LP capacity'),
               Line2D([0], [0], color='#666666', linestyle='-.', marker='^', label='Protected capacity')]
    fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(.5, -.11), ncol=5, frameon=False)
    save(fig, c['stem'] + '_monthly_plans')


def plot_heatmap(c):
    keys = [(m, p) for m in METHODS for p in ['Rule', 'LP', 'Protected']] + [('Hindsight', 'Hindsight')]
    groups = [c['schedules'][key] for key in keys]
    values = np.array([g.monthly_cost.to_numpy() for g in groups]) / 1e6
    totals = values.sum(axis=1).reshape(-1, 1)
    labels = [d.strftime('%b\n%Y') for d in pd.to_datetime(c['actual'].month)]
    fig, (ax, total_ax) = plt.subplots(1, 2, figsize=(14.2, 11.6), gridspec_kw={'width_ratios': [6, 1.18]}, layout='constrained')
    for axis, data, is_total in [(ax, values, False), (total_ax, totals, True)]:
        norm = Normalize(vmin=0, vmax=float(data.max()))
        im = axis.imshow(data, cmap='YlOrRd', norm=norm, aspect='auto')
        for i, g in enumerate(groups):
            for j in range(data.shape[1]):
                short = float(g.shortfall.sum()) if is_total else float(g.shortfall.iloc[j])
                spare = float(g.spare.sum()) if is_total else float(g.spare.iloc[j])
                note = f'short {short:,.0f}' if round(short) > 0 else f'spare {spare:,.0f}'
                axis.text(j, i, f'{data[i, j]:.2f}m\n{note}', ha='center', va='center', fontsize=9,
                          color='white' if norm(data[i, j]) > .68 else '#222222')
        axis.set(xticks=range(data.shape[1]), xticklabels=['Total'] if is_total else labels, yticks=range(16),
                 yticklabels=[] if is_total else [m + ' / ' + p if m != 'Hindsight' else m for m, p in keys])
        axis.tick_params(length=0, labelsize=9)
        for tick, (method, _) in zip(axis.get_yticklabels(), keys):
            tick.set_color(COLORS.get(method, 'black'))
        axis.set_xticks(np.arange(-.5, data.shape[1], 1), minor=True)
        axis.set_yticks(np.arange(-.5, 16, 1), minor=True)
        axis.grid(which='minor', color='white', linewidth=1.4)
        axis.tick_params(which='minor', length=0)
        for boundary in [2.5, 5.5, 8.5, 11.5, 14.5]:
            axis.axhline(boundary, color='#555555', linewidth=1.15)
        bar = fig.colorbar(im, ax=axis, location='bottom', shrink=.9, pad=.035)
        bar.set_label(('Total' if is_total else 'Monthly') + ' cost (million model units)', fontsize=9)
    fig.suptitle(c['title'] + ': monthly cost of every saved plan\nComputed from saved plans and actuals; Total uses its own colour scale', fontsize=14)
    save(fig, c['stem'] + '_monthly_cost_all_plans')


def monthly_section(c):
    def image(stem, alt):
        return f'![{alt}](figures/case_study/{stem}.png)\n\n[SVG](figures/case_study/{stem}.svg)'
    forecast = c['forecasts'].pivot(index='month', columns='method', values='forecast_gap').loc[c['actual'].month]
    rows = []
    for i, date in enumerate(pd.to_datetime(c['actual'].month)):
        row = [date.strftime('%b %Y')] + [f'{v:,.0f}' for v in [c['actual'].gap.iloc[i], *forecast.iloc[i][METHODS]]]
        assert [float(v.replace(',', '')) for v in row[1:]] == [round(v) for v in [c['actual'].gap.iloc[i], *forecast.iloc[i][METHODS]]]
        rows.append('| ' + ' | '.join(row) + ' |')
    table = '\n'.join(['| Month | Actual gap | ' + ' | '.join(METHODS) + ' |', '| ' + ' | '.join(['---'] * 7) + ' |', *rows])
    if c['origin'] == '2025-09':
        for m in ['Mean-24', 'Persistence']:
            for p in ['Rule', 'LP']:
                assert set(c['schedules'][m, p].monthly_cost.nlargest(2).index) == {3, 4}
        assert all((c['schedules'][m, 'Protected'].x.to_numpy() > c['actual'].gap.to_numpy()).all() for m in METHODS)
        narrative = ('January and February drive the largest bills for Mean-24 and Persistence under Rule and LP, while October includes the opening costs. '
                     'In December, LP keeps Seasonal naive and SARIMA-24 capacity open through the dip; Protected keeps every method above the actual gap throughout this window.')
    else:
        assert all(c['schedules']['Seasonal naive', p].monthly_cost.idxmax() == 3 for p in ['Rule', 'LP'])
        for m in ['Mean-24', 'Persistence', 'ExtraTrees-24']:
            assert (c['schedules'][m, 'Protected'].x.to_numpy() > c['actual'].gap.to_numpy()).all()
        for m in ['Seasonal naive', 'SARIMA-24']:
            assert c['schedules'][m, 'Protected'].shortfall.sum() < c['schedules'][m, 'LP'].shortfall.sum()
        narrative = ('Opening costs raise April\'s bills, while Seasonal naive\'s Rule and LP bills peak in July. '
                     'LP keeps Seasonal naive and SARIMA-24 capacity open through August; Protected reduces their shortfalls while leaving Mean-24, Persistence and ExtraTrees-24 above the actual gap throughout the window.')
    dip = 2 if c['origin'] == '2025-09' else 4
    for m in ['Seasonal naive', 'SARIMA-24']:
        assert c['schedules'][m, 'Rule'].r.iloc[dip] > 0
        close(c['schedules'][m, 'LP'].x.iloc[dip], c['schedules'][m, 'LP'].x.iloc[dip - 1], rtol=0)
    return '\n\n'.join(['### Month by month',
        image(c['stem'] + '_monthly_plans', c['title'] + ': actual gap, forecast and all capacity plans'),
        'Capacity below the actual line is short; capacity above it is spare. Overlapping lines show equal values.',
        image(c['stem'] + '_monthly_cost_all_plans', c['title'] + ': monthly realised cost of all plans'),
        '**Computed from saved plans and actuals.** Costs are millions of model units; short/spare values are rounded beds. '
        'In Total, short is summed shortfall bed-months; if that rounds to zero, spare is summed spare bed-months. '
        'Total has its own colour scale, and the unrounded monthly costs sum to the saved total for every plan.',
        'Actual and forecast gaps, rounded to whole beds:', table,
        'Sources: [saved actuals](' + FAIR + c['key'] + '/actuals.csv) and [saved forecasts](' + FAIR + c['key'] + '/forecasts.csv).',
        narrative]) + '\n\n'


def section(text, heading):
    match = re.search(r'^' + re.escape(heading) + r'\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    assert match, heading
    return match.group(1).strip()


def reshape_readme(original, cases):
    # Make the transformation repeatable without touching the retained prose.
    text = re.sub(r'^### Month by month\n.*?(?=^### Why optimisation helped)', '', original, flags=re.M | re.S)
    text = re.sub(r'^## How typical are these two windows\?\n.*?(?=^## Answer)', '', text, flags=re.M | re.S)
    text = re.sub(r'(^## Answer\n\n).*?(?=\n\n## Limitations)', lambda m: m.group(1) + ANSWER, text, flags=re.M | re.S)
    limits = ('- These are two six-month windows, so the results describe these cases.\n'
              '- The models are univariate and do not use staffing, admissions, discharge or policy variables.\n'
              '- Occupied and available beds are forecast separately.\n'
              '- National monthly totals hide local bottlenecks and daily peaks.\n'
              '- Model specifications were fixed, not tuned for these windows.\n'
              '- Costs are illustrative (validated: False); capacity is not a full staffing or clinical feasibility model.\n'
              '- Results depend on the assumed shortage price and capacity limits.')
    text = re.sub(r'(^## Limitations\n\n).*?(?=\n\n<details>)', lambda m: m.group(1) + limits, text, flags=re.M | re.S)
    text = text.replace('agreement between the two-origin and rolling records', 'agreement between the saved two-origin records')
    text = re.sub(r'; \[rolling totals\]\(outputs/fair_comparison_rolling/[^)]+\)', '', text)
    text = re.sub(r'^- \*\*Broader context:\*\*.*$',
                  "- **Prior-year comparison:** Seasonal naive's [saved March forecast](outputs/fair_comparison/O2_mar2026/forecasts.csv) repeats the same calendar months of 2025.",
                  text, flags=re.M)
    text = text.replace('Rebuild the README and figures with `python -B scripts/case_study/build.py`. The script reads saved files and writes only this README and the new case-study figures.',
                        'Refresh the monthly figures and this README with `python -B scripts/case_study/monthly_views.py`. The script reads saved files; existing case-study figures are reused unchanged.')
    for index, c in enumerate(cases, 1):
        heading = f'## Origin {index}: {c["title"]}'
        start = text.index(heading)
        insertion = text.index('### Why optimisation helped', start)
        text = text[:insertion] + monthly_section(c) + text[insertion:]
        old = re.sub(r'^### Month by month\n.*?(?=^### Why optimisation helped)', '', section(original, heading), flags=re.M | re.S).strip()
        new = re.sub(r'^### Month by month\n.*?(?=^### Why optimisation helped)', '', section(text, heading), flags=re.M | re.S).strip()
        assert new == old, ('Retained origin content changed', heading)
    for heading in ['## Question', '## Method', '## The two origins side by side']:
        assert section(text, heading) == section(original, heading), heading
    assert section(text, '## Answer') == ANSWER
    assert not re.search(r'25[- ]origins?|twenty[- ]five|percentiles?|How typical|summary\.csv|fair_comparison_rolling', text, re.I)
    return text


def parse_tables(text):
    result = []
    for block in re.findall(r'^\|.*(?:\n\|.*)+', text, re.M):
        lines = block.splitlines()
        rows = [[v.strip() for v in line.strip('|').split('|')] for line in lines]
        assert all(len(row) == len(rows[0]) for row in rows), 'Malformed Markdown table'
        assert all(v == '---' for v in rows[1])
        result.append((rows[0], rows[2:]))
    return result


def fmt(value, places=2, scale=1e6, signed=False):
    value = round(float(value) / scale, places)
    if value == 0:
        value = 0.
    return format(value, ('+' if signed else '') + f',.{places}f')


def verify_readme_numbers(text, cases):
    cells = 0
    for index, c in enumerate(cases, 1):
        tables = parse_tables(section(text, f'## Origin {index}: {c["title"]}'))
        assert len(tables) == 6
        accuracy = c['accuracy']
        expected = [[m, fmt(accuracy.loc[m, 'gap_mae'], 1, 1), fmt(accuracy.loc[m, 'gap_mean_signed_error'], 1, 1, True),
                     'Too low' if accuracy.loc[m, 'gap_mean_signed_error'] > 0 else 'Too high'] for m in METHODS]
        assert tables[0][1] == expected
        best = min((float(c['costs'].loc[m, p]), m, p) for m in METHODS for p in ['Rule', 'LP', 'Protected'])[1:]
        expected = []
        for m in METHODS:
            row = [m]
            for p in ['Rule', 'LP', 'Protected', 'Hindsight']:
                g = c['schedules']['Hindsight', 'Hindsight'] if p == 'Hindsight' else c['schedules'][m, p]
                cost = fmt(g.monthly_cost.sum())
                if (m, p) == best:
                    cost = '**' + cost + '**'
                row.append(cost + '<br>(' + fmt(g.operating.sum()) + ' + ' + fmt(g.shortfall_cost.sum()) + ')')
            expected.append(row)
        assert tables[1][1] == expected
        f = c['forecasts'].pivot(index='month', columns='method', values='forecast_gap').loc[c['actual'].month]
        expected = [[pd.Timestamp(date).strftime('%b %Y'), fmt(c['actual'].gap.iloc[j], 0, 1)] +
                    [fmt(f.iloc[j][m], 0, 1) for m in METHODS] for j, date in enumerate(c['actual'].month)]
        assert tables[2][1] == expected
        for k, m in enumerate(['Seasonal naive', 'SARIMA-24'], 3):
            expected = [[pd.Timestamp(date).strftime('%b %Y')] +
                        [fmt(c['schedules'][m, p].iloc[j][col], 1, 1) for p in ['Rule', 'LP'] for col in ['x', 'a', 'r']]
                        for j, date in enumerate(c['actual'].month)]
            assert tables[k][1] == expected
            r = c['costs'].loc[m]
            assert f'It saved **{fmt(r.value_optimisation)}m**:' in section(text, f'## Origin {index}: {c["title"]}')
        expected = [[m, 'Too low' if accuracy.loc[m, 'gap_mean_signed_error'] > 0 else 'Too high',
                     fmt(c['costs'].loc[m, 'LP']), fmt(c['costs'].loc[m, 'Protected']),
                     ('Helped by ' if c['costs'].loc[m, 'value_protection'] > 0 else 'Hurt by ') + fmt(abs(c['costs'].loc[m, 'value_protection'])) + 'm']
                    for m in METHODS]
        assert tables[5][1] == expected
        cells += sum(sum(len(row) - 1 for row in body) for _, body in tables)
    side = parse_tables(section(text, '## The two origins side by side'))
    expected = []
    for m in METHODS:
        chain = [' / '.join(fmt(c['costs'].loc[m, p]) for p in ['Rule', 'LP', 'Protected', 'Hindsight']) for c in cases]
        benefits = [' / '.join(fmt(c['costs'].loc[m, 'value_' + metric], signed=True) for metric in ['optimisation', 'protection']) for c in cases]
        expected.append([m, *chain, *benefits])
    assert len(side) == 1 and side[0][1] == expected
    cells += 20
    assert len(parse_tables(text)) == 13
    assert section(text, '## Answer') == ANSWER
    print(f'PASS: all 13 retained/new tables ({cells} data cells) match saved outputs; exact Answer and retained sections checked', flush=True)


def verify_figures_and_links(text):
    assert len(FIGURES) == 8
    for path in FIGURES:
        assert 'monthly' in path.stem
        if path.suffix == '.png':
            with Image.open(path) as im:
                assert all(abs(dpi - 300) < .01 for dpi in im.info['dpi'])
        else:
            ET.parse(path)
    for path in re.findall(r'\]\(([^)]+)\)', text):
        assert (ROOT / path).is_file(), path
    for path, expected in SOURCES.items():
        assert sha(ROOT / path) == expected, path
    print('PASS: four new PNG/SVG pairs at 300 dpi; all README links resolve; source files unchanged', flush=True)


def main():
    new_figures = {f'figures/case_study/{stem}_{kind}.{ext}' for _, _, stem, _ in ORIGINS
                   for kind in ['monthly_plans', 'monthly_cost_all_plans'] for ext in ['png', 'svg']}
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().strip('\0').split('\0')
    protected = {p: sha(ROOT / p) for p in paths if p and p not in new_figures and p not in ['README.md', 'scripts/case_study/monthly_views.py']}
    original = (ROOT / 'README.md').read_text(encoding='utf-8')
    cfg = verify_hashes()
    cases = load_cases(cfg)
    document = reshape_readme(original, cases)
    verify_readme_numbers(document, cases)
    FIG.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False, 'savefig.facecolor': 'white'})
    for case in cases:
        plot_plans(case)
        plot_heatmap(case)
    verify_figures_and_links(document)
    assert all(sha(ROOT / path) == expected for path, expected in protected.items()), 'An existing file changed'
    (ROOT / 'README.md').write_bytes(document.encode('utf-8'))
    print(f'PASS: preserved {len(protected)} existing files; no 25-origin text remains in README', flush=True)


if __name__ == '__main__':
    main()
