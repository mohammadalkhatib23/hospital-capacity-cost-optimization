"""Registered rolling comparison, importing the unchanged Fable model and LP code.

Run stages in order: register (historical banks only), plan, score, verify/report.
Later-origin calibration may include earlier-origin outcomes, but every bank is
filtered by its own planning cutoff and no evaluation is performed until freezing.
"""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts/fair_comparison'))
import fair
import numpy as np
import pandas as pd

cc = fair.cc
OUT = REPO / 'outputs/fair_comparison_rolling'
PENALTIES = (2500.0, 5000.0, 10000.0)
LAST_ORIGIN = pd.Timestamp('2026-04-01')
METHODS = list(fair.METHODS)
FABLE = {'2025-09': 'O1_sep2025', '2026-03': 'O2_mar2026'}


def publication_inventory(raw):
    """Inspect presence, not values: ignore trailing date-only template rows.

    The observed span starts/ends where BOTH bed series are non-blank. Every
    month inside that span must be present and complete. Future values are not
    converted or returned to fitting/planning code by this metadata inspection.
    """
    header = raw.iloc[cc.HEADER_ROW].astype(str).str.strip()
    assert header.iloc[1] == 'Month', 'header row moved'
    columns = []
    for label in cc.LABELS.values():
        hits = [i for i, value in enumerate(header) if value == label]
        assert len(hits) == 1, f'expected one {label} column'
        columns.append(hits[0])
    body = raw.iloc[cc.HEADER_ROW + 1:]
    months = pd.to_datetime(body.iloc[:, 1], format='%B %Y', errors='coerce')
    values = body.iloc[:, columns]
    nonblank = values.notna() & values.astype(str).apply(lambda s: s.str.strip().ne(''))
    complete = months.notna() & nonblank.all(axis=1)
    observed = pd.DatetimeIndex(months[complete], name='month')
    assert len(observed), 'no months with both bed observations'
    assert not observed.has_duplicates, 'duplicate observed month'
    expected = pd.date_range(observed.min(), observed.max(), freq='MS')
    assert observed.equals(expected), 'blank or missing month inside observed data span'
    inside = months.between(observed.min(), observed.max())
    assert int(inside.sum()) == len(expected), 'duplicate date label inside observed span'
    assert nonblank.loc[inside].all().all(), 'blank bed value inside observed data span'
    trailing = months.notna() & (months > observed.max())
    assert not nonblank.loc[trailing].any().any(), 'incomplete observation after data end'
    return observed, pd.DatetimeIndex(months[trailing])


def assert_observed_window(observed, start, end):
    expected = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq='MS')
    assert len(expected) and expected.isin(pd.DatetimeIndex(observed)).all(), 'blank/missing month in required window'


def write_json(path, value):
    cc.write_text(path, json.dumps(value, indent=2, allow_nan=False) + '\n')


def read_bank(path):
    frame = cc.read_csv(path)
    frame['backtest_origin'] = frame.backtest_origin.astype(str)
    return frame


def bank_for_origin(master, origin):
    """Only complete paths ending by this origin; never leak a partial path."""
    origin = pd.Timestamp(origin)
    if master.empty:
        return master.copy()
    keys = ['backtest_origin', 'method']
    assert not master.duplicated(keys + ['horizon']).any(), 'duplicate cached horizon'
    for (backtest, method), group in master.groupby(keys, sort=False):
        expected = cc.target_months(pd.Timestamp(backtest))
        group = group.sort_values('horizon')
        assert list(group.horizon) == list(range(1, cc.HORIZON + 1)), 'incomplete cached path'
        assert list(group.month) == list(expected), 'incorrect cached target dates'
    cutoff = origin - pd.DateOffset(months=cc.HORIZON)
    selected = master.loc[pd.to_datetime(master.backtest_origin) <= cutoff].copy()
    if not selected.empty:
        assert selected.month.max() <= origin
        expected_rows = selected.backtest_origin.nunique() * len(METHODS) * cc.HORIZON
        assert len(selected) == expected_rows, 'unequal method banks'
        assert set(selected.method) == set(METHODS)
    return selected.reset_index(drop=True)


