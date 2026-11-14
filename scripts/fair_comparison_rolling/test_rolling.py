"""Tests of the approved blank-row correction and whole-path shared cache."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rolling as r
import numpy as np
import pandas as pd
import pytest


def toy_raw():
    raw = pd.DataFrame([[None]*4 for _ in range(r.cc.HEADER_ROW+1+8)])
    raw.iloc[r.cc.HEADER_ROW] = [None, 'Month', 'G&A beds available', 'G&A beds occupied']
    for i, month in enumerate(pd.date_range('2026-08-01', periods=8, freq='MS')):
        raw.iloc[r.cc.HEADER_ROW+1+i] = [None, month.strftime('%B %Y'), 100 if i < 3 else None, 90 if i < 3 else None]
    return raw


def test_trailing_blank_template_rows_do_not_extend_publication():
    observed, trailing = r.publication_inventory(toy_raw())
    assert observed.max() == pd.Timestamp('2026-10-01')
    assert list(observed) == list(pd.date_range('2026-08-01', '2026-10-01', freq='MS'))
    assert list(trailing) == list(pd.date_range('2026-11-01', '2027-03-01', freq='MS'))
    r.assert_observed_window(observed, '2026-08', '2026-10')
    with pytest.raises(AssertionError, match='required window'):
        r.assert_observed_window(observed, '2026-09', '2026-11')


@pytest.mark.parametrize('column', [2, 3])
def test_internal_blank_in_either_series_is_rejected(column):
    raw = toy_raw()
    raw.iloc[r.cc.HEADER_ROW+2, column] = None
    with pytest.raises(AssertionError, match='blank or missing'):
        r.publication_inventory(raw)


def test_partial_final_observation_is_not_silently_discarded():
    raw = toy_raw()
    raw.iloc[r.cc.HEADER_ROW+4, 2] = 100
    with pytest.raises(AssertionError, match='incomplete observation after'):
        r.publication_inventory(raw)


def test_whitespace_placeholder_cells_are_blank():
    raw = toy_raw()
    raw.iloc[-1, 2:4] = '  '
    observed, _ = r.publication_inventory(raw)
    assert observed[-1] == pd.Timestamp('2026-10-01')


def test_convergence_screen_checks_both_targets_without_rescue(monkeypatch):
    idx=pd.date_range('2024-01-01',periods=24,freq='MS')
    data=pd.DataFrame({'occupied':90.,'available':100.},index=idx)
    seen=[]
    def nonconvergent(y):
        seen.append(y.name)
        raise SystemExit(f'SARIMA-24 did not converge for {y.name} at {y.index[-1]:%Y-%m}; stopping')
    monkeypatch.setattr(r.cc,'sarima_forecast',nonconvergent)
    failures,logs=r.screen_planning_origin(data,idx[-1])
    assert seen==['occupied','available']
    assert [f['target'] for f in failures]==seen and logs=={}


def test_convergence_screen_does_not_hide_other_failures(monkeypatch):
    idx=pd.date_range('2024-01-01',periods=24,freq='MS')
    data=pd.DataFrame({'occupied':90.,'available':100.},index=idx)
    def other_error(y):
        raise SystemExit('unexpected model failure')
    monkeypatch.setattr(r.cc,'sarima_forecast',other_error)
    with pytest.raises(SystemExit,match='unexpected model failure'):
        r.screen_planning_origin(data,idx[-1])


def test_shared_cache_matches_original_banks_without_repeated_fits(monkeypatch):
    idx = pd.date_range('2020-01-01', periods=38, freq='MS')
    data = pd.DataFrame({'occupied': np.arange(38)+90., 'available': np.arange(38)+100.}, index=idx)
    calls = []
    def fake_forecast(frame, origin):
        assert frame.index[-1] == origin
        r.cc.window(frame, origin)
        calls.append(origin)
        if origin == idx[25]:
            raise SystemExit('SARIMA-24 did not converge for available; stopping')
        months = r.cc.target_months(origin)
        return pd.DataFrame([dict(method=m, month=d, forecast_occupied=frame.occupied.iloc[-1],
                                  forecast_available=frame.available.iloc[-1],
                                  forecast_gap=r.cc.gap(frame.occupied.iloc[-1],frame.available.iloc[-1]))
                             for m in r.METHODS for d in months]), {}
    monkeypatch.setattr(r.cc, 'forecast_origin', fake_forecast)
    master, drops = r.fair.build_bank(data, idx[-1])
    assert len(calls) == len(set(calls))
    count = len(calls)
    selected = [r.bank_for_origin(master, origin) for origin in (idx[-4], idx[-1])]
    assert len(calls) == count, 'bank selection refitted a backtest'
    assert len(drops) == 1
    for origin, cached in zip((idx[-4], idx[-1]), selected):
        direct, _ = r.fair.build_bank(data.loc[:origin], origin)
        assert r.compare_banks(cached, direct) == 0
        assert cached.month.max() <= origin
        assert len(cached) == cached.backtest_origin.nunique()*5*6
        assert drops[0]['backtest_origin'] not in cached.backtest_origin.str[:7].unique()
    with pytest.raises(AssertionError, match='incomplete cached path'):
        r.bank_for_origin(master.iloc[:-1], idx[-1])
