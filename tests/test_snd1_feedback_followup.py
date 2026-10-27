"""Conflicting repeat observations must remain visible to the next choice."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
from snd1_feedback_followup import analyze


def test_pooled_majority_does_not_erase_opposite_seed_preference(tmp_path):
    rows=[]
    for seed,states in [(1,[(True,False)]*4),(2,[(False,True)]*2)]:
        folder=tmp_path/str(seed);folder.mkdir()
        (folder/'CONTACTS.json').write_text(json.dumps([
            {'ASN281':{'primary':a},'ARG255':{'primary':b}} for a,b in states]))
        rows.append(dict(status='succeeded',mode='coupled',seed=seed,handle=str(folder/'mdjob'),
                         late_samples=len(states),quality={'acceptable':True}))
    result=tmp_path/'result.json';result.write_text(json.dumps(dict(rows=rows,
        decision={'action':'analyze_competing_anchor_states'})))
    follow=analyze(result,tmp_path/'follow.json')
    assert follow['totals']['ASN_only']>follow['totals']['ARG_only']
    assert follow['proposal']['candidate_anchor'] is None
    assert follow['proposal']['alternatives']==['ASN281','ARG255']