def compare_banks(left, right):
    keys = ['backtest_origin', 'method', 'horizon', 'month', 'window_start']
    left, right = (v.sort_values(keys).reset_index(drop=True) for v in (left, right))
    assert left[keys].equals(right[keys]), 'bank identities differ'
    numeric = [c for c in left if c not in keys]
    difference = float(np.abs(left[numeric].to_numpy(float) - right[numeric].to_numpy(float)).max())
    assert difference <= 1e-6, f'bank difference {difference} beds'
    return difference


def verify_source(data_dir):
    source = json.loads((data_dir / 'SOURCE.json').read_text(encoding='utf-8'))
    assert cc.sha256(data_dir / source['file']) == source['sha256'], 'workbook hash mismatch'
    return source


def check_registration(out, data_dir):
    cc.verify_hashes(out / 'config.sha256')
    cc.verify_hashes(out / 'registration.sha256')
    config = json.loads((out / 'config.json').read_text())
    assert verify_source(data_dir)['sha256'] == config['data']['sha256']
    for name, digest in config['source_hashes'].items():
        assert cc.sha256(REPO / name) == digest, f'changed source: {name}'
    return config


def screen_planning_origin(data, origin):
    """Approved pre-score exclusion: check both SARIMA targets, without rescue."""
    window = cc.window(data, origin)
    failures, logs = [], {}
    for target in cc.SERIES:
        try:
            _, logs[target] = cc.sarima_forecast(window[target])
        except SystemExit as exc:
            expected = f'SARIMA-24 did not converge for {target} at {origin:%Y-%m}; stopping'
            if str(exc) != expected:
                raise
            failures.append(dict(target=target, reason=str(exc)))
    return failures, logs


