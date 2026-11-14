from pathlib import Path
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import pytest
from value import cc,close_reopen_events,gap_shares,counts


def test_imported_rule_ramp_limited_month_and_one_month_dip():
    actual=cc.rule_plan([2000,9000,3000,8000,8000,8000])
    np.testing.assert_array_equal(actual,[2000,7000,3000,8000,8000,8000])
    a,r=cc.flows(actual)
    assert a.max()<=5000 and r.max()<=5000


def test_imported_rule_cap_and_both_ramps():
    np.testing.assert_array_equal(cc.rule_plan([5000,10000,15000,1000,15000,20000]),[5000,10000,15000,10000,15000,15000])


def test_close_then_reopen_counts_each_removal_only_once():
    assert close_reopen_events([10,5,8,10,7,9])==2
    assert close_reopen_events([10,5,5,8,8,8])==1
    assert close_reopen_events([10,5,5,5,10,10])==0
    assert close_reopen_events([10,10,10,10,10,5])==0
    assert close_reopen_events([10,10-1e-8,10,10,10,10])==0


def test_gap_shares_hand_calculation_ratios_of_sums():
    result=gap_shares([10,20],[9,17],[8,15],[5,10])
    assert result['denominator']==15
    assert result['optimisation']==pytest.approx(4/15)
    assert result['protection']==pytest.approx(3/15)
    assert result['remaining']==pytest.approx(8/15)
    assert sum(result[k] for k in ['optimisation','protection','remaining'])==pytest.approx(1)


def test_gap_shares_do_not_clip_negative_value():
    result=gap_shares([10],[12],[9],[5])
    assert result['optimisation']==pytest.approx(-.4)
    assert result['protection']==pytest.approx(.6)
    assert result['remaining']==pytest.approx(.8)


def test_zero_gap_has_undefined_shares():
    result=gap_shares([5],[5],[5],[5])
    assert result['denominator']==0
    assert all(np.isnan(result[k]) for k in ['optimisation','protection','remaining'])


def test_tolerance_boundaries_are_ties():
    assert counts([2,1,0,-1,-2],[1]*5)==dict(helped=1,tie=3,hurt=1)
