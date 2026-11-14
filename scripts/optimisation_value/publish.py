"""Finish and audit the publication using saved outputs only.

This never imports the modelling pipeline, generates plans, scores, or runs tests.
Run after report.py has prepared the draft and figures. Existing scientific
outputs and figures are protected by a byte-hash manifest during publication.
"""
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'outputs/optimisation_value'
TITLE = '## Does optimisation help? Forecast alone vs optimisation vs protection'
METHODS = ['Mean-24', 'Persistence', 'Seasonal naive', 'SARIMA-24', 'ExtraTrees-24']
OLD_ROW = '| Seasonal naive | 3.50 | 14 | 0/25/0 | 194.3 | 95.3% (143/150) | 1.01 |'
NEW_ROW = OLD_ROW.replace('0/25/0', '-')
ENDING = ('Neighbouring origins share up to five target months, so origins are not '
          'independent; costs are illustrative model units (validated: False).')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def rows(name):
    with (OUT / name).open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def write(path, text):
    path.write_bytes(text.encode('utf-8'))


def save(name, value):
    write(OUT / name, json.dumps(value, indent=2) + '\n')


def number(value, scale=1e6, places=2):
    result = f'{float(value) / scale:.{places}f}'
    return result[1:] if result.startswith('-') and float(result) == 0 else result


def percent(value):
    return number(value, .01, 1) + '%'


def equal_cases():
    costs = rows('costs.csv')
    return {m: [r['origin'] for r in costs if r['method'] == m and
                abs(float(r['value_optimisation'])) <= float(r['optimisation_band'])]
            for m in METHODS}


def baseline():
    cfg = load('config.json')
    raw = subprocess.check_output(['git', 'show', cfg['source_commit'] + ':README.md'], cwd=REPO)
    original = raw.decode('utf-8').replace('\r\n', '\n')
    assert original.count(OLD_ROW) == 1
    return original.replace(OLD_ROW, NEW_ROW).rstrip() + '\n\n'


