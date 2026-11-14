"""Two-origin README, using saved results only: no fitting, solving or rescoring.

New monthly component arithmetic is explicitly labelled in the publication.
Run: python -B scripts/case_study/build.py
Only README.md and figures/case_study/*.png,*.svg are written.
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
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FIG = ROOT / 'figures/case_study'
FAIR = 'outputs/fair_comparison/'
CLEAN = 'outputs/clean_checkpoint/'
ROLL = 'outputs/fair_comparison_rolling/'
VALUE = 'outputs/optimisation_value/'
METHODS = ['Mean-24', 'Persistence', 'Seasonal naive', 'SARIMA-24', 'ExtraTrees-24']
ORIGINS = [('2025-09', 'O1_sep2025', 'sep2025', 'September 2025'),
           ('2026-03', 'O2_mar2026', 'mar2026', 'March 2026')]
COLORS = dict(zip(METHODS, ['#CC79A7', '#D55E00', '#E69F00', '#0072B2', '#009E73']))
STYLES = [':', (0, (5, 2, 1, 2)), '-', '--', '-.']
MARKERS = ['D', 'v', 'o', 's', '^']
SOURCES, NUMBERS, TABLES, IMAGES = {}, [], [], []


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(path):
    SOURCES[path] = digest(ROOT / path)
    return ROOT / path


def read(path):
    frame = pd.read_csv(source(path), float_precision='round_trip')
    if 'index' in frame and 'month' not in frame:
        frame = frame.rename(columns={'index': 'month'})
    return frame


def config(path):
    return json.loads(source(path).read_text(encoding='utf-8'))


def close(a, b, rtol=1e-6, atol=1e-6):
    np.testing.assert_allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), rtol=rtol, atol=atol)


def n(value, provenance, places=2, scale=1e6, signed=False):
    """Trace and round every numerical data cell; values retain source precision."""
    assert provenance in SOURCES, provenance
    value = float(value)
    assert np.isfinite(value)
    rounded = round(value / scale, places)
    if rounded == 0:
        rounded = 0.
    text = format(rounded, ('+' if signed else '') + f',.{places}f')
    close(float(text.replace(',', '')) * scale, value, rtol=0, atol=scale * .5 * 10**-places + 1e-7)
    NUMBERS.append((text, value, provenance))
    return text


def table(headers, rows):
    assert all(len(row) == len(headers) for row in rows)
    result = '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                       ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])
    TABLES.append(result)
    return result


def link(label, path):
    assert (ROOT / path).is_file()
    return f'[{label}]({path})'


def figure(stem, alt):
    return f'![{alt}](figures/case_study/{stem}.png)\n\n[SVG](figures/case_study/{stem}.svg)'


def verify_hashes():
    count = 0
    for base in [CLEAN, FAIR, ROLL, VALUE]:
        for manifest in (ROOT / base).glob('*.sha256'):
            for line in manifest.read_text().splitlines():
                expected, name = line.split(maxsplit=1)
                assert digest(manifest.parent / name.lstrip('*')) == expected, (manifest, name)
                count += 1
    cfg = config(VALUE + 'config.json')
    for path, expected in cfg['source_hashes'].items():
        assert digest(ROOT / path) == expected, path
    print(f'PASS: {count} existing hash-manifest entries and registered source hashes', flush=True)
    return cfg


def load_hindsight(key, reader=None):
    """Hindsight is a scored result, not a forecast-time frozen decision.

    Compare the two saved scored sources at the approved relative tolerance.
    No frozen-plan file is read by this checker.
    """
    reader = read if reader is None else reader
    saved = []
    for base in [FAIR, CLEAN]:
        plans = reader(base + key + '/plans.csv')
        totals = reader(base + key + '/totals.csv')
        selected = totals[totals.method == 'Hindsight']
        if base == FAIR:
            selected = selected[(selected.kind == 'hindsight') & (selected.penalty == 5000)]
        assert len(selected) == 1, (base, key, 'expected one scored Hindsight total')
        row = selected.iloc[0]
        schedule = plans[plans.plan == row.plan].sort_values('month').reset_index(drop=True)
        assert len(schedule) == 6 and schedule.month.is_unique
        close(schedule.monthly_cost.sum(), row.total, rtol=1e-6, atol=0)
        saved.append((schedule, row))
    fair_plan, fair_total = saved[0]
    clean_plan, clean_total = saved[1]
    assert list(fair_plan.month) == list(clean_plan.month)
    columns = ['x', 'a', 'r', 'shortfall', 'spare', 'monthly_cost']
    close(fair_plan[columns], clean_plan[columns], rtol=1e-6, atol=0)
    columns = ['operating', 'shortfall_bed_months', 'shortfall_cost', 'total']
    close(fair_total[columns], clean_total[columns], rtol=1e-6, atol=0)
    return fair_plan.copy(), fair_total


def test_hindsight_reads_scored_files():
    """A scored-only fixture works without any frozen Hindsight record."""
    import pytest
    key = 'toy_origin'
    plan = pd.DataFrame({'plan': ['Hindsight'] * 6,
                         'month': pd.date_range('2026-04-01', periods=6, freq='MS').strftime('%Y-%m-%d'),
                         'x': [10.] * 6, 'a': [10., 0., 0., 0., 0., 0.], 'r': [0.] * 6,
                         'shortfall': [0.] * 6, 'spare': [0.] * 6, 'monthly_cost': [100.] * 6})
    clean_total = pd.DataFrame([dict(plan='Hindsight', method='Hindsight', operating=600.,
                                    shortfall_bed_months=0., shortfall_cost=0., total=600.)])
    fair_plan = plan.assign(plan='Hindsight | p=5000')
    fair_total = clean_total.assign(plan='Hindsight | p=5000', kind='hindsight', penalty=5000)
    fixture = {FAIR + key + '/plans.csv': pd.concat([fair_plan, fair_plan.assign(plan='Hindsight | p=2500', x=999.)]),
               FAIR + key + '/totals.csv': pd.concat([fair_total, fair_total.assign(plan='Hindsight | p=2500', penalty=2500)]),
               CLEAN + key + '/plans.csv': plan, CLEAN + key + '/totals.csv': clean_total}
    calls = []

    def scored_only(path):
        assert path in fixture, 'Checker tried to read something other than the four scored files: ' + path
        calls.append(path)
        return fixture[path].copy()

    result, total = load_hindsight(key, reader=scored_only)
    assert set(calls) == set(fixture) and len(calls) == 4
    assert result.x.tolist() == [10.] * 6 and total.total == 600.
    # A disagreement in the scored record must still stop the checker.
    fixture[CLEAN + key + '/plans.csv'] = plan.assign(x=11.)
    with pytest.raises(AssertionError):
        load_hindsight(key, reader=scored_only)


def load_cases(cfg):
    fair_cfg = config(FAIR + 'config.json')
    assert cfg['window'] == 24 and cfg['horizon'] == 6 and len(cfg['methods']) == 5
    assert cfg['costs'] == {'running': 500, 'opening': 550, 'closing': 300, 'shortfall': 5000}
    assert cfg['limits'] == {'cap': 15000, 'ramp': 5000, 'x0': 0, 'terminal_charge': 0}
    assert '0.90' in fair_cfg['gap'] and 'actual - forecast' in fair_cfg['error_bank']['error']
    costs = read(VALUE + 'costs.csv')
    monthly = read(VALUE + 'monthly_costs.csv')
    rules = read(VALUE + 'rule_plans.csv')
    frozen = read(FAIR + 'plans_frozen.csv')
    rolling = read(ROLL + 'plans.csv')
    cases = []
    for origin, key, stem, title in ORIGINS:
        base = FAIR + key + '/'
        actual = read(base + 'actuals.csv').sort_values('month').set_index('month')
        forecasts = read(base + 'forecasts.csv')
        accuracy = read(base + 'accuracy.csv').set_index('method')
        totals = read(base + 'totals.csv')
        totals = totals[(totals.penalty == 5000) & totals.kind.isin(['point', 'protected', 'hindsight'])]
        rolling_totals = read(ROLL + origin + '/totals.csv')
        plans = read(base + 'plans.csv')
        checkpoint = read(CLEAN + key + '/totals.csv').set_index('plan')
        hindsight_plan, hindsight_total = load_hindsight(key)
        clean_fc = read(CLEAN + key + '/forecasts.csv')
        rolling_fc = read(ROLL + origin + '/forecasts.csv')
        for other in [read(CLEAN + key + '/actuals.csv'), read(ROLL + origin + '/actuals.csv')]:
            close(actual[['occupied', 'available', 'gap']], other.sort_values('month')[['occupied', 'available', 'gap']])
        expected_months = [d.strftime('%Y-%m-%d') for d in pd.date_range(pd.Timestamp(origin) + pd.DateOffset(months=1), periods=6, freq='MS')]
        assert list(actual.index) == expected_months
        assert fair_cfg['origins'][key]['cutoff'] == origin
        close(actual.gap, actual.occupied / .9 - actual.available, rtol=0, atol=1e-8)
        for other in [clean_fc, rolling_fc]:
            close(forecasts.sort_values(['method', 'month'])[['forecast_occupied', 'forecast_available', 'forecast_gap']],
                  other.sort_values(['method', 'month'])[['forecast_occupied', 'forecast_available', 'forecast_gap']], rtol=0, atol=1e-6)
        case = dict(origin=origin, key=key, stem=stem, title=title, base=base, actual=actual,
                    forecasts=forecasts, accuracy=accuracy, costs=costs[costs.origin == origin].set_index('method'),
                    monthly={}, parts={}, totals=totals)
        for method in METHODS + ['Hindsight']:
            types = ['Hindsight'] if method == 'Hindsight' else ['Rule', 'LP', 'Protected']
            for plan in types:
                if plan == 'Hindsight':
                    saved = hindsight_total
                    g = hindsight_plan.copy()
                else:
                    g = monthly[(monthly.origin == origin) & (monthly.method == method) & (monthly.plan == plan)].sort_values('month').copy()
                assert list(g.month) == expected_months
                x = g.x.to_numpy(); change = np.diff(np.r_[0., x])
                opening, closing = np.maximum(change, 0), np.maximum(-change, 0)
                short, spare = np.maximum(actual.gap.to_numpy() - x, 0), np.maximum(x - actual.gap.to_numpy(), 0)
                # Arithmetic on frozen schedules only, independently checked against saved monthly costs.
                operating = 500 * x + 550 * opening + 300 * closing
                shortage = 5000 * short
                close(g.a, opening); close(g.r, closing); close(g.shortfall, short); close(g.spare, spare)
                close(g.monthly_cost, operating + shortage)
                g['operating'] = operating; g['shortfall_cost'] = shortage
                case['monthly'][method, plan] = g
                case['parts'][method, plan] = (float(operating.sum()), float(shortage.sum()), float(g.monthly_cost.sum()))
                saved_total = case['costs'].iloc[0].Hindsight if plan == 'Hindsight' else case['costs'].loc[method, plan]
                close(g.monthly_cost.sum(), saved_total)
                if plan == 'Rule':
                    r = rules[(rules.origin == origin) & (rules.method == method)].sort_values('month')
                    close(g[['x', 'a', 'r']], r[['x', 'a', 'r']], rtol=0, atol=1e-8)
                    close(saved_total, checkpoint.loc[method + ' Rule', 'total'], rtol=1e-6)
                else:
                    kind = {'LP': 'point', 'Protected': 'protected', 'Hindsight': 'hindsight'}[plan]
                    saved = totals[(totals.method == method) & (totals.kind == kind)].iloc[0]
                    close(saved_total, saved.total)
                    roll_total = rolling_totals[(rolling_totals.method == method) & (rolling_totals.kind == kind) & (rolling_totals.penalty == 5000)].iloc[0]
                    close(saved.total, roll_total.total)
                    close(operating.sum(), saved.operating); close(shortage.sum(), saved.shortfall_cost)
                    close(g.x, plans[plans.plan == saved.plan].sort_values('month').x, rtol=0, atol=1e-6)
                    if plan != 'Hindsight':
                        for records, label in [(frozen, key), (rolling, origin)]:
                            record = records[(records.origin == label) & (records.method == method) &
                                             (records.kind == kind) & (records.penalty == 5000)].sort_values('month')
                            close(g[['x', 'a', 'r']], record[['x', 'a', 'r']], rtol=0, atol=1e-6)
                assert case['costs'].iloc[0].Hindsight <= saved_total + 1e-6 * abs(saved_total)
            if method != 'Hindsight':
                f = forecasts[forecasts.method == method].sort_values('month')
                err = actual.gap.to_numpy() - f.forecast_gap.to_numpy()
                close([abs(err).mean(), err.mean()], accuracy.loc[method, ['gap_mae', 'gap_mean_signed_error']], rtol=1e-10)
                if method in ['Mean-24', 'Persistence']:
                    close(case['monthly'][method, 'Rule'].x, case['monthly'][method, 'LP'].x, rtol=0, atol=.001)
        h = case['costs'].iloc[0].Hindsight
        assert np.allclose(case['costs'].Hindsight, h)
        choices = [(float(case['costs'].loc[m, p]), m, p) for m in METHODS for p in ['Rule', 'LP', 'Protected']]
        case['best'] = min(choices)
        cases.append(case)
    summary = read(VALUE + 'summary.csv').query("scope == 'all'").set_index('method')
    assert len(costs) == 125 and len(cfg['origins']) == 25
    for m in METHODS:
        g = costs[costs.method == m]
        for plan in ['Rule', 'LP', 'Protected', 'Hindsight']:
            close(summary.loc[m, 'mean_' + plan], g[plan].mean(), rtol=1e-12)
        for metric in ['optimisation', 'protection']:
            close(summary.loc[m, metric + '_mean'], g['value_' + metric].mean(), rtol=1e-12)
        assert [summary.loc[m, k] for k in ['lp_cheaper', 'lp_equal', 'lp_dearer']] == [
            int((g.value_optimisation > g.optimisation_band).sum()),
            int((g.value_optimisation.abs() <= g.optimisation_band).sum()),
            int((g.value_optimisation < -g.optimisation_band).sum())]
    prior = read(ROLL + '2025-03/actuals.csv').sort_values('month')
    seasonal = cases[1]['forecasts'].query("method == 'Seasonal naive'").sort_values('month')
    close(prior.gap, seasonal.forecast_gap, rtol=0, atol=1e-6)
    cases[1]['prior_actual'] = prior
    print('PASS: both origins agree across saved Rule/checkpoint and fair/rolling plans, forecasts, scores; all 32 monthly schedules reconcile and hindsight bounds hold', flush=True)
    return cases, costs, summary


def percentile(costs, method, origin, metric):
    group = costs[costs.method == method]
    values = group['value_' + metric].copy()
    if metric == 'optimisation':
        values = values.mask(values.abs() <= group.optimisation_band, 0.)
    target = values[group.origin == origin].iloc[0]
    result = 100 * (sum(values < target) + .5 * sum(values == target)) / len(values)
    # Independent midrank check, with tied values assigned the mean of their rank endpoints.
    ordered = sorted(values)
    positions = [i for i, v in enumerate(ordered) if v == target]
    close(result, 100 * (min(positions) + max(positions) + 1) / (2 * len(ordered)))
    return result


def plots(cases):
    FIG.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.fonttype': 'none', 'savefig.facecolor': 'white'})

    def save(fig, stem):
        for extension in ['png', 'svg']:
            path = FIG / (stem + '.' + extension)
            fig.savefig(path, dpi=300, bbox_inches='tight', metadata={'Description': 'Read-only case study of saved plans; costs in illustrative model units.'}) if extension == 'svg' else fig.savefig(path, dpi=300, bbox_inches='tight')
            if extension == 'svg':
                path.write_bytes(('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines()) + '\n').encode('utf-8'))
            IMAGES.append(path)
        plt.close(fig)

    for c in cases:
        dates = pd.to_datetime(c['actual'].index)
        labels = [d.strftime('%b\n%Y') for d in dates]
        x = np.arange(6)
        actual = c['actual'].gap.to_numpy()
        fig, ax = plt.subplots(figsize=(9.4, 3.7), layout='constrained')
        ax.plot(x, actual, color='black', marker='o', linewidth=2.2)
        ax.fill_between(x, actual, color='#EEEEEE')
        for j in [int(actual.argmin()), int(actual.argmax())]:
            ax.annotate(f'{actual[j]:,.0f} beds', (j, actual[j]), xytext=(0, 12), textcoords='offset points', ha='center')
        ax.set(xticks=x, xticklabels=labels, ylabel='Actual planning gap (beds)', ylim=(0, max(actual) * 1.2),
               title=c['title'] + ': what happened in the next six months')
        ax.grid(axis='y', alpha=.2)
        save(fig, c['stem'] + '_actual_gap')

        fig, ax = plt.subplots(figsize=(10.6, 4.5), layout='constrained')
        for method, style, marker in zip(METHODS, STYLES, MARKERS):
            f = c['forecasts'][c['forecasts'].method == method].sort_values('month')
            ax.plot(x, f.forecast_gap, color=COLORS[method], linestyle=style, marker=marker, label=method, linewidth=1.7)
        ax.plot(x, actual, color='black', marker='X', linewidth=2.5, label='Actual', zorder=5)
        ax.set(xticks=x, xticklabels=labels, ylabel='Planning gap (beds)',
               title=c['title'] + ': five forecasts against the actual gap')
        ax.grid(axis='y', alpha=.2)
        ax.legend(loc='upper center', bbox_to_anchor=(.5, -.18), ncol=3, frameon=False)
        save(fig, c['stem'] + '_forecasts')

        fig, ax = plt.subplots(figsize=(12.8, 5.2), layout='constrained')
        width = .23
        for i, method in enumerate(METHODS):
            for j, plan in enumerate(['Rule', 'LP', 'Protected']):
                at = i + (j - 1) * width
                op, short, total = c['parts'][method, plan]
                ax.bar(at, op / 1e6, width=.20, color=COLORS[method], edgecolor='#333333', linewidth=.7)
                ax.bar(at, short / 1e6, bottom=op / 1e6, width=.20, color='white', edgecolor=COLORS[method], hatch='////', linewidth=1)
                ax.text(at, total / 1e6 + .35, f'{total / 1e6:.2f}', ha='center', fontsize=8)
                ax.text(at, -.035, ['Rule', 'LP', 'Prot.'][j], transform=ax.get_xaxis_transform(), ha='center', va='top', fontsize=8)
        h = c['costs'].iloc[0].Hindsight / 1e6
        ax.axhline(h, color='black', linestyle='--', linewidth=1.5)
        ax.set(xticks=range(5), xticklabels=METHODS, ylabel='Six-month cost (million model units)',
               title=c['title'] + ': operating costs and shortages', ylim=(0, c['costs'][['Rule', 'LP', 'Protected']].to_numpy().max() / 1e6 * 1.16))
        ax.tick_params(axis='x', pad=27)
        ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
        ax.legend(handles=[Patch(facecolor='#888888', edgecolor='#333333', label='Operating: running + opening + closing'),
                           Patch(facecolor='white', edgecolor='#555555', hatch='////', label='Shortfall cost'),
                           Line2D([0], [0], color='black', linestyle='--', label=f'Hindsight: {h:.2f}m')],
                  loc='upper center', bbox_to_anchor=(.5, -.18), ncol=3, frameon=False, fontsize=9)
        save(fig, c['stem'] + '_chain')

        groups = [c['monthly'][m, 'Protected'] for m in METHODS] + [c['monthly']['Hindsight', 'Hindsight']]
        grid = np.array([g.monthly_cost.to_numpy() / 1e6 for g in groups])
        totals = grid.sum(axis=1).reshape(-1, 1)
        fig, (ax, total_ax) = plt.subplots(1, 2, figsize=(12.3, 5.8), gridspec_kw={'width_ratios': [6, 1.2]}, layout='constrained')
        cmap = plt.get_cmap('YlOrRd')
        for axis, values, is_total in [(ax, grid, False), (total_ax, totals, True)]:
            norm = Normalize(vmin=0, vmax=float(values.max()))
            im = axis.imshow(values, cmap=cmap, norm=norm, aspect='auto')
            for i, g in enumerate(groups):
                for j in range(values.shape[1]):
                    short = float(g.shortfall.sum()) if is_total else float(g.shortfall.iloc[j])
                    spare = float(g.spare.sum()) if is_total else float(g.spare.iloc[j])
                    if is_total:
                        note = f'short {short:,.0f}\nspare {spare:,.0f}'
                    elif round(short) > 0:
                        note = f'short {short:,.0f}'
                    elif round(spare) > 0:
                        note = f'spare {spare:,.0f}'
                    else:
                        note = 'balanced'
                    axis.text(j, i, f'{values[i, j]:.2f}m\n{note}', ha='center', va='center', fontsize=9,
                              color='white' if norm(values[i, j]) > .66 else '#222222')
            axis.set(xticks=range(values.shape[1]), xticklabels=['Total'] if is_total else labels,
                     yticks=range(6), yticklabels=[] if is_total else METHODS + ['Hindsight'])
            axis.tick_params(length=0)
            axis.set_xticks(np.arange(-.5, values.shape[1], 1), minor=True)
            axis.set_yticks(np.arange(-.5, 6, 1), minor=True)
            axis.grid(which='minor', color='white', linewidth=2); axis.tick_params(which='minor', length=0)
            bar = fig.colorbar(im, ax=axis, location='bottom', shrink=.9, pad=.08)
            bar.set_label(('Total' if is_total else 'Monthly') + ' cost (million units)', fontsize=9)
        fig.suptitle(c['title'] + ': Protected plans and hindsight\nComputed from saved plans; Total uses a separate colour scale', fontsize=13)
        save(fig, c['stem'] + '_protected_heatmap')

    fig, axes = plt.subplots(1, 5, figsize=(14.5, 4.6), sharey=True, layout='constrained')
    for ax, method in zip(axes, METHODS):
        for c, style, marker in zip(cases, ['-', '--'], ['o', 's']):
            r = c['costs'].loc[method]
            values = np.array([r[p] - r.Hindsight for p in ['Rule', 'LP', 'Protected']]) / 1e6
            ax.plot([0, 1, 2], values, color=COLORS[method], linestyle=style, marker=marker, linewidth=2,
                    markerfacecolor=COLORS[method] if style == '-' else 'white', label=c['title'])
        ax.axhline(0, color='black', linewidth=1)
        ax.set(title=method, xticks=[0, 1, 2], xticklabels=['Rule', 'LP', 'Protected'], xlim=(-.2, 2.2))
        ax.grid(axis='y', alpha=.2)
    axes[0].set_ylabel('Cost above hindsight (million model units)')
    fig.suptitle('The two frozen origins: how each step changed the gap to hindsight', fontsize=15)
    fig.legend(handles=[Line2D([0], [0], color='#444444', marker='o', linestyle='-', label='September 2025'),
                        Line2D([0], [0], color='#444444', marker='s', markerfacecolor='white', linestyle='--', label='March 2026')],
               loc='lower center', bbox_to_anchor=(.5, -.09), ncol=2, frameon=False)
    save(fig, 'two_origin_chain')


def build_readme(cases, costs, summary, cfg):
    lines = [
        '# Does optimisation improve a bed-capacity forecast? Two frozen origins', '',
        '## Question', '',
        'We freeze a bed-capacity plan before knowing the next six months of demand. '
        'Five methods forecast NHS England general and acute (G&A) beds, then we either follow each forecast, optimise the schedule, or optimise with protection against forecast error. '
        'Does optimisation make the realised plan cheaper, why, and by how much? '
        'How much does protection add on top?', '',
        '## Method', '',
        'At each origin, only observations available up to that month are used. Each method uses the 24-month window ending at the origin and forecasts the next six months: '
        '**September 2025** covers **October 2025 to March 2026**, and **March 2026** covers **April to September 2026**. '
        'All six decisions stay fixed; there is no monthly replanning.', '',
        'The planning gap is **occupied / 0.90 - available**, measured in beds. It is the additional capacity needed to keep occupancy at or below 90%. '
        'A shortfall here means falling short of that capacity target.', '',
        'Each method forecasts occupied and available beds separately:', '',
        '- **Mean-24:** repeats the average of its training window.',
        '- **Persistence:** repeats the last observed month.',
        '- **Seasonal naive:** repeats the same calendar month from the previous year.',
        '- **SARIMA-24:** a fixed seasonal time-series model using the training window.',
        '- **ExtraTrees-24:** a fixed tree ensemble using earlier values and calendar features; predictions feed later forecast steps.', '',
        '**Rule** follows the forecast gap, opening the requested non-negative capacity as far as the limits allow. '
        '**LP** optimises the entire schedule: running costs 500 per bed-month, opening 550 per bed opened, closing 300 per bed closed, and shortfall 5,000 per missing bed-month. '
        'Capacity carries over between months, starts at zero, is capped at 15,000 beds, and can rise or fall by at most 5,000 beds per month; there is no terminal closing charge.', '',
        '**Protected** uses the same optimisation with scenarios formed from the method\'s own past forecast errors, adding a data-driven safety margin. '
        '**Hindsight** uses the actual future gap: it is the best possible schedule under these same constraints. '
        'All forecasts, plans and total scores below were already saved; nothing was refitted, re-solved or rescored.', '',
        'Costs are **illustrative model units (validated: False)**, not NHS expenditure estimates. '
        'Cost tables use **millions**. Operating cost means running + opening + closing; total cost adds the shortfall penalty. '
        'Small solver-rounding differences are treated as equal using the saved relative tolerance of 1e-6.', '']

    for index, c in enumerate(cases, 1):
        base, actual, acc, score = c['base'], c['actual'], c['accuracy'], c['costs']
        actual_source, accuracy_source = base + 'actuals.csv', base + 'accuracy.csv'
        score_source, monthly_source = VALUE + 'costs.csv', VALUE + 'monthly_costs.csv'
        lines += [f'## Origin {index}: {c["title"]}', '', '### What happened', '', figure(c['stem'] + '_actual_gap', c['title'] + ' actual planning gap'), '']
        if index == 1:
            assert actual.gap.idxmin() == '2025-12-01' and actual.gap.idxmax() == '2026-01-01'
            story = (f'The gap dipped to **{n(actual.loc["2025-12-01", "gap"], actual_source, 0, 1)} beds in December**, '
                     f'then peaked at **{n(actual.loc["2026-01-01", "gap"], actual_source, 0, 1)} in January**. '
                     f'It remained at {n(actual.loc["2026-02-01", "gap"], actual_source, 0, 1)} in February before easing to '
                     f'{n(actual.loc["2026-03-01", "gap"], actual_source, 0, 1)} in March.')
        else:
            prior = c['prior_actual']
            assert (actual.gap.to_numpy() > prior.gap.to_numpy()).all()
            story = (f'The April-to-September gap averaged **{n(actual.gap.mean(), actual_source, 0, 1)} beds in 2026**, '
                     f'compared with **{n(prior.gap.mean(), ROLL + "2025-03/actuals.csv", 0, 1)}** in the same months of 2025. '
                     f'It fell to {n(actual.loc["2026-08-01", "gap"], actual_source, 0, 1)} in August, then rose to '
                     f'{n(actual.loc["2026-09-01", "gap"], actual_source, 0, 1)} in September.')
        lines += [story, '', '### What each method forecast', '', figure(c['stem'] + '_forecasts', c['title'] + ' gap forecasts against actual'), '',
                  'MAE is the average absolute gap error, in beds. Signed error is **actual minus forecast**: positive means the forecast was too low on average; negative means too high.', '',
                  table(['Method', 'Gap MAE (beds)', 'Mean signed error (beds)', 'Forecast on average'],
                        [[m, n(acc.loc[m, 'gap_mae'], accuracy_source, 1, 1),
                          n(acc.loc[m, 'gap_mean_signed_error'], accuracy_source, 1, 1, True),
                          'Too low' if acc.loc[m, 'gap_mean_signed_error'] > 0 else 'Too high'] for m in METHODS]), '',
                  'Source: ' + link('saved accuracy', accuracy_source) + '.', '', '### The cost chain', '',
                  'Each cell is **total (operating + shortfall)**, in millions. Bold marks the cheapest forecast-based plan; hindsight is a benchmark. '
                  'Cost components are **computed from saved plans** and checked against saved scores. Rounded components may differ from rounded totals.', '']
        chain_rows = []
        for method in METHODS:
            cells = []
            for plan in ['Rule', 'LP', 'Protected', 'Hindsight']:
                key = ('Hindsight', 'Hindsight') if plan == 'Hindsight' else (method, plan)
                op, short, total = c['parts'][key]
                provenance = base + 'plans.csv' if plan == 'Hindsight' else monthly_source
                shown = n(total, provenance)
                if (method, plan) == c['best'][1:]:
                    shown = '**' + shown + '**'
                cells.append(shown + '<br>(' + n(op, provenance) + ' + ' + n(short, provenance) + ')')
            chain_rows.append([method] + cells)
        lines += [table(['Method', 'Rule', 'LP', 'Protected', 'Hindsight'], chain_rows), '',
                  figure(c['stem'] + '_chain', c['title'] + ' cost chain with operating and shortfall components'), '',
                  'Source: ' + link('saved total scores', base + 'totals.csv') + ' and ' + link('saved Rule/LP/Protected monthly costs', monthly_source) + '.', '',
                  '### Why optimisation helped', '',
                  'The tables show additional beds open (**x**), additions (**+**) and removals (**-**) each month. '
                  'All values are beds, rounded to one decimal. These are the frozen schedules, not decisions updated after observing demand.', '']
        dip = 'December' if index == 1 else 'August'
        before = 'November' if index == 1 else 'July'
        for method in ['Seasonal naive', 'SARIMA-24']:
            rule, lp = c['monthly'][method, 'Rule'], c['monthly'][method, 'LP']
            records = []
            for j, date in enumerate(pd.to_datetime(rule.month)):
                records.append([date.strftime('%b %Y')] + [n(g.iloc[j][column], monthly_source, 1, 1)
                               for g in [rule, lp] for column in ['x', 'a', 'r']])
            op_gain = c['parts'][method, 'Rule'][0] - c['parts'][method, 'LP'][0]
            short_gain = c['parts'][method, 'Rule'][1] - c['parts'][method, 'LP'][1]
            close(op_gain + short_gain, score.loc[method, 'value_optimisation'])
            dip_index = 2 if index == 1 else 4
            assert rule.r.iloc[dip_index] > 0 and lp.r.iloc[dip_index] < 1e-6
            close(lp.x.iloc[dip_index], lp.x.iloc[dip_index - 1], rtol=0)
            assert lp.a.iloc[dip_index + 1] < rule.a.iloc[dip_index + 1]
            lines += ['**' + method + '**', '', table(['Month', 'Rule x', 'Rule +', 'Rule -', 'LP x', 'LP +', 'LP -'], records), '',
                      f'LP kept the {before} capacity through {dip}, reducing the amount that had to reopen next month. '
                      f'It saved **{n(score.loc[method, "value_optimisation"], score_source)}m**: '
                      f'{n(op_gain, monthly_source)}m in operating cost and {n(short_gain, monthly_source)}m in shortfall cost.', '']
        lines += ['Mean-24 and Persistence have flat forecasts, so **Rule = LP within solver precision**. '
                  'Holding beds through a dip can cost more in that month but avoid a larger closing-and-reopening bill; it can also cover demand that the forecast underestimated.', '',
                  '### Why protection helped or hurt', '',
                  'Protection uses past errors, not knowledge of this future. A forecast that is already high can already provide spare capacity; adding protection can then increase cost. '
                  'The effect below is LP minus Protected, with the saved tie band of 1% of hindsight cost.', '',
                  table(['Method', 'Forecast on average', 'Point LP cost', 'Protected cost', 'Effect'],
                        [[m, 'Too low' if acc.loc[m, 'gap_mean_signed_error'] > 0 else 'Too high',
                          n(score.loc[m, 'LP'], score_source), n(score.loc[m, 'Protected'], score_source),
                          ('Helped by ' if score.loc[m, 'value_protection'] > score.loc[m, 'protection_band'] else 'Hurt by ' if score.loc[m, 'value_protection'] < -score.loc[m, 'protection_band'] else 'Tie: ') +
                          n(abs(score.loc[m, 'value_protection']), score_source) + 'm'] for m in METHODS]), '',
                  '### Where the Protected-plan costs occurred', '',
                  figure(c['stem'] + '_protected_heatmap', c['title'] + ' Protected monthly cost heatmap and hindsight'), '',
                  '**Computed from saved plans.** Each monthly cell shows cost and the shortfall or spare capacity, rounded to whole beds. '
                  'Balanced means neither is visible after rounding. Total cells sum costs and show both shortfall and spare bed-months; they use a separate colour scale. '
                  'The totals include opening costs at the start of the plan.', '', '### Result', '']
        best_cost, best_method, best_plan = c['best']
        result = f'**{best_method} + {best_plan}** was the cheapest forecast-based combination, at **{n(best_cost, score_source)}m**.'
        if index == 1:
            best_protected = score.Protected.idxmin()
            result += (' Protection helped Mean-24, Persistence and ExtraTrees-24, but made Seasonal naive and SARIMA-24 more expensive. '
                       f'{best_protected} was the cheapest Protected plan, at {n(score.loc[best_protected, "Protected"], score_source)}m.')
        else:
            result += (' Protection helped the low Seasonal naive and SARIMA-24 forecasts, but hurt the other methods, whose forecasts were high on average.')
        assert len(re.split(r'(?<=[.!?])\s+(?=[A-Z*])', result)) <= 3
        lines += [result, '']

    side_rows = []
    for m in METHODS:
        chain = [' / '.join(n(c['costs'].loc[m, p], VALUE + 'costs.csv') for p in ['Rule', 'LP', 'Protected', 'Hindsight']) for c in cases]
        benefits = [' / '.join(n(c['costs'].loc[m, 'value_' + metric], VALUE + 'costs.csv', signed=True) for metric in ['optimisation', 'protection']) for c in cases]
        side_rows.append([m, *chain, *benefits])
    lines += ['## The two origins side by side', '',
              'Costs and benefits are millions. Each cost chain is **Rule / LP / Protected / Hindsight**; each benefit pair is **optimisation / protection**. '
              'Positive benefits mean a saving; negative benefits mean an extra cost.', '',
              table(['Method', 'Sep 2025 cost chain', 'Mar 2026 cost chain', 'Sep 2025 benefits', 'Mar 2026 benefits'], side_rows), '',
              figure('two_origin_chain', 'Two origins compared as cost above their own hindsight benchmark'), '',
              'Optimisation helped Seasonal naive, SARIMA-24 and ExtraTrees-24 at both origins, while the flat forecasts were unchanged within tolerance. '
              'Protection helped Mean-24, Persistence and ExtraTrees-24 in September but hurt them in March, while Seasonal naive and SARIMA-24 switched from hurt to helped. '
              'Those switches follow the average forecast direction: the former group went from too low to too high, and the latter group went from too high to too low.', '',
              '## How typical are these two windows?', '',
              'The saved comparison retains 25 origins for every method. The table gives mean cost per six-month plan; C/E/D counts LP cheaper/equal/dearer than Rule under the registered tolerance.', '']
    typical = []
    for m in METHODS:
        r = summary.loc[m]
        counts = '/'.join(n(r[k], VALUE + 'summary.csv', 0, 1) for k in ['lp_cheaper', 'lp_equal', 'lp_dearer'])
        typical.append([m] + [n(r['mean_' + p], VALUE + 'summary.csv') for p in ['Rule', 'LP', 'Protected', 'Hindsight']] +
                       [n(r.optimisation_mean, VALUE + 'summary.csv') + ' (' + counts + ')', n(r.protection_mean, VALUE + 'summary.csv')])
    lines += [table(['Method', 'Rule', 'LP', 'Protected', 'Hindsight', 'Optimisation value (C/E/D)', 'Protection value'], typical), '',
              'Source: ' + link('saved 25-origin summary', VALUE + 'summary.csv') + '.', '',
              'Percentiles rank each case-study benefit within that method\'s saved 25-origin results. Higher means a larger saving, not necessarily a positive saving. '
              'We use a midrank: **100 × (number below + half the number tied) / 25**. Optimisation benefits within the registered cost-equality tolerance count as zero for these ranks; the raw scores stay unchanged.', '',
              table(['Method', 'Sep: optimisation percentile', 'Sep: protection percentile', 'Mar: optimisation percentile', 'Mar: protection percentile'],
                    [[m] + [n(percentile(costs, m, c['origin'], metric), VALUE + 'costs.csv', 0, 1) + '%'
                            for c in cases for metric in ['optimisation', 'protection']] for m in METHODS]), '',
              'The flat methods therefore sit at the middle optimisation percentile. Seasonal naive\'s March protection benefit was the largest in its saved distribution, so that window shows an unusually large gain.', '',
              'Neighbouring origins share up to five target months, so the 25 origins are about four to five independent windows. '
              'This describes a rough count of disjoint six-month blocks; statistical dependence may extend across them.', '', '## Answer', '']
    max_gain = max(c['costs'].value_optimisation.max() for c in cases)
    protection_values = [float(v) for c in cases for v in c['costs'].value_protection]
    best_mean = min((float(summary.loc[m, 'mean_' + p]), m, p) for m in METHODS for p in ['Rule', 'LP', 'Protected'])
    pooled = summary.loc['Pooled']
    answer = ('LP never cost materially more than Rule in either case study or the saved 25-origin comparison. '
              f'It saved up to {n(max_gain, VALUE + "costs.csv")}m per case-study plan for the changing Seasonal naive, SARIMA-24 and ExtraTrees-24 forecasts by keeping beds open through dips; flat forecasts gained nothing material. '
              f'Protection ranged from an extra cost of {n(-min(protection_values), VALUE + "costs.csv")}m to a saving of {n(max(protection_values), VALUE + "costs.csv")}m across these cases, helping forecasts that were too low on average and hurting those already high. '
              f'The cheapest combinations were {cases[0]["best"][1]} + {cases[0]["best"][2]} in September ({n(cases[0]["best"][0], VALUE + "costs.csv")}m), '
              f'{cases[1]["best"][1]} + {cases[1]["best"][2]} in March ({n(cases[1]["best"][0], VALUE + "costs.csv")}m), and '
              f'{best_mean[1]} + {best_mean[2]} across the 25 origins ({n(best_mean[0], VALUE + "summary.csv")}m on average). '
              f'Across those origins, optimisation saved {n(pooled.optimisation_mean, VALUE + "summary.csv")}m and protection added {n(pooled.protection_mean, VALUE + "summary.csv")}m per method-origin case on average, although protection cost more in '
              f'{n(pooled.protection_hurt, VALUE + "summary.csv", 0, 1)} of {n(pooled.plan_count, VALUE + "summary.csv", 0, 1)} cases.')
    assert len(re.split(r'(?<=[.!?])\s+(?=[A-Z])', answer)) <= 5
    assert int(pooled.lp_dearer) == 0
    for c in cases:
        assert (c['costs'].value_optimisation >= -c['costs'].optimisation_band).all()
        sign = c['accuracy'].loc[METHODS, 'gap_mean_signed_error'].to_numpy()
        assert ((sign > 0) == (c['costs'].loc[METHODS, 'value_protection'].to_numpy() > 0)).all()
    assert cases[1]['costs'].loc['Seasonal naive', 'value_protection'] == costs.query("method == 'Seasonal naive'").value_protection.max()
    lines += [answer, '', '## Limitations', '',
              '- These are two case-study origins; the overlapping comparison gives broader descriptive context.',
              '- The models are univariate and do not use staffing, admissions, discharge or policy variables.',
              '- National monthly totals hide local bottlenecks and daily peaks.',
              '- Model specifications were fixed, not tuned for these windows.',
              '- Costs are illustrative; capacity is not a full staffing or clinical feasibility model.',
              '- Results depend on the shortage price and the assumed capacity limits.', '',
              '<details>', '<summary>Saved sources, verification and reproduction</summary>', '',
              'The builder verifies the existing SHA-256 manifests, agreement between the two-origin and rolling records, '
              'Rule agreement with the clean checkpoint, monthly sums, hindsight bounds, numerical provenance, table structure and figure links. '
              'All checks passed. Monthly cost arithmetic only reconstructs components of the saved plans; no model or optimisation routine is called.', '',
              'Checker correction: Rule, LP and Protected are checked against their hashed frozen plans. '
              'Hindsight is checked against the saved scored plans and totals from both the fair comparison and clean checkpoint, '
              'which agree to 1e-6 relative. A scored-only unit test covers this distinction; no saved data or results changed.', '',
              'The source files are:', '']
    for c in cases:
        lines.append('- **' + c['title'] + ':** ' + ', '.join(link(name.removesuffix('.csv'), c['base'] + name)
                     for name in ['actuals.csv', 'forecasts.csv', 'accuracy.csv', 'plans.csv', 'totals.csv']) +
                     '; ' + link('clean-checkpoint totals', CLEAN + c['key'] + '/totals.csv') +
                     '; ' + link('clean-checkpoint scored plans', CLEAN + c['key'] + '/plans.csv') +
                     '; ' + link('rolling totals', ROLL + c['origin'] + '/totals.csv') + '.')
    lines += ['- **Rule and common scoring:** ' + ', '.join(link(name.removesuffix('.csv'), VALUE + name)
              for name in ['rule_plans.csv', 'monthly_costs.csv', 'costs.csv']) + '.',
              '- **Broader context:** ' + link('25-origin summary', VALUE + 'summary.csv') + ' and ' + link('April–September 2025 actuals', ROLL + '2025-03/actuals.csv') + '.',
              '- **Definitions:** ' + link('fair-comparison configuration', FAIR + 'config.json') + ' and ' + link('registered optimisation-value configuration', VALUE + 'config.json') + '.', '',
              'Rebuild the README and figures with `python -B scripts/case_study/build.py`. '
              'The script reads saved files and writes only this README and the new case-study figures.', '', '</details>', '']
    return '\n'.join(lines)


def audit_publication(text, cfg):
    assert len(TABLES) == 13
    for expected in TABLES:
        assert text.count(expected) == 1
        lines = expected.splitlines()
        width = len(lines[0].split('|'))
        assert all(len(line.split('|')) == width for line in lines)
        assert all(cell.strip() == '---' for cell in lines[1].strip('|').split('|'))
    # All numeric strings in the prose/tables must be either traced data or
    # explicitly checked method constants, origin labels and structural labels.
    assert cfg['conventions']['relative_cost_tolerance'] == 1e-6
    assert cfg['conventions']['solver_schedule_tolerance_beds'] == .001
    literal_facts = {'1', '2', '24', '25', '2025', '2026', '0.90', '90', '500', '550', '300',
                     '5,000', '15,000', '1e-6', '100', '256'}
    tokens = re.compile(r'(?<![A-Za-z])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:e-\d+)?')
    allowed = set(literal_facts)
    for display, value, path in NUMBERS:
        assert path in SOURCES and np.isfinite(value)
        allowed.update(tokens.findall(display))
    visible = re.sub(r'\]\([^)]+\)', ']', text)
    unknown = set(tokens.findall(visible)) - allowed
    assert not unknown, ('Untraced README numbers', unknown)
    for dest in re.findall(r'\]\(([^)]+)\)', text):
        assert (ROOT / dest).is_file(), dest
    assert len(IMAGES) == 18
    for path in IMAGES:
        if path.suffix == '.png':
            with Image.open(path) as im:
                assert all(abs(d - 300) < .01 for d in im.info['dpi'])
        else:
            ET.parse(path)
    for path, expected in SOURCES.items():
        assert digest(ROOT / path) == expected, path
    print(f'PASS: {len(TABLES)} Markdown tables, {len(NUMBERS)} traced numerical entries, all links and 9 PNG/SVG pairs (300 dpi)', flush=True)


def main():
    inventory = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().strip('\0').split('\0')
    previous = {p: digest(ROOT / p) for p in inventory if p and p != 'README.md' and
                p != 'scripts/case_study/build.py' and not p.startswith('figures/case_study/')}
    cfg = verify_hashes()
    cases, costs, summary = load_cases(cfg)
    document = build_readme(cases, costs, summary, cfg)
    plots(cases)
    audit_publication(document, cfg)
    assert all(digest(ROOT / p) == expected for p, expected in previous.items()), 'A prior file changed'
    (ROOT / 'README.md').write_bytes(document.encode('utf-8'))
    print(f'PASS: all {len(previous)} prior files preserved; only the requested README, script and new figures changed', flush=True)
    report = {}
    for c in cases:
        report[c['origin']] = {'cheapest': c['best'], 'methods': {
            m: {'optimisation_m': c['costs'].loc[m, 'value_optimisation'] / 1e6,
                'protection_m': c['costs'].loc[m, 'value_protection'] / 1e6,
                'optimisation_percentile': percentile(costs, m, c['origin'], 'optimisation'),
                'protection_percentile': percentile(costs, m, c['origin'], 'protection')}
            for m in METHODS}}
    print(json.dumps(report, indent=2), flush=True)
    print('Saved numerical sources:\n' + '\n'.join(sorted(SOURCES)), flush=True)


if __name__ == '__main__':
    main()