def reregister(data_dir, out=OUT):
    """User-authorized planning-origin convergence rule, before any scoring."""
    import shutil
    old = check_registration(out, data_dir)
    old_hash = cc.sha256(out/'config.json')
    assert old_hash == '1d93530aa10a4a110c3f60f979617d50a7c1f0fc50775657ccecacdf08f2f15d'
    assert not (out/'plans.csv').exists() and not (out/'totals.csv').exists(), 'cannot amend after freezing/scoring'
    archive = out/'superseded_registration_1d93530a'
    assert not archive.exists(), 'earlier registration already archived'
    for name in ['config.json','config.sha256','registration.sha256','origin_inventory.csv',
                 'cache/backtests.csv','cache/dropped_backtests.json','cache_verification.json','failure.json']:
        dest = archive/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(out/name,dest)
    audits, forecast_files, fit_logs, failed = [], [], {}, {}
    for key in old['origins']:
        origin = pd.Timestamp(key)
        data = cc.load(data_dir, cutoff=origin)
        failures, sarima_logs = screen_planning_origin(data, origin)
        audits.append(dict(origin=key, data_cutoff=f'{data.index[-1]:%Y-%m}',
                           training_start=f'{origin-pd.DateOffset(months=23):%Y-%m}',
                           status='excluded' if failures else 'included', failed_targets=[f['target'] for f in failures],
                           failures=failures, sarima=sarima_logs))
        if failures:
            failed[key] = failures
            print('EXCLUDED PLANNING ORIGIN',key,','.join(f['target'] for f in failures),flush=True)
            continue
        fc, fit_logs[key] = cc.forecast_origin(data, origin)
        name = f'planning_forecasts/{key}/forecasts.csv'
        cc.write_csv(out/name,fc)
        forecast_files.append(name)
        print('CONVERGENCE PASS',key,flush=True)
    write_json(out/'planning_convergence.json',audits)
    write_json(out/'planning_forecast_fit_log.json',fit_logs)
    config = old
    for record in config['origin_records']:
        record['failed_targets'] = [f['target'] for f in failed.get(record['origin'],[])]
        if record['origin'] in failed:
            record['status'] = 'excluded'
            record['reason'] = 'SARIMA-24 did not converge for '+', '.join(record['failed_targets'])+' at planning origin; excluded for all methods'
    origins = [r['origin'] for r in config['origin_records'] if r['status']=='included']
    config['origins'] = origins
    config['scopes'] = dict(all=origins, excluding_fable=[o for o in origins if o not in FABLE],
                            through_2024=[o for o in origins if o<='2024-12'],from_2025=[o for o in origins if o>='2025-01'])
    config['subsets'] = {scope:{str(offset):values[offset::6] for offset in range(6)} for scope,values in config['scopes'].items()}
    assert all(v for sets in config['subsets'].values() for v in sets.values())
    config['planning_origin_convergence'] = dict(
        rule='Before scoring, fit unchanged SARIMA-24 separately to occupied and available at every otherwise-eligible planning origin. If either fails to converge, exclude the origin for all five methods. No rescue or model change.',
        excluded=[dict(origin=o,failed_targets=[f['target'] for f in fs]) for o,fs in failed.items()],
        determined_before_scoring=True)
    config['supersedes'] = dict(config_sha256=old_hash,reason='User-authorized planning-origin convergence exclusion, registered before any rolling scoring')
    config['verification'] = dict(cost_relative_tolerance=1e-6,forecast_absolute_tolerance_beds=1e-6,solver_plan_tolerance_beds=1e-3,
                                  rerun_sample_origins=[origins[0],origins[-1]])
    config['source_hashes'].update({p:cc.sha256(REPO/p) for p in ['scripts/fair_comparison_rolling/rolling.py','scripts/fair_comparison_rolling/summarize.py']})
    write_json(out/'config.json',config)
    cc.write_text(out/'config.sha256',f"{cc.sha256(out/'config.json')}  config.json\n")
    inventory = pd.DataFrame(config['origin_records'])
    for col in ['dropped_backtests','failed_targets']:
        inventory[col] = inventory[col].apply(json.dumps)
    cc.write_csv(out/'origin_inventory.csv',inventory)
    names = ['cache/backtests.csv','cache/dropped_backtests.json','cache_verification.json','origin_inventory.csv',
             'planning_convergence.json','planning_forecast_fit_log.json']+forecast_files
    cc.write_text(out/'registration.sha256',''.join(f'{cc.sha256(out/name)}  {name}\n' for name in names))
    write_json(out/'registration_amendment.json',dict(status='PASS',superseded_config_sha256=old_hash,
               final_config_sha256=cc.sha256(out/'config.json'),scoring_started=False,excluded=config['planning_origin_convergence']['excluded']))
    with (out/'verification.md').open('ab') as file:
        file.write((f'\n## Authorized planning-origin convergence rule, before scoring\n\n'
                    f'Registration `{old_hash}` was superseded before any rolling scoring. '
                    f'The unchanged SARIMA was checked on both targets at every candidate origin. '
                    f'An origin with either nonconvergent target is excluded for all five methods. '
                    f'No model specifications changed. Final origins: {len(origins)}. '
                    f'Convergence exclusions: {json.dumps(config["planning_origin_convergence"]["excluded"])}. '
                    f'New config SHA-256: `{cc.sha256(out/"config.json")}`.\n').encode())
    print('FINAL CONFIG SHA256',cc.sha256(out/'config.json'),flush=True)
    print('FINAL ORIGINS',', '.join(origins),flush=True)


