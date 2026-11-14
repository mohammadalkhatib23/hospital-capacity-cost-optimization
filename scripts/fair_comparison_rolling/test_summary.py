import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pandas as pd
from summarize import decide, pairwise_counts


def test_pairwise_band_includes_equal_boundaries():
    assert pairwise_counts([89,90,100,110,111],[100]*5,[10]*5) == dict(wins=1,ties=3,losses=1)


def test_clear_winner_needs_four_offsets_and_more_pairwise_wins():
    methods=['A','B','C','D','E']
    # A leads four of six offsets and four of six origins.
    costs=pd.DataFrame({'A':[110,110,110,110,150,150],'B':[130]*6,'C':[200]*6,'D':[210]*6,'E':[220]*6})[methods]
    result,_=decide(costs,pd.Series([100]*6),methods)
    assert result['top']=='A' and result['clear_winner']=='A'
    assert result['subset_wins']['A']==4
    assert result['wins_ties_losses_vs_runner']==dict(wins=4,ties=0,losses=2)


def test_mean_winner_is_not_clear_without_four_subset_wins():
    methods=['A','B','C','D','E']
    costs=pd.DataFrame({'A':[100,100,100,141,141,141],'B':[130]*6,'C':[200]*6,'D':[210]*6,'E':[220]*6})[methods]
    result,_=decide(costs,pd.Series([100]*6),methods)
    assert result['top']=='A' and result['subset_wins']['A']==3
    assert result['clear_winner'] is None


def test_small_cost_advantage_can_fail_pairwise_rule():
    methods=['A','B','C','D','E']
    costs=pd.DataFrame({'A':[120]*6,'B':[120.5]*6,'C':[200]*6,'D':[210]*6,'E':[220]*6})[methods]
    result,_=decide(costs,pd.Series([100]*6),methods)
    assert result['subset_wins']['A']==6 and result['clear_winner'] is None
    assert result['wins_ties_losses_vs_runner']==dict(wins=0,ties=6,losses=0)


def test_exact_aggregate_tie_has_no_unique_winner():
    methods=['A','B','C','D','E']
    costs=pd.DataFrame({'A':[120]*6,'B':[120]*6,'C':[200]*6,'D':[210]*6,'E':[220]*6})[methods]
    result,_=decide(costs,pd.Series([100]*6),methods)
    assert not result['unique_aggregate_minimum'] and result['clear_winner'] is None
    assert result['subset_wins']['A']==result['subset_wins']['B']==6