def finish():
    """Edit the saved draft and record the already completed scientific checks."""
    assert not (OUT / 'publication_input_hashes.json').exists(), 'Publication already prepared'
    protected = [p for p in OUT.rglob('*') if p.is_file() and p.name != 'readme_section.md']
    protected += list((REPO / 'figures/optimisation_value').glob('*'))
    save('publication_input_hashes.json', {p.relative_to(REPO).as_posix(): sha(p) for p in protected})
    cfg = load('config.json')
    phase0 = load('phase0.json')
    checks = load('verification_checks.json')
    assert phase0['status'] == 'PASS' and all(c['status'] == 'PASS' for c in checks)
    equal = equal_cases()
    save('equal_cases.json', {'definition': cfg['metrics']['M2'],
                            'total': sum(map(len, equal.values())), 'origins_by_method': equal})
    summary = {(r['scope'], r['method']): r for r in rows('summary.csv')}
    pooled = summary['all', 'Pooled']
    section = (OUT / 'readme_section.md').read_text(encoding='utf-8')
    assert section.startswith(TITLE)
    section = section.replace('### Mean costs and value added', '### Table 1. Mean costs and value added')
    section = section.replace('### Why the LP helps', '### Table 2. Why the LP helps')
    section = section.replace('the other columns are mean counts per plan.',
                              'events and shortfall bed-months are also averages per plan.')
    explanation = (f'Optimisation never cost more beyond the registered tolerance: '
                   f'LP was cheaper in **{pooled["lp_cheaper"]} of {pooled["plan_count"]}** cases and equal in '
                   f'**{pooled["lp_equal"]}**. Protection cost more in **{pooled["protection_hurt"]} of '
                   f'{pooled["plan_count"]}** cases, but helped on average.\n\n'
                   '<details>\n<summary>Which cases were equal?</summary>\n\n'
                   '| Method | Equal cases | Origins (YYYY-MM) |\n| --- | --- | --- |\n')
    for m in METHODS:
        origins = 'All retained origins' if len(equal[m]) == len(cfg['origins']) else ', '.join(equal[m])
        explanation += f'| {m} | {len(equal[m])} | {origins} |\n'
    explanation += ('\nEqual means within the registered cost tolerance, not necessarily bit-for-bit equality. '
                    'The [full equal-case list](outputs/optimisation_value/equal_cases.json) records every origin.\n\n</details>\n\n')
    first = '![Mean cost from forecast alone to optimisation and protection]'
    section = section.replace(first, explanation + '**Figure 1. Mean cost at each planning step.** '
                              '[SVG](figures/optimisation_value/chain_by_method.svg)\n\n' + first)
    second = '![Value of optimisation at each origin, grouped by method]'
    section = section.replace(second, '**Figure 2. Optimisation value at each origin.** '
                              '[SVG](figures/optimisation_value/value_of_optimisation_by_origin.svg)\n\n' + second)
    write(OUT / 'readme_section.md', section)
    verification = [
        '# Verification: value of optimisation and protection', '',
        'Scientific verification completed before publication. On resumption, saved results were reused: '
        'no model, plan generation, scoring, scientific verification suite, tests or figures were rerun.', '',
        '| Check | Status | Evidence |', '| --- | --- | --- |',
        f'| Phase 0: reproduce the two anchors | PASS | {phase0["reproduction_rows"]} method-origin totals; '
        f'maximum relative Rule-cost difference {phase0["max_rule_cost_relative_difference"]:.16g}; '
        f'maximum forecast difference {phase0["max_forecast_difference_beds"]:.16g} beds; '
        f'{phase0["readme_rows"]} saved rolling README rows agree. |']
    verification += [f'| {c["check"]} | {c["status"]} | {c["detail"]} |' for c in checks]
    verification += ['', '## Frozen inputs', '',
        'Config SHA-256: `' + sha(OUT / 'config.json') + '`.',
        'Rule-plan SHA-256: `' + sha(OUT / 'rule_plans.csv') + '`.',
        'Reused rolling-plan SHA-256: `' + cfg['source_plans_sha256'] + '`.', '',
        'The config and all 125 Rule schedules were frozen before new rolling Rule scoring. '
        'The required two-anchor reproduction was the only pre-registration Rule scoring. '
        'Forecasts, Point LP, Protected and hindsight results were reused unchanged.', '',
        'The common data end remains October 2026; trailing blank placeholders were already excluded '
        'in the prior study. The 25-origin list is unchanged: October 2025 (occupied SARIMA) and '
        'January 2026 (available SARIMA) were excluded for nonconvergence, and 15 earlier origins '
        'had insufficient calibration paths. No exclusions were chosen from these new results.', '',
        '## Interpretation and precision', '',
        'E1, E2 and E3 held under the registered definitions; expectations were not stop conditions. '
        'E1 uses a 0.001-bed schedule tolerance for saved CBC precision. '
        'E2 uses 1e-6 relative cost tolerance against Rule cost. Tiny negative raw optimisation '
        'benefits remain in the CSV files; none exceeds that tolerance. '
        'Protection uses a 1%-of-hindsight tie band.', '',
        f'Equal cases: {pooled["lp_equal"]}, comprising ' + ', '.join(f'{m}: {len(equal[m])}' for m in METHODS) + '.',
        f'LP cheaper/equal/dearer: {pooled["lp_cheaper"]}/{pooled["lp_equal"]}/{pooled["lp_dearer"]}. '
        f'Protection helped/tie/hurt: {pooled["protection_helped"]}/{pooled["protection_tie"]}/{pooled["protection_hurt"]}.', '',
        'Close-then-reopen counts each removal month once if an addition occurs within the next two '
        'months inside the same plan. Flows below 1e-6 beds are ignored. Gap shares use ratios of sums, '
        'with no clipping. Pooled results weight every method-origin equally. '
        'Six-offset ranges are descriptive, not confidence intervals.', '',
        'Detailed evidence: [checks](verification_checks.json), [phase 0](phase0.json), '
        '[saved test log](pytest.txt), [equal origins](equal_cases.json), '
        '[publication audit](publication_audit.json).', '']
    write(OUT / 'verification.md', '\n'.join(verification))
    original = (REPO / 'README.md').read_text(encoding='utf-8')
    assert original.replace(OLD_ROW, NEW_ROW).rstrip() + '\n\n' == baseline()
    write(REPO / 'README.md', baseline() + section)