def register(data_dir, out=OUT):
    assert json.loads((out / 'phase0_result.json').read_text())['status'] == 'PASS'
    assert not (out / 'config.json').exists(), 'rolling config already registered'
    assert not (out / 'plans.csv').exists(), 'plans already frozen'
    source = verify_source(data_dir)
    # Approved correction: inspect whether both observations exist, not date
    # labels alone. Values of scoring months are not used in this inspection.
    raw = pd.read_excel(data_dir / source['file'], sheet_name=cc.SHEET, header=None)
    observed, placeholders = publication_inventory(raw)
    last_published = observed.max()
    assert last_published == pd.Timestamp('2026-10-01'), 'data end is not October 2026'
    assert last_published == LAST_ORIGIN + pd.DateOffset(months=cc.HORIZON)
    del raw
    known = cc.load(data_dir, cutoff=LAST_ORIGIN)
    first = known.index[0]
    # Original bank builder computes each historical backtest just once here.
    master, dropped = fair.build_bank(known, LAST_ORIGIN)
    for b in fair.backtest_origins(first, LAST_ORIGIN):
        assert_observed_window(observed, b-pd.DateOffset(months=23), b)
        assert_observed_window(observed, cc.target_months(b)[0], cc.target_months(b)[-1])
    cc.write_csv(out / 'cache/backtests.csv', master)
    write_json(out / 'cache/dropped_backtests.json', dropped)
    cache_checks = []
    for origin, key in FABLE.items():
        selected = bank_for_origin(master, origin)
        expected = read_bank(out / 'phase0' / key / 'backtests.csv')
        difference = compare_banks(selected, expected)
        expected_drop = json.loads((out / 'phase0/fit_log.json').read_text())[key]['dropped']
        actual_drop = [d for d in dropped if pd.Timestamp(d['backtest_origin']) <= pd.Timestamp(origin) - pd.DateOffset(months=6)]
        assert actual_drop == expected_drop, 'cached exclusions differ from Fable'
        cache_checks.append(dict(origin=origin, paths=selected.backtest_origin.nunique(), maximum_difference_beds=difference, status='PASS'))
    write_json(out / 'cache_verification.json', cache_checks)
    candidates = pd.date_range(first + pd.DateOffset(months=cc.WINDOW - 1), LAST_ORIGIN, freq='MS')
    records = []
    for origin in candidates:
        assert_observed_window(observed, origin-pd.DateOffset(months=23), origin)
        assert_observed_window(observed, cc.target_months(origin)[0], cc.target_months(origin)[-1])
        bank = bank_for_origin(master, origin)
        n = bank.backtest_origin.nunique()
        dropped_here = [d['backtest_origin'] for d in dropped if pd.Timestamp(d['backtest_origin']) <= origin - pd.DateOffset(months=6)]
        records.append(dict(origin=f'{origin:%Y-%m}', training_start=f'{origin-pd.DateOffset(months=23):%Y-%m}',
                            target_start=f'{cc.target_months(origin)[0]:%Y-%m}', target_end=f'{cc.target_months(origin)[-1]:%Y-%m}',
                            bank_paths=n, dropped_backtests=dropped_here,
                            status='included' if n >= 9 else 'excluded', reason='' if n >= 9 else 'fewer than 9 complete retained paths'))
    origins = [r['origin'] for r in records if r['status'] == 'included']
    assert origins and origins[-1] == '2026-04'
    scopes = dict(all=origins, excluding_fable=[o for o in origins if o not in FABLE],
                  through_2024=[o for o in origins if o <= '2024-12'], from_2025=[o for o in origins if o >= '2025-01'])
    subsets = {scope: {str(r): values[r::6] for r in range(6)} for scope, values in scopes.items()}
    assert all(v for per_scope in subsets.values() for v in per_scope.values()), 'an offset subset is empty'
    inherited = fair.build_config(source)
    tracked_sources = ['scripts/clean_checkpoint/pipeline.py', 'scripts/fair_comparison/fair.py']
    config = dict(name='Fair comparison across many origins', data=inherited['data'],
                  window=24, horizon=6, minimum_paths=9, quantile_level=.9, cost_band=.01, subset_count=6, required_subset_wins=4,
                  first_data_month=f'{first:%Y-%m}', last_published_month=f'{last_published:%Y-%m}',
                  observed_months=[f'{d:%Y-%m}' for d in observed], blank_placeholder_months=[f'{d:%Y-%m}' for d in placeholders],
                  boundary_correction='Approved before rolling scoring: final data month requires both bed series non-blank; assert October 2026 and no internal blanks.',
                  methods=METHODS, origins=origins, origin_records=records, scopes=scopes, subsets=subsets,
                  forecasts=inherited['forecasts'], gap=inherited['gap'], costs=inherited['costs'], limits=inherited['limits'],
                  source_hashes={p: cc.sha256(REPO / p) for p in tracked_sources},
                  original_config_sha256=cc.sha256(REPO / 'outputs/fair_comparison/config.json'),
                  cache_sha256=cc.sha256(out / 'cache/backtests.csv'),
                  error_bank=dict(construction=inherited['error_bank'], minimum_paths=9,
                                  override='user-requested eligibility minimum 9 replaces the original orchestration minimum 20; no model or bank recipe changes',
                                  dropped_backtests=dropped,
                                  sharing='one original build_bank call through April 2026; each origin receives only whole paths ending by its cutoff'),
                  scenarios=inherited['scenarios']['primary'], quantile=inherited['scenarios']['quantile'],
                  plans=dict(point='original point LP at p=5000 only (reference)', protected_penalties=list(PENALTIES),
                             hindsight='original hindsight_plan at each penalty; evaluated only after all practical plans are frozen',
                             tie_break=inherited['plans']['tie_break'], solver=inherited['plans']['solver'],
                             omitted=['bias-corrected point', 'centred SAA', 'service-level plans', 'penalty 1000', 'point penalty sensitivities']),
                  rules=dict(primary_penalty=5000, joint_best='cost <= cheapest protected cost + 0.01 * origin hindsight cost',
                             ranking='ascending mean excess over hindsight over all origins in the reporting scope',
                             subsets='for each scope sorted chronologically, offsets r=0..5 use origin_list[r::6]; gaps from exclusions are retained',
                             clear_winner='unique lowest mean excess, lowest subset mean in at least 4 of 6 subsets, and more wins than losses against overall runner-up',
                             pairwise='a win is cost difference below minus 1% of origin hindsight; a loss above plus 1%; equality at boundaries is a tie',
                             exact_ties='all exactly tied subset minima count as lowest; no unique overall minimum means no clear winner; fixed method order is presentation only',
                             robustness='same clear winner at all three penalties OR same top method at all three; report these separately',
                             quantile_metrics='pinball and coverage at 0.9, actual <= quantile counts as covered; aggregate equally over method-origin-horizon rows',
                             protection='point cost minus protected cost at p=5000; positive means protection lowered cost'),
                  disclosures=['Rules fixed before new scoring, not a blind experiment; earlier outcomes and two-origin results were known.',
                               'Neighbouring origins share up to five target months; offset subsets overlap each other.',
                               'Calibration for a later origin may use earlier origins\' target months, but never its own future outcomes.',
                               'p=10000 is indicative; small banks cannot reliably resolve the 0.95 tail.',
                               'Final publication vintage, not reconstructed real-time publication availability.',
                               'Costs are illustrative model units (validated: False).'])
    config['costs']['shortfall_sweep'] = list(PENALTIES)
    cc.write_csv(out / 'origin_inventory.csv', pd.DataFrame(records).assign(dropped_backtests=lambda df: df.dropped_backtests.apply(json.dumps)))
    write_json(out / 'config.json', config)
    cc.write_text(out / 'config.sha256', f"{cc.sha256(out / 'config.json')}  config.json\n")
    registration_files = ['cache/backtests.csv', 'cache/dropped_backtests.json', 'cache_verification.json', 'origin_inventory.csv']
    cc.write_text(out / 'registration.sha256', ''.join(f'{cc.sha256(out / name)}  {name}\n' for name in registration_files))
    print('REGISTERED CONFIG SHA256', cc.sha256(out / 'config.json'), flush=True)
    print('ORIGINS', ', '.join(origins), flush=True)
    print('BANK SIZES', [r['bank_paths'] for r in records if r['status'] == 'included'], flush=True)
    print('EXCLUSIONS', json.dumps([r for r in records if r['status'] != 'included']), flush=True)


