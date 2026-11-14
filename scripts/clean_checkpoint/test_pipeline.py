"""Unit tests for scripts/clean_checkpoint/pipeline.py on hand examples and toy series. Run: pytest scripts/clean_checkpoint"""

from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import pytest

import pipeline as p


def toy(values, start="2024-01-01"):
    return pd.Series(np.asarray(values, dtype=float), index=pd.date_range(start, periods=len(values), freq="MS"), name="toy")


# ------------------------------------------------------------------------------------------------ gap and cost

def test_gap_formula_hand_example():
    assert p.gap(95.0, 100.0) == pytest.approx(5.5556, abs=5e-5)


def test_cost_accounting_hand_example():
    t = p.cost_table([100, 150, 50], [120, 100, 80])
    assert t.a.tolist() == [100, 50, 0] and t.r.tolist() == [0, 0, 100]
    assert t.shortfall.tolist() == [20, 0, 30] and t.spare.tolist() == [0, 50, 0]
    # month 1: 500*100 + 550*100 + 5000*20; month 2: 500*150 + 550*50; month 3: 500*50 + 300*100 + 5000*30
    assert t.monthly_cost.tolist() == [205_000, 102_500, 205_000]
    total = p.totals_row(t)
    assert total["operating"] == 262_500 and total["shortfall_cost"] == 250_000 and total["total"] == 512_500
    assert total["shortfall_bed_months"] == 50


def test_no_shortfall_deadband():
    assert p.cost_table([100], [100.01]).shortfall_cost.iloc[0] == pytest.approx(50.0)


# -------------------------------------------------------------------------------------------------------- Rule

def test_rule_follows_forecast_with_a_ramp_limited_month():
    # month 1 cannot exceed the 5,000 ramp from zero; month 3 has a negative gap; month 4 is ramp-limited again
    assert p.rule_plan([6000, 2000, -100, 9000]).tolist() == [5000, 2000, 0, 5000]


def test_rule_respects_cap_and_downward_ramp():
    assert p.rule_plan([5000, 10000, 20000, 20000]).tolist() == [5000, 10000, 15000, 15000]
    assert p.rule_plan([5000, 10000, 0, 0]).tolist() == [5000, 10000, 5000, 0]


# ---------------------------------------------------------------------------------------------------------- LP

def test_lp_keeps_beds_open_through_a_one_month_dip():
    # keeping 100 beds for the dip month costs 500 each; closing and reopening costs 300 + 550
    x, objective = p.lp_plan([100, 0, 100])
    assert x == pytest.approx([100, 100, 100], abs=1e-6)
    assert objective == pytest.approx(500 * 300 + 550 * 100)
    rule = p.totals_row(p.cost_table(p.rule_plan([100, 0, 100]), [100, 0, 100]))["total"]
    assert rule == pytest.approx(500 * 200 + 550 * 200 + 300 * 100) and objective < rule


def test_lp_closes_and_reopens_for_a_two_month_dip():
    # two idle months cost 1,000 per bed, more than 300 + 550
    x, objective = p.lp_plan([100, 0, 0, 100])
    assert x == pytest.approx([100, 0, 0, 100], abs=1e-6)
    assert objective == pytest.approx(500 * 200 + 550 * 200 + 300 * 100)


def test_lp_opens_early_when_the_ramp_would_bind():
    x, _ = p.lp_plan([0, 8000])
    assert x == pytest.approx([3000, 8000], abs=1e-6)


def test_lp_accepts_shortfall_only_when_capacity_cannot_be_reached():
    x, objective = p.lp_plan([9000])
    assert x == pytest.approx([5000], abs=1e-6)
    assert objective == pytest.approx(500 * 5000 + 550 * 5000 + 5000 * 4000)


def test_lp_ignores_negative_gaps_and_has_no_terminal_charge():
    x, objective = p.lp_plan([-200, 50])
    assert x == pytest.approx([0, 50], abs=1e-6)
    assert objective == pytest.approx(500 * 50 + 550 * 50)


def test_rule_plan_is_feasible_for_the_lp_and_never_cheaper_on_the_forecast():
    rng = np.random.default_rng(0)
    for _ in range(20):
        gaps = rng.uniform(-500, 7000, 6)
        x, objective = p.lp_plan(gaps)
        rule = p.totals_row(p.cost_table(p.rule_plan(gaps), gaps))["total"]
        assert objective <= rule + 1e-6 * rule
        assert p.totals_row(p.cost_table(x, gaps))["total"] == pytest.approx(objective, rel=1e-7)


def test_hindsight_is_never_beaten_by_another_plan():
    rng = np.random.default_rng(1)
    actual = rng.uniform(1000, 6000, 6)
    best = p.lp_plan(actual)[1]
    for _ in range(20):
        forecast = actual + rng.normal(0, 800, 6)
        for x in (p.rule_plan(forecast), p.lp_plan(forecast)[0]):
            assert p.totals_row(p.cost_table(x, actual))["total"] >= best * (1 - 1e-9)


# --------------------------------------------------------------------------------------------- simple forecasts

