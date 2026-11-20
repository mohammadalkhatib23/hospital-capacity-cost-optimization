"""Toy forecasts and matched paths test the arithmetic without any study outcomes."""
import numpy as np
import pandas as pd
import pytest

from experiment import METHODS, cc, combine_bank, combine_forecasts, members, names


def toy_forecasts(partial):
    rows = []
    for method in members(partial):
        i = METHODS.index(method)
        for h in range(1, 7):
            occupied, available = 90 + 9 * i + h, 80 + 2 * i
            rows.append(dict(method=method, month=f"2020-{h:02d}-01", horizon=h,
                             forecast_occupied=occupied, forecast_available=available,
                             forecast_gap=cc.gap(occupied, available)))
    return pd.DataFrame(rows).sample(frac=1, random_state=4)


def toy_bank():
    rows = []
    for i, method in enumerate(METHODS):
        for b, origin in enumerate(["2018-01", "2019-01"]):
            for h in range(1, 7):
                e_occ, e_av = 9 * i + h + b, i - h + 3 * b
                rows.append(dict(method=method, backtest_origin=origin, month=f"{origin[:4]}-{h+1:02d}-01",
                                 horizon=h, actual_occupied=90., actual_available=82.,
                                 forecast_occupied=90-e_occ, forecast_available=82-e_av,
                                 error_occupied=e_occ, error_available=e_av, error_gap=cc.gap(e_occ, e_av)))
    return pd.DataFrame(rows).sample(frac=1, random_state=2)


@pytest.mark.parametrize("partial", [False, True])
def test_forecasts_and_matched_error_bank(partial):
    forecasts = toy_forecasts(partial)
    combined = combine_forecasts(forecasts, partial)
    avg, median = names(partial)
    i = np.mean([METHODS.index(m) for m in members(partial)])
    h = np.arange(1, 7)
    g = combined[combined.method == avg].sort_values("horizon")
    np.testing.assert_allclose(g.forecast_occupied, 90 + 9*i + h)
    np.testing.assert_allclose(g.forecast_available, 80 + 2*i)
    np.testing.assert_allclose(g.forecast_gap, (90 + 9*i + h)/.9 - (80 + 2*i))
    np.testing.assert_allclose(combined[combined.method == median].sort_values("horizon").forecast_gap,
                               forecasts.groupby("horizon").forecast_gap.median())
    bank = combine_bank(toy_bank(), partial)
    assert set(bank.method) == set(members(partial)) | {avg}
    for b, (_, rows) in enumerate(bank[bank.method == avg].groupby("backtest_origin")):
        rows = rows.sort_values("horizon")
        np.testing.assert_allclose(rows.error_occupied, 9*i + h + b)
        np.testing.assert_allclose(rows.error_available, i - h + 3*b)
        np.testing.assert_allclose(rows.error_gap, (9*i + h + b)/.9 - (i - h + 3*b))


def test_missing_backtest_is_rejected():
    bank = toy_bank()
    missing = (bank.method == "Seasonal naive") & (bank.backtest_origin == "2018-01")
    with pytest.raises(AssertionError, match="unmatched"):
        combine_bank(bank[~missing])