def plan(data_dir, out=OUT):
    config = check_registration(out, data_dir)
    assert not (out / 'plans.csv').exists(), 'plans already frozen'
    master = read_bank(out / 'cache/backtests.csv')
    frozen, files = [], []
    fit_logs = {}
    for key in config['origins']:
        origin = pd.Timestamp(key)
        assert_observed_window(pd.to_datetime(config['observed_months']), origin-pd.DateOffset(months=23), cc.target_months(origin)[-1])
        # Reuse forecasts fitted and hashed by the pre-score convergence screen.
        fc = cc.read_csv(out/f'planning_forecasts/{key}/forecasts.csv')
        fit_logs[key] = json.loads((out/'planning_forecast_fit_log.json').read_text())[key]
        assert fit_logs[key]['data_last_month'] == key
        bank = bank_for_origin(master, origin)
        assert bank.backtest_origin.nunique() >= 9
        scen_rows, quant_rows = [], []
        for method in METHODS:
            g = fc[fc.method == method]
            point = g.forecast_gap.to_numpy()
            errors = fair.error_paths(bank, method)
            scen = fair.scenarios(point, errors)
            backtests = sorted(bank.backtest_origin.unique())
            months = cc.target_months(origin)
            for i, b in enumerate(backtests):
                scen_rows.extend(dict(method=method, backtest_origin=b, horizon=h+1, month=months[h], error_gap=errors[i,h], scenario_gap=scen[i,h]) for h in range(6))
            q = fair.quantile_path(scen, .9)
            quant_rows.extend(dict(method=method, month=months[h], horizon=h+1, rank=fair.rank(len(scen), .9), quantile_gap=q[h], point_gap=point[h]) for h in range(6))
            choices = [('point', 5000., cc.lp_plan(point)[0])]
            choices.extend(('protected', p, fair.saa_plan(scen, p)) for p in PENALTIES)
            for kind, p, x in choices:
                a, r = cc.flows(x)
                frozen.extend(dict(origin=key, plan=fair.plan_id(method, kind, p), method=method, kind=kind,
                                   penalty=p, month=months[h], horizon=h+1, x=x[h], a=a[h], r=r[h]) for h in range(6))
        for name, frame in [('forecasts', fc), ('backtests', bank), ('scenarios', pd.DataFrame(scen_rows)), ('quantiles', pd.DataFrame(quant_rows))]:
            filename = f'{key}/{name}.csv'
            cc.write_csv(out / filename, frame)
            files.append(filename)
        print('PLANNED', key, 'bank paths', bank.backtest_origin.nunique(), flush=True)
    cc.write_csv(out / 'plans.csv', pd.DataFrame(frozen))
    write_json(out / 'fit_log.json', fit_logs)
    names = ['plans.csv', 'fit_log.json'] + files
    cc.write_text(out / 'plans.sha256', ''.join(f'{cc.sha256(out / name)}  {name}\n' for name in names))
    print('FROZEN PLANS SHA256', cc.sha256(out / 'plans.csv'), flush=True)