def test_mean_persistence_and_seasonal_naive_on_a_toy_series():
    y = toy(range(1, 25))  # Jan 2024 - Dec 2025 holds 1..24; targets are Jan - Jun 2026
    assert p.mean_forecast(y).tolist() == [12.5] * 6
    assert p.persistence_forecast(y).tolist() == [24.0] * 6
    assert p.seasonal_naive_forecast(y).tolist() == [13, 14, 15, 16, 17, 18]  # Jan - Jun 2025


def test_target_months_follow_the_origin():
    months = p.target_months(pd.Timestamp("2025-09-01"))
    assert [f"{m:%Y-%m}" for m in months] == ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]


def test_window_requires_the_frame_to_end_at_the_cut_off():
    data = pd.DataFrame(dict(occupied=np.arange(30.0), available=np.arange(30.0)), index=pd.date_range("2023-01-01", periods=30, freq="MS"))
    w = p.window(data, pd.Timestamp("2025-06-01"))
    assert len(w) == 24 and w.index[0] == pd.Timestamp("2023-07-01")
    with pytest.raises(AssertionError):
        p.window(data, pd.Timestamp("2025-05-01"))  # a later month is in memory


# --------------------------------------------------------------------------------------------------- SARIMA-24

def test_sarima_is_seasonal_naive_plus_a_fading_year_on_year_change():
    rng = np.random.default_rng(2)
    season = 1000 * np.sin(2 * np.pi * np.arange(24) / 12)
    y = toy(90_000 + season + 40 * np.arange(24) + rng.normal(0, 150, 24))
    forecast, info = p.sarima_forecast(y)
    assert info["converged"] and info["labels"] == 24
    phi = info["params"]["ar.L1"]
    expected = p.seasonal_naive_forecast(y) + phi ** np.arange(1, 7) * (y.iloc[-1] - y.iloc[-13])
    assert forecast == pytest.approx(expected, abs=1e-3)


# ----------------------------------------------------------------------------------------------- ExtraTrees-24

def test_features_are_the_sixteen_named_columns_and_twelve_rows_survive():
    y = toy(np.random.default_rng(3).uniform(90, 110, 24))
    f = p.training_features(y)
    assert list(f.columns) == p.FEATURES and len(p.FEATURES) == 16
    kept = f.dropna()
    assert len(kept) == 12 and kept.index[0] == y.index[12]


def test_features_never_use_the_current_month():
    y = toy(np.random.default_rng(4).uniform(90, 110, 24))
    changed = y.copy()
    changed.iloc[-1] += 1000
    pd.testing.assert_series_equal(p.training_features(y).iloc[-1], p.training_features(changed).iloc[-1])


def test_recursive_row_matches_the_training_features():
    y = toy(np.random.default_rng(5).uniform(90, 110, 24))
    nxt = y.index[-1] + pd.offsets.MonthBegin(1)
    extended = pd.concat([y, pd.Series([12345.0], index=[nxt])])
    expected = p.training_features(extended).iloc[-1].to_numpy(float)
    assert p.recursive_row(y, nxt).iloc[0].to_numpy(float) == pytest.approx(expected, rel=1e-9)


def test_extra_trees_uses_twelve_rows_stays_in_range_and_repeats():
    y = toy(np.random.default_rng(6).uniform(90_000, 95_000, 24))
    first, info = p.extra_trees_forecast(y)
    second, _ = p.extra_trees_forecast(y)
    assert info["training_rows"] == 12 and info["earliest_month_used"] == f"{y.index[0]:%Y-%m}"
    assert np.max(np.abs(first - second)) <= 1e-6
    assert y.iloc[12:].min() <= first.min() and first.max() <= y.iloc[12:].max()  # trees cannot leave the label range


# ------------------------------------------------------------------------------------------------------ loader

def raw_sheet(rows):
    """A sheet shaped like 'TS - All Acutes': 12 filler rows, the header row, then one row per month."""
    width = 8
    header = [None, "Month", "G&A beds available", "x", "x", "x", "G&A beds occupied", "x"]
    body = [[None, month, available, None, None, None, occupied, None] for month, available, occupied in rows]
    return pd.DataFrame([[None] * width] * 12 + [header] + body)


def test_loader_drops_later_rows_blank_months_and_parses_commas():
    raw = raw_sheet([("January 2024", "1,000", "900"), ("February 2024", 1010, 910), ("March 2024", 1020, 920), ("April 2024", None, None)])
    full = p.parse(raw, "2024-12-01")
    assert [f"{m:%Y-%m}" for m in full.index] == ["2024-01", "2024-02", "2024-03"]  # the blank April row is dropped
    assert full.available.tolist() == [1000, 1010, 1020] and full.occupied.tolist() == [900, 910, 920]
    cut = p.parse(raw, "2024-02-01")
    assert cut.index[-1] == pd.Timestamp("2024-02-01") and len(cut) == 2


def test_loader_rejects_a_moved_header():
    raw = raw_sheet([("January 2024", 1, 1)])
    with pytest.raises(AssertionError):
        p.parse(raw.iloc[1:].reset_index(drop=True), "2024-12-01")


# ------------------------------------------------------------------------------------------------------- rules