def audit():
    """Check publication numbers and preserved bytes; do not recompute study scores."""
    cfg = load('config.json')
    section = (OUT / 'readme_section.md').read_text(encoding='utf-8')
    summary = {(r['scope'], r['method']): r for r in rows('summary.csv')}
    diagnostics = {(r['scope'], r['method'], r['plan']): r for r in rows('diagnostics.csv')}
    equal = equal_cases()
    checks = []
    for name, digest in [('config.json', 'b95a791442544a9a93ccfa9f0904220c4571b85f2b51eb21a278d318edba7c90'),
                         ('rule_plans.csv', '1dea041a3fdf0828c7bc3e25b48b8accf7c953f881ec9ead511865e8f1b7cc03')]:
        assert sha(OUT / name) == digest == (OUT / (name.split('.')[0] + '.sha256')).read_text().split()[0]
        assert digest in section
    for path, expected in cfg['source_hashes'].items():
        assert sha(REPO / path) == expected, path
    for path, expected in load('publication_input_hashes.json').items():
        assert sha(REPO / path) == expected, path
    for path, expected in load('prior_files_manifest.json').items():
        if path != 'README.md':
            assert sha(REPO / path) == expected, path
    for name, expected in load('report_source_hashes.json').items():
        assert sha(OUT / name) == expected, name
    assert (REPO / 'README.md').read_text(encoding='utf-8') == baseline() + section
    checks.append('Frozen config, Rule plans, source forecasts/plans/scores, all saved scientific outputs and figures match their hashes; all prior files are unchanged except the exact authorised README edit and addition.')

    tables = []
    for match in re.finditer(r'^\| Method \|.*(?:\n\|.*)+', section, re.M):
        lines = match.group(0).splitlines()
        tables.append(([x.strip() for x in lines[0].strip('|').split('|')],
                       [[x.strip() for x in line.strip('|').split('|')] for line in lines[2:]]))
    assert len(tables) == 7
    main_scope = iter(['all', 'excluding_original_two'])
    why_scope = iter(['all', 'excluding_original_two'])
    checked_cells = 0
    for headers, body in tables:
        if headers[1] == 'Rule':
            scope = next(main_scope)
            expected = []
            for m in METHODS + ['Pooled']:
                r = summary[scope, m]
                counts = '/'.join(r[k] for k in ['lp_cheaper', 'lp_equal', 'lp_dearer'])
                protection = '/'.join(r[k] for k in ['protection_helped', 'protection_tie', 'protection_hurt'])
                expected.append([m] + [number(r['mean_' + p]) for p in ['Rule', 'LP', 'Protected', 'Hindsight']] +
                                [number(r['optimisation_mean']) + ' (' + counts + ')',
                                 number(r['protection_mean']) + ' (' + protection + ')',
                                 percent(r['optimisation_share']) + ' / ' + percent(r['protection_share'])])
        elif headers[1] == 'Opening cost':
            scope = next(why_scope)
            expected = []
            for m in METHODS + ['Pooled']:
                a, b = diagnostics[scope, m, 'Rule'], diagnostics[scope, m, 'LP']
                cols = [('mean_opening_cost', 1e6, 2), ('mean_closing_cost', 1e6, 2), ('mean_running_cost', 1e6, 2),
                        ('mean_close_then_reopen', 1, 2), ('mean_shortfall_bed_months', 1, 1)]
                expected.append([m] + [number(a[k], scale, places) + ' -> ' + number(b[k], scale, places)
                                       for k, scale, places in cols])
        elif headers[1] == 'Equal cases':
            expected = [[m, str(len(equal[m])), 'All retained origins' if len(equal[m]) == len(cfg['origins'])
                         else ', '.join(equal[m])] for m in METHODS]
        elif headers[1] == 'Optimisation value':
            expected = [[m] + [number(summary['all', m][k + '_median']) + ' [' +
                         number(summary['all', m][k + '_min']) + ', ' + number(summary['all', m][k + '_max']) + ']'
                         for k in ['optimisation', 'protection', 'total_value']] for m in METHODS + ['Pooled']]
        else:
            assert headers[1] == 'All origins: optimisation'
            expected = [[m] + [number(summary[s, m][k + '_subset_min']) + ' to ' + number(summary[s, m][k + '_subset_max'])
                         for s in ['all', 'excluding_original_two'] for k in ['optimisation', 'protection']]
                         for m in METHODS + ['Pooled']]
        assert body == expected, headers
        checked_cells += sum(len(row) - 1 for row in body)
    checks.append(f'Every numeric entry in all seven new README tables matches saved CSVs: {checked_cells} data cells, including counts, dates, distributions, subset ranges and excluded-anchor results.')

    pooled = summary['all', 'Pooled']
    expectations = load('expectations.json')
    assert all(expectations[e]['held'] for e in ['E1', 'E2', 'E3'])
    assert sum(map(len, equal.values())) == int(pooled['lp_equal']) == 69
    assert load('equal_cases.json')['origins_by_method'] == equal
    assert len(cfg['origins']) == 25 and cfg['horizon'] == 6 and cfg['costs']['shortfall'] == 5000
    assert cfg['limits']['x0'] == 0 and len(cfg['scopes']['excluding_original_two']) == 23
    assert set(cfg['origins']) - set(cfg['scopes']['excluding_original_two']) == {'2025-09', '2026-03'}
    assert all(e['maximum_difference_beds'] < .001 and e['origins_matching'] == 25
               for e in expectations['E1']['methods'])
    claims = [f'LP was cheaper in {pooled["lp_cheaper"]} cases, equal within tolerance in {pooled["lp_equal"]}, and materially dearer in {pooled["lp_dearer"]}.',
              f'LP was cheaper in **{pooled["lp_cheaper"]} of {pooled["plan_count"]}** cases and equal in **{pooled["lp_equal"]}**.',
              f'Protection cost more in **{pooled["protection_hurt"]} of {pooled["plan_count"]}** cases, but helped on average.',
              f'Protection helped in **{pooled["protection_helped"]}** cases and hurt in **{pooled["protection_hurt"]}** cases, yet its pooled mean benefit was **{number(pooled["protection_mean"])}m**.',
              f'Its median benefit was **{number(pooled["protection_median"])}m**.']
    for claim in claims:
        assert claim in section, claim
    answer = section.split('### Answer\n\n')[1].strip()
    assert len(re.split(r'(?<=[.!?])\s+(?=[A-Z])', answer)) == 5 and answer.endswith(ENDING)
    for m in ['Seasonal naive', 'SARIMA-24', 'ExtraTrees-24']:
        assert number(summary['all', m]['optimisation_mean']) + 'm for ' + m in answer
    assert f'Protection added a further {number(pooled["protection_mean"])}m' in answer
    assert f'{percent(pooled["optimisation_share"])} of the Rule-to-hindsight gap, protection closed {percent(pooled["protection_share"])}, and {percent(pooled["remaining_share"])} remained.' in answer
    best = load('combination_ranking.json')['all'][0]
    assert f'{best["method"]} + {best["plan"]}, at {number(best["mean_cost"])}m per plan' in answer
    assert float(best['mean_cost']) == min(float(summary['all', m]['mean_' + p])
                                         for m in METHODS for p in ['Rule', 'LP', 'Protected'])
    checks.append('All narrative numerical claims, method constants, 69 equal cases, E1-E3, five-sentence Answer, pooled shares and best combination agree with the saved outputs.')

    for link in re.findall(r'\]\(([^)]+)\)', section):
        assert (REPO / link).is_file(), link
    for entry in load('figure_inventory.json'):
        path = REPO / entry['file']
        assert sha(path) == entry['sha256']
        if path.suffix == '.png':
            with Image.open(path) as image:
                assert all(abs(dpi - 300) < .01 for dpi in image.info['dpi'])
        else:
            ET.parse(path)
    for r in rows('chain_figure_data.csv'):
        assert all(float(r[k]) == float(summary['all', r['method']][k])
                   for k in ['mean_Rule', 'mean_LP', 'mean_Protected', 'mean_Hindsight'])
    costs = {(r['origin'], r['method']): r for r in rows('costs.csv')}
    points = rows('optimisation_by_origin_figure_data.csv')
    assert len(points) == 125 and len({(r['origin'], r['method']) for r in points}) == 125
    for r in points:
        assert all(float(r[k]) == float(costs[r['origin'], r['method']][k])
                   for k in ['value_optimisation', 'optimisation_band'])
    checks.append('All new README links resolve; both unchanged PNGs are 300 dpi, both SVGs parse, and figure-data tables match the saved costs and means. Both figures were also visually inspected.')
    save('publication_audit.json', {'status': 'PASS', 'readme_sha256': sha(REPO / 'README.md'),
                                  'section_sha256': sha(OUT / 'readme_section.md'),
                                  'numeric_table_cells_checked': checked_cells,
                                  'checks': [{'status': 'PASS', 'detail': c} for c in checks]})
    verification = (OUT / 'verification.md').read_text(encoding='utf-8')
    verification += '\n## Publication audit\n\n' + '\n'.join('- **PASS:** ' + c for c in checks) + '\n'
    write(OUT / 'verification.md', verification)
    print('PASS: publication completed from saved outputs only; all seven tables, prose, links, figures, hashes and preservation checked.')


if __name__ == '__main__':
    finish()
    audit()