def score(data_dir, out=OUT):
    config = check_registration(out, data_dir)
    cc.verify_hashes(out / 'plans.sha256')
    assert not (out / 'totals.csv').exists(), 'already scored'
    frozen = cc.read_csv(out / 'plans.csv')
    # This is the first new scoring access to evaluation outcomes. Historical
    # errors already used by later-origin calibration remain cutoff-restricted.
    data = cc.load(data_dir, cutoff=pd.Timestamp(config['last_published_month']))
    totals, monthly, accuracy, horizon_rows = [], [], [], []
    for key in config['origins']:
        origin = pd.Timestamp(key)
        months = cc.target_months(origin)
        actual = data.loc[months]
        assert len(actual) == 6 and not actual.isna().any().any(), 'blank target observation'
        gaps = cc.gap(actual.occupied, actual.available).to_numpy()
        hindsight = {}
        for p in PENALTIES:
            x = fair.hindsight_plan(gaps, p)
            row, table = fair.score_plan(x, gaps, p)
            hindsight[p] = row['total']
            totals.append(dict(origin=key, method='Hindsight', kind='hindsight', penalty=p, **row, hindsight=row['total'], excess_over_hindsight=0.))
            monthly.append(table.assign(shortfall_cost=p*table.shortfall, origin=key, method='Hindsight', kind='hindsight', penalty=p, month=months, horizon=range(1,7)))
        for (method, kind, p), group in frozen[frozen.origin == key].groupby(['method', 'kind', 'penalty'], sort=False):
            group = group.sort_values('month')
            assert list(group.month) == list(months)
            row, table = fair.score_plan(group.x.to_numpy(), gaps, p)
            totals.append(dict(origin=key, method=method, kind=kind, penalty=p, **row, hindsight=hindsight[p], excess_over_hindsight=row['total']-hindsight[p]))
            monthly.append(table.assign(shortfall_cost=p*table.shortfall, origin=key, method=method, kind=kind, penalty=p, month=months, horizon=range(1,7)))
        fc = cc.read_csv(out / key / 'forecasts.csv')
        quant = cc.read_csv(out / key / 'quantiles.csv')
        for method in METHODS:
            g = fc[fc.method == method].sort_values('month')
            q = quant[quant.method == method].sort_values('month').quantile_gap.to_numpy()
            err = gaps - g.forecast_gap.to_numpy()
            accuracy.append(dict(origin=key, method=method, gap_mae=float(np.abs(err).mean()), gap_mean_signed_error=float(err.mean()),
                                 occupied_mae=float(np.abs(actual.occupied.to_numpy()-g.forecast_occupied.to_numpy()).mean()),
                                 available_mae=float(np.abs(actual.available.to_numpy()-g.forecast_available.to_numpy()).mean()),
                                 pinball_090=fair.pinball(gaps, q, .9), covered_090=int((gaps <= q).sum()), months=6))
            horizon_rows.extend(dict(origin=key, method=method, horizon=h+1, month=months[h], actual_gap=gaps[h],
                                     point_gap=g.forecast_gap.iloc[h], quantile_gap=q[h], covered_090=bool(gaps[h]<=q[h]),
                                     pinball_090=fair.pinball([gaps[h]], [q[h]], .9)) for h in range(6))
        cc.write_csv(out / key / 'actuals.csv', actual.reset_index().assign(gap=gaps))
    for name, frame in [('totals', pd.DataFrame(totals)), ('monthly_costs', pd.concat(monthly, ignore_index=True)),
                        ('accuracy', pd.DataFrame(accuracy)), ('horizon_accuracy', pd.DataFrame(horizon_rows))]:
        cc.write_csv(out / f'{name}.csv', frame)
        for key, group in frame.groupby('origin',sort=False):
            cc.write_csv(out/key/f'{name}.csv',group)
    cc.verify_hashes(out / 'plans.sha256')
    print('SCORING COMPLETE: frozen hashes unchanged', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['register', 'reregister', 'plan', 'score'])
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        globals()[args.stage](args.data_dir.resolve())
    except BaseException as exc:
        write_json(OUT / 'failure.json', dict(stage=args.stage, error=f'{type(exc).__name__}: {exc}'))
        raise