def costs(overrides, base=200.0):
    table = pd.DataFrame(base, index=list(p.PLANS), columns=["O1", "O2"])
    for plan, values in overrides.items():
        table.loc[plan] = values
    return table


HIND = {"O1": 100.0, "O2": 100.0}  # tie band 1.0 at each origin


def test_joint_best_is_within_one_percent_of_hindsight_cost():
    ranked, band = p.rank_origin(costs({"SARIMA-24 LP": [110, 110], "Seasonal naive LP": [111, 111], "Mean-24 LP": [111.5, 111.5]})["O1"], 100.0)
    assert band == 1.0
    assert list(ranked.index[ranked.joint_best]) == ["Seasonal naive LP", "SARIMA-24 LP"]  # 111.5 is outside the band
    assert ranked.loc["SARIMA-24 LP", "rank"] == 1 and ranked.loc["Seasonal naive LP", "rank"] == 2 and ranked.loc["Mean-24 LP", "rank"] == 3


def test_single_plan_joint_best_at_both_origins_is_the_clear_winner():
    r = p.overall(costs({"SARIMA-24 LP": [110, 120]}), HIND)
    assert r["clear_winners"] == ["SARIMA-24 LP"] and r["first"] == "SARIMA-24 LP"


def test_no_clear_winner_when_the_best_plan_changes():
    r = p.overall(costs({"SARIMA-24 LP": [110, 160], "Seasonal naive LP": [160, 110]}), HIND)
    assert r["clear_winners"] == [] and r["joint_best"] == {"O1": ["SARIMA-24 LP"], "O2": ["Seasonal naive LP"]}


def test_two_plans_joint_best_at_both_origins_are_both_reported():
    r = p.overall(costs({"SARIMA-24 LP": [110, 120], "Seasonal naive LP": [110.5, 120.5]}), HIND)
    assert r["clear_winners"] == ["Seasonal naive LP", "SARIMA-24 LP"]


def test_overall_first_wins_by_beating_the_runner_up_at_each_origin():
    # ExtraTrees-24 LP is cheapest at O2 but poor at O1; SARIMA-24 LP is first overall and beats the runner-up twice
    r = p.overall(costs({"SARIMA-24 LP": [110, 130], "Seasonal naive LP": [115, 135], "ExtraTrees-24 LP": [200, 120]}), HIND)
    assert r["first"] == "SARIMA-24 LP" and r["runner_up"] == "Seasonal naive LP"
    assert r["joint_best_at_both"] == [] and r["clear_winners"] == ["SARIMA-24 LP"]
    assert r["table"].loc["SARIMA-24 LP", "combined_excess"] == 40 and r["table"].loc["SARIMA-24 LP", "overall_rank"] == 1


def test_best_forecast_is_clear_only_with_a_margin_at_both_origins():
    mae = pd.DataFrame({"O1": [700, 900, 300, 340, 450], "O2": [800, 900, 500, 490, 600]}, index=list(p.METHODS), dtype=float)
    r = p.best_forecast(mae)
    assert r["method"] == "Seasonal naive" and r["mean_gap_mae"] == 400 and not r["clear"]  # not the lowest at O2
    mae.loc["Seasonal naive", "O2"] = 480
    assert p.best_forecast(mae)["clear"]
    mae.loc["Seasonal naive", "O2"] = 486
    assert not p.best_forecast(mae)["clear"]  # lowest, but by less than 5 beds


def test_improvement_measures_hand_example():
    cost = costs({"Persistence Rule": [300, 300], "Persistence LP": [300, 300], "Seasonal naive Rule": [180, 220], "Seasonal naive LP": [150, 200],
                  "SARIMA-24 Rule": [170, 190], "SARIMA-24 LP": [140, 160]})
    flat = pd.DataFrame({"O1": [700, 900, 300, 240, 450], "O2": [800, 900, 500, 400, 600]}, index=list(p.METHODS), dtype=float)
    m = p.improvements(cost, HIND, dict(gap=flat, occupied=flat, available=flat)).set_index(["scope", "plan"])
    o1, both = m.loc[("O1", "SARIMA-24 LP")], m.loc[("combined", "SARIMA-24 LP")]
    assert o1.value_of_optimisation == 30 and o1.saving_vs_persistence == 160 and o1.saving_vs_seasonal_naive == 10
    assert o1.share_avoidable_vs_persistence_lp == pytest.approx(80.0) and o1.share_avoidable_vs_seasonal_naive_lp == pytest.approx(20.0)
    assert o1.excess_over_hindsight == 40 and o1.excess_pct_of_hindsight == pytest.approx(40.0)
    assert o1.gap_skill_vs_seasonal_naive == pytest.approx(0.2)
    assert both.value_of_optimisation == 60 and both.saving_vs_seasonal_naive == 50
    assert both.share_avoidable_vs_seasonal_naive_lp == pytest.approx(100 * 50 / 150)  # ratio of sums
    assert both.gap_skill_vs_seasonal_naive == pytest.approx(1 - 320 / 400)
    rule = m.loc[("O1", "SARIMA-24 Rule")]
    assert rule.saving_vs_seasonal_naive == 10  # Rule against Rule
