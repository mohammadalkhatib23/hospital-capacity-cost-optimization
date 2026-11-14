"""Apply the registered rules to saved results, without fitting or planning."""
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
from rolling import OUT, METHODS, PENALTIES, cc, write_json


def pairwise_counts(left, right, bands):
    diff = np.asarray(left) - np.asarray(right)
    bands = np.asarray(bands)
    return dict(wins=int((diff < -bands).sum()), ties=int((np.abs(diff) <= bands).sum()), losses=int((diff > bands).sum()))


def decide(costs, hindsight, methods=METHODS):
    """Rows are chronological origins; columns are methods, all at one penalty."""
    assert list(costs.columns) == list(methods)
    assert len(costs) >= 6 and costs.notna().all().all()
    excess = costs.sub(hindsight, axis=0)
    means = excess.mean()
    ranking = sorted(methods, key=lambda m: (float(means[m]), methods.index(m)))
    top, runner = ranking[:2]
    subset = pd.DataFrame([excess.iloc[r::6].mean() for r in range(6)], index=range(6))
    subset_wins = {m: int((subset[m] == subset.min(axis=1)).sum()) for m in methods}
    wtl = pairwise_counts(costs[top], costs[runner], .01*hindsight)
    unique = int((means == means.min()).sum()) == 1
    clear = top if unique and subset_wins[top] >= 4 and wtl['wins'] > wtl['losses'] else None
    return dict(top=top, runner_up=runner, ranking=ranking, clear_winner=clear,
                mean_excess=means.to_dict(), subset_wins=subset_wins,
                wins_ties_losses_vs_runner=wtl, unique_aggregate_minimum=unique), subset


def summarize(out=OUT):
    out = Path(out)
    cc.verify_hashes(out/'config.sha256')
    cc.verify_hashes(out/'plans.sha256')
    config = json.loads((out/'config.json').read_text())
    totals = pd.read_csv(out/'totals.csv', float_precision='round_trip')
    accuracy = pd.read_csv(out/'accuracy.csv', float_precision='round_trip')
    horizon = pd.read_csv(out/'horizon_accuracy.csv', float_precision='round_trip')
    assert set(horizon.covered_090.unique()).issubset({True, False})
    summaries, subset_rows, ranks, pairs, coverage_rows = [], [], [], [], []
    decisions = dict(scopes={})
    for scope, origins in config['scopes'].items():
        scope_acc = accuracy[accuracy.origin.isin(origins)]
        scope_h = horizon[horizon.origin.isin(origins)]
        assert len(scope_acc) == len(origins)*5 and len(scope_h) == len(origins)*5*6
        scope_result = dict(origins=origins, penalties={})
        for method in METHODS:
            for h, group in scope_h[scope_h.method == method].groupby('horizon'):
                coverage_rows.append(dict(scope=scope, method=method, horizon=int(h),
                                          covered=int(group.covered_090.sum()), months=len(group), coverage=float(group.covered_090.mean())))
        for penalty in PENALTIES:
            t = totals[totals.origin.isin(origins) & (totals.penalty == penalty)]
            protected = t[t.kind == 'protected'].pivot(index='origin', columns='method', values='total').loc[origins, METHODS]
            hindsight = t[t.kind == 'hindsight'].set_index('origin').total.loc[origins]
            outcome, subset = decide(protected, hindsight)
            scope_result['penalties'][str(int(penalty))] = outcome
            excess = protected.sub(hindsight, axis=0)
            for offset in range(6):
                selected = origins[offset::6]
                assert selected == config['subsets'][scope][str(offset)]
                for method in METHODS:
                    subset_rows.append(dict(scope=scope, penalty=penalty, offset=offset, method=method, origins=len(selected),
                                            origin_list=';'.join(selected), mean_excess_over_hindsight=float(subset.loc[offset, method]),
                                            lowest=bool(subset.loc[offset, method] == subset.loc[offset].min())))
            for origin in origins:
                values = protected.loc[origin]
                band = .01*hindsight.loc[origin]
                for method in METHODS:
                    ranks.append(dict(scope=scope, penalty=penalty, origin=origin, method=method, total=float(values[method]),
                                      hindsight=float(hindsight.loc[origin]), excess_over_hindsight=float(excess.loc[origin,method]),
                                      cost_band=float(band), rank=int(values.rank(method='min')[method]),
                                      joint_best=bool(values[method] <= values.min()+band)))
            for method in METHODS:
                acc = scope_acc[scope_acc.method == method]
                wtl = pairwise_counts(protected[method], protected['Seasonal naive'], .01*hindsight)
                value = np.nan
                if penalty == 5000:
                    point = t[(t.kind == 'point') & (t.method == method)].set_index('origin').total.loc[origins]
                    value = float((point-protected[method]).mean())
                summaries.append(dict(scope=scope, penalty=penalty, method=method, origins=len(origins),
                                      mean_excess_over_hindsight=float(excess[method].mean()),
                                      joint_best_count=int((protected[method] <= protected.min(axis=1)+.01*hindsight).sum()),
                                      wins_vs_seasonal_naive=wtl['wins'], ties_vs_seasonal_naive=wtl['ties'], losses_vs_seasonal_naive=wtl['losses'],
                                      mean_pinball_090=float(acc.pinball_090.mean()), covered_090=int(acc.covered_090.sum()),
                                      months=int(acc.months.sum()), coverage_090=float(acc.covered_090.sum()/acc.months.sum()),
                                      mean_value_of_protection=value, subset_lowest_count=outcome['subset_wins'][method],
                                      subset_min_mean=float(subset[method].min()), subset_max_mean=float(subset[method].max())))
                for other in METHODS:
                    pairs.append(dict(scope=scope, penalty=penalty, method=method, comparator=other,
                                      **pairwise_counts(protected[method],protected[other],.01*hindsight),
                                      mean_cost_advantage=float((protected[other]-protected[method]).mean())))
        top = {p: entry['top'] for p,entry in scope_result['penalties'].items()}
        clear = {p: entry['clear_winner'] for p,entry in scope_result['penalties'].items()}
        same_top = len(set(top.values())) == 1
        same_clear = len(set(clear.values())) == 1 and all(clear.values())
        scope_result['robustness'] = dict(top_methods=top, clear_winners=clear, same_top_method=same_top,
                                          same_clear_winner=same_clear, robust=bool(same_top or same_clear))
        decisions['scopes'][scope] = scope_result
    for name, rows in [('summary',summaries),('subset_means',subset_rows),('per_origin_ranks',ranks),('pairwise',pairs),('coverage_by_horizon',coverage_rows)]:
        cc.write_csv(out/f'{name}.csv', pd.DataFrame(rows))
    write_json(out/'decisions.json', decisions)
    print(json.dumps(decisions['scopes']['all'],indent=2), flush=True)
    return decisions


if __name__ == '__main__':
    summarize()
