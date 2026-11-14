"""Reproduce the unchanged two-origin experiment before any rolling scoring."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts/fair_comparison'))
import fair
import numpy as np

cc = fair.cc
OUT = REPO / 'outputs/fair_comparison_rolling'


def main(data_dir):
    original = REPO / 'outputs/fair_comparison'
    args = SimpleNamespace(data_dir=Path(data_dir).resolve(), out_dir=OUT / 'phase0')
    checks = []
    try:
        fair.check_config(original, args.data_dir)
        cc.verify_hashes(original / 'plans.sha256')
        source = json.loads((args.data_dir / 'SOURCE.json').read_text())
        assert cc.sha256(args.data_dir / source['file']) == source['sha256']
        checks.append(dict(check='Original config, plan and workbook hashes', status='PASS'))
        fair.register(args)
        fair.plan(args)
        fair.score(args)
        for key in fair.ORIGINS:
            old = cc.read_csv(original / key / 'forecasts.csv')
            new = cc.read_csv(args.out_dir / key / 'forecasts.csv')
            assert old[['method', 'month']].equals(new[['method', 'month']])
            diff = float(np.abs(old.filter(like='forecast_').to_numpy() - new.filter(like='forecast_').to_numpy()).max())
            assert diff <= 1e-6, f'{key}: forecast difference {diff}'
            old_tot = cc.read_csv(original / key / 'plans.csv')  # confirms saved month schema
            import pandas as pd
            old_tot = pd.read_csv(original / key / 'totals.csv', float_precision='round_trip')
            new_tot = pd.read_csv(args.out_dir / key / 'totals.csv', float_precision='round_trip')
            select = lambda df: df.loc[(df.kind == 'protected') & (df.penalty == 5000)].set_index('method').loc[list(fair.METHODS), 'total']
            expected, actual = select(old_tot), select(new_tot)
            relative = float(np.abs((actual - expected) / expected).max())
            assert relative <= 1e-6, f'{key}: protected cost relative difference {relative}'
            checks.append(dict(check=key, status='PASS', forecast_max_abs_beds=diff,
                               protected_max_relative_difference=relative, protected_totals=actual.to_dict()))
        result = dict(status='PASS', checks=checks, config_sha256=cc.sha256(args.out_dir / 'config.json'),
                      plans_sha256=cc.sha256(args.out_dir / 'plans_frozen.csv'))
    except BaseException as exc:
        result = dict(status='FAIL', checks=checks, error=f'{type(exc).__name__}: {exc}')
        cc.write_text(OUT / 'phase0_result.json', json.dumps(result, indent=2) + '\n')
        raise
    cc.write_text(OUT / 'phase0_result.json', json.dumps(result, indent=2) + '\n')
    print('PHASE 0 RESULT', json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    main(parser.parse_args().data_dir)
