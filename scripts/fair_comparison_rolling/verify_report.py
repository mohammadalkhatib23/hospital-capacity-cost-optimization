"""Check every displayed result table and result claim against saved outputs."""
from pathlib import Path
import json
import re
import sys
sys.dont_write_bytecode=True
import xml.etree.ElementTree as ET
import pandas as pd
from PIL import Image
from rolling import OUT,REPO,METHODS,cc,write_json


def main():
    text=(OUT/'readme_section.md').read_text(encoding='utf-8')
    cfg=json.loads((OUT/'config.json').read_text()); decisions=json.loads((OUT/'decisions.json').read_text())
    s=pd.read_csv(OUT/'summary.csv',float_precision='round_trip')
    c=pd.read_csv(OUT/'coverage_by_horizon.csv',float_precision='round_trip')
    summary_rows=0
    for row in s[s.penalty==5000].itertuples():
        expected='| '+' | '.join([row.method,f'{row.mean_excess_over_hindsight/1e6:.2f}',str(row.joint_best_count),
                  f'{row.wins_vs_seasonal_naive}/{row.ties_vs_seasonal_naive}/{row.losses_vs_seasonal_naive}',
                  f'{row.mean_pinball_090:.1f}',f'{row.coverage_090:.1%} ({row.covered_090}/{row.months})',
                  f'{row.mean_value_of_protection/1e6:.2f}'])+' |'
        assert expected in text,expected
        summary_rows+=1
    coverage_rows=0
    for scope in cfg['scopes']:
        for method in METHODS:
            values=c[(c.scope==scope)&(c.method==method)].sort_values('horizon').coverage
            expected='| '+' | '.join([method]+[f'{v:.1%}' for v in values])+' |'
            assert expected in text,expected
            coverage_rows+=1
    labels={'all':'All retained origins','excluding_fable':'Excluding September 2025 and March 2026',
            'through_2024':'Origins through December 2024','from_2025':'Origins from January 2025'}
    for scope in cfg['scopes']:
        d=decisions['scopes'][scope]
        for p,entry in d['penalties'].items():
            expected='| '+' | '.join([labels[scope],f'{int(p):,}'+(' (indicative)' if p=='10000' else ''),
                                      entry['top'],entry['clear_winner'] or 'No clear winner'])+' |'
            assert expected in text
        if scope!='all':
            entry=d['penalties']['5000']; wtl=entry['wins_ties_losses_vs_runner']
            assert f"Lowest subset mean in {entry['subset_wins'][entry['top']]} of six subsets" in text
            assert f"{wtl['wins']}/{wtl['ties']}/{wtl['losses']}" in text
    a=decisions['scopes']['all'];primary=a['penalties']['5000'];excluded=decisions['scopes']['excluding_fable']['penalties']['5000']
    assert primary['clear_winner']=='Seasonal naive' and primary['runner_up']=='ExtraTrees-24'
    assert excluded['top']=='Seasonal naive' and excluded['clear_winner'] is None
    assert decisions['scopes']['through_2024']['penalties']['5000']['clear_winner'] is None
    assert decisions['scopes']['from_2025']['penalties']['5000']['clear_winner']=='Seasonal naive'
    assert a['penalties']['10000']['top']=='ExtraTrees-24' and a['penalties']['10000']['clear_winner'] is None
    assert not a['robustness']['robust']
    under=c[(c.scope=='all')&(c.coverage<.9)]
    assert set(zip(under.method,under.horizon))=={('Seasonal naive',5),('Seasonal naive',6),('Persistence',1),('SARIMA-24',5)}
    assert (s[(s.scope=='all')&(s.penalty==5000)].coverage_090>.9).all()
    assert len(cfg['origin_records'])==42 and len(cfg['origins'])==25
    assert sum(v['status']=='excluded' for v in cfg['origin_records'])==17
    assert sum(v['bank_paths']<9 for v in cfg['origin_records'])==15
    assert cfg['planning_origin_convergence']['excluded']==[
        {'origin':'2025-10','failed_targets':['occupied']},{'origin':'2026-01','failed_targets':['available']}]
    assert cfg['last_published_month']=='2026-10'
    assert cfg['window']==24 and cfg['horizon']==6 and cfg['minimum_paths']==9 and cfg['cost_band']==.01
    assert cfg['plans']['protected_penalties']==[2500,5000,10000]
    assert cfg['rules']['primary_penalty']==5000
    for record in cfg['origin_records']:
        if record['status']=='included':
            expected=pd.Timestamp(record['origin']).strftime('%b')+f" ({record['bank_paths']})"
            assert expected in text
    assert cc.sha256(OUT/'config.json') in text and cc.sha256(OUT/'plans.csv') in text
    # Decimal points in numerical values are not sentence terminators.
    answer=text.split('### Answer\n\n')[1].strip()
    assert len(re.split(r'(?<!\d)\.\s+(?=[A-Z])',answer))<=5
    ending='Neighbouring origins share up to five target months, so origins are not independent; costs are illustrative model units (validated: False).'
    assert answer.endswith(ending)
    # Every linked artifact must exist before appending to the public README.
    for target in re.findall(r'\]\(([^)]+)\)',text):
        assert (REPO/target).is_file(),target
    inventory=json.loads((OUT/'figure_inventory.json').read_text())
    assert len(inventory)==6
    for entry in inventory:
        file=REPO/entry['file'];assert cc.sha256(file)==entry['sha256']
        if file.suffix=='.png':
            with Image.open(file) as image:
                assert image.size[0]>2500 and all(abs(d-300)<1 for d in image.info['dpi'])
        else:
            assert ET.parse(file).getroot().tag.endswith('svg')
    for name,digest in json.loads((OUT/'report_source_hashes.json').read_text()).items():
        assert cc.sha256(OUT/name)==digest,name
    result=dict(status='PASS',summary_rows=summary_rows,coverage_rows=coverage_rows,robustness_rows=12,
                origin_and_exclusion_tables='checked',primary_and_subgroup_claims='checked',
                figure_files=6,png_dpi=300,readme_section_sha256=cc.sha256(OUT/'readme_section.md'),
                result='Every numerical results row and reported finding matches verified outputs; source/plan hashes unchanged.')
    write_json(OUT/'report_verification.json',result)
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    try:
        main()
    except BaseException as exc:
        write_json(OUT/'report_verification.json',dict(status='FAIL',error=f'{type(exc).__name__}: {exc}'))
        raise
