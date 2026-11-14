"""Unit tests for scripts/fair_comparison/fair.py on hand examples and toy series. Run: pytest scripts/fair_comparison"""

from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import pytest

import fair as f

cc = f.cc


# --------------------------------------------------------------------------------------- scenario construction

@pytest.fixture(scope="module")
def toy_bank():
    """36 months of two noisy seasonal series; the planning origin is the last month, so 36 - 24 - 6 + 1 = 7 backtests."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2021-01-01", periods=36, freq="MS")
    season = np.sin(2 * np.pi * np.arange(36) / 12)
    data = pd.DataFrame(dict(occupied=94_000 + 900 * season + 30 * np.arange(36) + rng.normal(0, 120, 36),
                             available=100_000 + 600 * season + 20 * np.arange(36) + rng.normal(0, 120, 36)), index=idx)
    bank, dropped = f.build_bank(data, idx[-1])
    return data, bank, dropped


def test_backtests_cover_every_usable_origin_and_stay_before_the_planning_origin(toy_bank):
    data, bank, dropped = toy_bank
    origin = data.index[-1]
    assert not dropped and bank.backtest_origin.nunique() == 7
    assert sorted(bank.backtest_origin.unique())[0] == "2022-12-01" and sorted(bank.backtest_origin.unique())[-1] == "2023-06-01"
    assert bank.month.max() == origin and (bank.month > pd.to_datetime(bank.backtest_origin)).all()
    assert (pd.to_datetime(bank.window_start) >= data.index[0]).all()
    assert len(bank) == 7 * 5 * 6  # backtests x methods x horizons


def test_backtest_errors_are_actual_minus_forecast_and_paired(toy_bank):
    data, bank, _ = toy_bank
    g = bank[bank.method == "Persistence"]
    for row in g.itertuples():
        b = pd.Timestamp(row.backtest_origin)
        assert row.error_occupied == pytest.approx(data.occupied[row.month] - data.occupied[b])  # persistence repeats the origin month
        assert row.error_available == pytest.approx(data.available[row.month] - data.available[b])
    assert np.allclose(bank.error_gap, bank.error_occupied / 0.90 - bank.error_available)
    paths = f.error_paths(bank, "Persistence")
    assert paths.shape == (7, 6)
    first = g[g.backtest_origin == "2022-12-01"].sort_values("horizon").error_gap.to_numpy()
    assert np.allclose(paths[0], first)  # one path per backtest, horizons kept together


def test_backtest_refuses_a_frame_that_runs_past_the_origin(toy_bank):
    data, _, _ = toy_bank
    with pytest.raises(AssertionError):
        f.build_bank(data, data.index[-2])


def test_scenarios_add_error_paths_to_the_point_forecast():
    point = np.array([100.0, 200.0])
    errors = np.array([[10.0, -20.0], [30.0, 0.0], [-10.0, 50.0]])
    assert f.scenarios(point, errors).tolist() == [[110, 180], [130, 200], [90, 250]]
    centred = f.scenarios(point, errors, centred=True)  # mean error path (10, 10) removed
    assert centred.tolist() == [[100, 170], [120, 190], [80, 240]] and np.allclose(centred.mean(axis=0), point)


def test_quantile_rank_rule():
    assert (f.rank(29, 0.9), f.rank(35, 0.9)) == (27, 33)
    assert (f.rank(29, 0.95), f.rank(35, 0.95)) == (29, 35)  # the largest past error at both origins
    assert (f.rank(29, 0.5), f.rank(35, 0.5), f.rank(29, 0.8), f.rank(35, 0.8)) == (15, 18, 24, 29)
    assert f.rank(9, 0.9) == 9 and f.rank(8, 0.9) == 8  # capped at n when the rank would exceed it
    scen = np.arange(1.0, 10.0)[:, None] * np.array([[1.0, 10.0]])  # 9 scenarios, two months
    assert f.quantile_path(scen, 0.5).tolist() == [5, 50] and f.quantile_path(scen, 0.9).tolist() == [9, 90]


# ----------------------------------------------------------------------------------------------------- pinball

def test_pinball_loss_hand_example():
    assert f.pinball([100], [90], 0.9) == pytest.approx(9.0)   # 10 beds short, weighted 0.9
    assert f.pinball([100], [110], 0.9) == pytest.approx(1.0)  # 10 beds spare, weighted 0.1
    assert f.pinball([100, 100], [90, 110], 0.9) == pytest.approx(5.0)
    assert f.pinball([100, 100], [90, 110], 0.5) == pytest.approx(5.0)  # half the absolute error at 0.5


def test_monthly_cost_is_running_the_gap_plus_five_thousand_times_pinball():
    for d, x in ((3000.0, 2800.0), (3000.0, 3300.0)):
        cost = 500 * x + 5000 * max(d - x, 0)
        assert cost == pytest.approx(500 * d + 5000 * f.pinball([d], [x], 0.9))


def test_crps_hand_example():
    assert f.crps([1.0], [[4.0]]) == pytest.approx(3.0)        # one scenario: the absolute error
    assert f.crps([1.0], [[0.0], [2.0]]) == pytest.approx(0.5)  # E|X - y| = 1, E|X - X'| = 1


# ----------------------------------------------------------------------------------------------- service level

def test_service_level_plan_meets_the_requirement_at_least_cost():
    x = f.service_plan([100, 0, 100])
    assert x == pytest.approx([100, 100, 100], abs=1e-6)  # holding through a one-month dip is cheaper
    x = f.service_plan([100, 0, 0, 100])
    assert x == pytest.approx([100, 0, 0, 100], abs=1e-6)
    x = f.service_plan([0, 300, 200])
    assert (x >= np.array([0, 300, 200]) - 1e-6).all() and x == pytest.approx([0, 300, 200], abs=1e-6)


def test_service_requirement_uses_the_quantile_and_is_lowered_to_what_is_reachable():
    scen = np.array([[k * 1000.0, -50.0] for k in range(1, 10)])  # month 1: 1000..9000; month 2: always negative
    required, quantile = f.service_requirement(scen, 0.10)
    assert quantile.tolist() == [9000, -50]
    assert required.tolist() == [5000, 0]  # month 1 cannot exceed the 5,000 ramp from zero; month 2 is not below zero
    assert f.reachable(4).tolist() == [5000, 10000, 15000, 15000]
    assert f.service_plan(required) == pytest.approx([5000, 0], abs=1e-6)


# --------------------------------------------------------------------------------------------------------- SAA

def test_saa_with_identical_scenarios_reproduces_the_point_plan():
    rng = np.random.default_rng(1)
    for _ in range(5):
        point = rng.uniform(500, 6000, 6)
        assert f.saa_plan(np.tile(point, (7, 1)), f.BASE) == pytest.approx(cc.lp_plan(point)[0], abs=1e-3)
        assert f.saa_plan(point[None, :], f.BASE) == pytest.approx(cc.lp_plan(point)[0], abs=1e-3)


def test_saa_single_month_is_the_newsvendor_quantile():
    scen = np.arange(100.0, 1100.0, 100.0)[:, None]  # ten scenarios: 100, 200, ..., 1000
    # raising x by one bed costs 500 + 550 and saves 5000 / 10 for each scenario still above x: stop when at most 2 are above
    assert f.saa_plan(scen, 5000.0) == pytest.approx([800], abs=1e-6)
    assert f.saa_plan(scen, 1000.0) == pytest.approx([0], abs=1e-6)  # 10 scenarios x 100 < 1,050: never worth opening


def test_saa_ties_resolve_to_the_smaller_plan():
    scen = np.arange(100.0, 1100.0, 100.0)[:, None]
    # at p = 5,250 every x between 800 and 900 costs the same (2 scenarios above x 525 = 1,050)
    assert f.saa_plan(scen, 5250.0) == pytest.approx([800], abs=1e-6)


def test_saa_is_never_worse_than_the_point_plan_on_its_own_scenarios():
    rng = np.random.default_rng(2)
    for _ in range(5):
        point, errors = rng.uniform(2000, 5000, 6), rng.normal(100, 500, (29, 6))
        scen = f.scenarios(point, errors)
        saa, pt = f.saa_plan(scen, f.BASE), cc.lp_plan(point)[0]
        assert f.average_cost(saa, scen, f.BASE) <= f.average_cost(pt, scen, f.BASE) * (1 + 1e-9)
        assert (saa >= -1e-9).all() and (saa <= cc.CAP).all() and np.abs(np.diff(np.r_[0, saa])).max() <= cc.RAMP + 1e-6


def test_score_plan_uses_the_given_penalty():
    row, table = f.score_plan([100, 100], [150, 80], 1000.0)
    assert row["operating"] == 500 * 200 + 550 * 100 and row["shortfall_bed_months"] == 50 and row["short_months"] == 1
    assert row["total"] == row["operating"] + 1000 * 50 and table.monthly_cost.sum() == pytest.approx(row["total"])


# ------------------------------------------------------------------------------------------------------- rules

HIND = {"O1": 100.0, "O2": 100.0}  # tie band 1.0


def table(values):
    return pd.DataFrame(values, index=list(f.METHODS), columns=["O1", "O2"], dtype=float)


def test_protection_effect_uses_the_tie_band():
    assert f.protection_effect(120, 110, 1.0) == "helped"
    assert f.protection_effect(110, 120, 1.0) == "hurt"
    assert f.protection_effect(110.5, 110, 1.0) == "no material difference"


def test_previous_winner_still_wins_only_if_its_protected_plan_is_joint_best():
    point = pd.Series([150, 105, 140, 130, 120], index=list(f.METHODS), dtype=float)  # Persistence has the cheapest point plan
    protected = pd.Series([125, 118, 112, 111.5, 119], index=list(f.METHODS), dtype=float)
    r = f.previous_winner(point, protected, 100.0)
    assert r == dict(method="Persistence", still_wins=False, protected_rank=3, protected_joint_best=["Seasonal naive", "SARIMA-24"])
    protected["Persistence"] = 112.2
    assert f.previous_winner(point, protected, 100.0)["still_wins"]


def test_winner_outcome_labels():
    clear = f.winner_outcome(table([[150, 150], [140, 140], [130, 130], [110, 112], [125, 125]]), HIND)
    assert clear["outcome"] == "SARIMA-24" and clear["first"] == "SARIMA-24" and clear["combined_excess"]["SARIMA-24"] == 22
    none = f.winner_outcome(table([[150, 150], [140, 105], [130, 130], [110, 140], [125, 125]]), HIND)
    assert none["outcome"] == "no clear winner" and none["joint_best"] == {"O1": ["SARIMA-24"], "O2": ["Persistence"]}


def test_improvement_measures_hand_example():
    costs = dict(point=table([[200, 200], [300, 150], [180, 260], [170, 240], [190, 190]]),
                 bias_corrected=table([[190, 190], [280, 160], [170, 230], [165, 220], [185, 185]]),
                 protected=table([[180, 180], [250, 170], [150, 200], [140, 160], [175, 175]]))
    losses = dict(pinball_090=table([[90, 90], [120, 60], [50, 100], [40, 60], [70, 70]]), gap_mae=table([[700, 300], [900, 500], [300, 600], [240, 400], [450, 520]]))
    m = f.improvements(costs, HIND, losses).set_index(["scope", "method"])
    o1, both = m.loc[("O1", "SARIMA-24")], m.loc[("combined", "SARIMA-24")]
    assert (o1.value_of_protection, o1.value_of_bias_correction, o1.value_of_margin) == (30, 5, 25)
    assert o1.saving_vs_seasonal_naive == 10 and o1.saving_vs_persistence == 110
    assert o1.share_avoidable_vs_seasonal_naive == pytest.approx(20.0) and o1.share_avoidable_vs_persistence == pytest.approx(100 * 110 / 150)
    assert o1.excess_over_hindsight == 40 and o1.pinball_090_skill_vs_seasonal_naive == pytest.approx(0.2)
    assert both.value_of_protection == 110 and both.saving_vs_seasonal_naive == 50
    assert both.share_avoidable_vs_seasonal_naive == pytest.approx(100 * 50 / 150)  # ratio of sums
    assert both.pinball_090_skill_vs_seasonal_naive == pytest.approx(1 - 50 / 75) and both.gap_mae_skill_vs_seasonal_naive == pytest.approx(1 - 320 / 450)
