"""Optional input-only branch rules, fitted on explicitly seen paired tasks.

This is not an execution router. Callers freeze a rule and persist its choice
before executing the new task; full alternatives remain separately charged.
"""
from __future__ import annotations
import math
from pathlib import Path
from etalon.active.schema import digest
from .evidence_refs import file_identity

FEATURES = ('heavy_atoms', 'rotatable_bonds', 'aromatic_fraction', 'tpsa',
            'logp', 'hbd', 'formal_charge', 'rings')


def input_features(ligand):
    """Chemical identity only: no reference, prediction, score or label input."""
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors as D
    ligand = Path(ligand)
    before = file_identity(ligand)
    mols = list(Chem.SDMolSupplier(str(ligand), removeHs=False))
    if len(mols) != 1 or mols[0] is None:
        raise ValueError('one readable conditioning ligand required')
    m = Chem.RemoveHs(mols[0])
    if not m.GetNumHeavyAtoms():
        raise ValueError('empty molecule')
    values = dict(heavy_atoms=m.GetNumHeavyAtoms(), rotatable_bonds=D.CalcNumRotatableBonds(m),
        aromatic_fraction=sum(a.GetIsAromatic() for a in m.GetAtoms())/m.GetNumHeavyAtoms(),
        tpsa=D.CalcTPSA(m), logp=Descriptors.MolLogP(m), hbd=D.CalcNumHBD(m),
        formal_charge=Chem.GetFormalCharge(m), rings=D.CalcNumRings(m))
    if before != file_identity(ligand):
        raise ValueError('conditioning ligand changed during feature extraction')
    return {'schema':'etalon-pose-input-features/1', 'stage':'before_scientific_execution',
            'values':values, 'source':{'path':str(ligand.absolute()), **before},
            'identity':Chem.MolToSmiles(m,isomericSmiles=True)}


def choose(rule, values):
    if rule['kind'] == 'constant':
        return rule['selected']
    value = values[rule['feature']]
    return rule['lower'] if value <= rule['threshold'] else rule['upper']


def fit_rule(rows, *, minimum_leaf_cases=4):
    """One split at most; case grouping prevents duplicate seeds inflating support.

    Optimize valid Top1 under a physical-validity floor vs empirical; ties prefer
    fewer CNN uses, then a constant, then canonical rule identity. These are
    development diagnostics, never independent validation of the fitted rule.
    """
    if not rows or len({(r['case_id'],r['seed']) for r in rows}) != len(rows):
        raise ValueError('nonempty unique seen case-seeds required')
    cases = {}
    for r in rows:
        if set(r['features']) != set(FEATURES) or not all(type(x) in (int,float) and math.isfinite(x) for x in r['features'].values()):
            raise ValueError('finite input-only features required')
        if r['case_id'] in cases and cases[r['case_id']] != r['features']:
            raise ValueError('features differ between seeds of one case')
        cases[r['case_id']] = r['features']
        for method in ('empirical','cnn'):
            if any(type(r[method][k]) is not bool for k in ('top1','physical_valid')):
                raise ValueError('explicit evaluated outcomes required')
    rules = [{'kind':'constant','selected':m} for m in ('empirical','cnn')]
    for f in FEATURES:
        vals = sorted({v[f] for v in cases.values()})
        for a,b in zip(vals,vals[1:]):
            threshold = (a+b)/2
            left = sum(v[f] <= threshold for v in cases.values())
            if min(left,len(cases)-left) < minimum_leaf_cases:
                continue
            for lower,upper in [('empirical','cnn'),('cnn','empirical')]:
                rules.append(dict(kind='stump',feature=f,threshold=threshold,lower=lower,upper=upper))
    floor = sum(r['empirical']['physical_valid'] for r in rows)
    candidates = []
    for rule in rules:
        selected = [choose(rule,r['features']) for r in rows]
        physical = sum(r[m]['physical_valid'] for r,m in zip(rows,selected))
        if physical < floor:
            continue
        successes = sum(r[m]['top1'] for r,m in zip(rows,selected))
        calls = selected.count('cnn')
        candidates.append(((-successes,calls,rule['kind']!='constant',digest(rule)),rule,
                           dict(successes=successes,physical_valid=physical,cnn_calls=calls)))
    _, rule, metrics = min(candidates,key=lambda c:c[0])
    return {'schema':'etalon-pose-selection-policy/1','rule':rule,
        'feature_stage':'before_scientific_execution','training_digest':digest(rows),
        'training_cases':sorted(cases),'training_metrics':metrics,'tested_rules':len(rules),
        'minimum_leaf_cases':minimum_leaf_cases,
        'feature_bounds':{f:[min(v[f] for v in cases.values()),max(v[f] for v in cases.values())] for f in FEATURES},
        'fallback':'empirical','status':'candidate_requires_independent_evaluation'}


def grouped_diagnostics(rows):
    folds=[]
    for case in sorted({r['case_id'] for r in rows}):
        train=[r for r in rows if r['case_id'] != case]
        policy=fit_rule(train)
        for r in [r for r in rows if r['case_id'] == case]:
            selected=select_with_policy(policy,{'values':r['features'],'stage':'before_scientific_execution'})['selected']
            folds.append({'case_id':case,'seed':r['seed'],'selected':selected,'success':r[selected]['top1'],'rule':policy['rule']})
    return {'scope':'leave-one-case-out development diagnostic; both seeds withheld together',
            'successes':sum(r['success'] for r in folds),'rows':folds}


def select_with_policy(policy, features):
    if policy.get('schema') != 'etalon-pose-selection-policy/1' or features.get('stage') != 'before_scientific_execution':
        raise ValueError('frozen policy and pre-execution features required')
    values = features['values']
    if set(values) != set(FEATURES) or any(type(v) not in (int,float) or not math.isfinite(v) for v in values.values()):
        raise ValueError('finite input-only features required')
    rule = policy['rule']; f = rule.get('feature')
    outside = f is not None and not policy['feature_bounds'][f][0] <= values[f] <= policy['feature_bounds'][f][1]
    selected = policy['fallback'] if outside else choose(rule,values)
    return {'schema':'etalon-pose-branch-selection/1','selected':selected,'policy_digest':digest(policy),
            'features_digest':digest(features),'outside_feature_support':outside,
            'reason':'outside fitted feature range; retain baseline' if outside else 'frozen input-only candidate rule',
            'unknown_alternative_outcome':True,'automatic_execution':False}


from etalon.active.schema import finite


def selection_diagnostic(rows: list[dict], *, score_key='score', minimize=True,
                         tie_tolerance=1e-6, loss_cap=6.) -> dict:
    finite(tie_tolerance, 'score tie tolerance', minimum=0)
    finite(loss_cap, 'loss cap', minimum=0)
    if len({r['id'] for r in rows}) != len(rows) or len({r['slot'] for r in rows}) != len(rows):
        raise ValueError('unique pose identity and slot index required')
    admitted = [r for r in rows if r['admitted']]
    if not admitted: return {'status': 'no_admitted_pose', 'attempted': len(rows)}
    mode_sets = [set(r['mode_rmsd_A']) for r in admitted]
    if not mode_sets[0] or any(s != mode_sets[0] for s in mode_sets):
        raise ValueError('identical nonempty reference mode set required for every pose')
    for row in admitted:
        for value in row['mode_rmsd_A'].values(): finite(value, 'RMSD', minimum=0)
        if row.get(score_key) is not None: finite(row[score_key], 'score')
    scored = [r for r in admitted if r.get(score_key) is not None]
    if not scored: return {'status': 'no_scored_pose', 'attempted': len(rows), 'admitted': len(admitted)}
    optimum = (min if minimize else max)(r[score_key] for r in scored)
    ties = sorted((r for r in scored if abs(r[score_key] - optimum) <= tie_tolerance), key=lambda r: r['slot'])
    selected = ties[0]
    metrics = {}
    for name, reduction in [('nearest_mode', min), ('worst_mode', max)]:
        losses = {r['id']: min(loss_cap, reduction(r['mode_rmsd_A'].values())) for r in admitted}
        oracle = min(losses.values()); scored_oracle = min(losses[r['id']] for r in scored)
        metrics[name] = {'selected_loss_A': losses[selected['id']], 'pool_optimal_loss_A': oracle,
            'scored_pool_optimal_loss_A': scored_oracle,
            'same_loss_regret_A': losses[selected['id']] - oracle,
            'scored_pool_regret_A': losses[selected['id']] - scored_oracle,
            'oracle_pose_ids': sorted(k for k, v in losses.items() if abs(v-oracle) <= 1e-10),
            'tie_loss_range_A': [min(losses[r['id']] for r in ties), max(losses[r['id']] for r in ties)],
            'all_pose_losses_A': losses}
    coverage = min(loss_cap, max(min(r['mode_rmsd_A'][mode] for r in admitted) for mode in mode_sets[0]))
    return {'status': 'measured', 'attempted': len(rows), 'admitted': len(admitted), 'scored': len(scored),
        'unscored_admitted_ids': [r['id'] for r in admitted if r.get(score_key) is None],
        'score_direction': 'min' if minimize else 'max', 'selected_pose_id': selected['id'],
        'selected_slot': selected['slot'], 'selected_score': selected[score_key],
        'tie_tolerance': tie_tolerance, 'tie_pose_ids': [r['id'] for r in ties],
        'tie_resolution': 'minimum global slot among scores within declared tolerance',
        'loss_cap_A': loss_cap, 'single_pose_objectives': metrics,
        'ensemble_worst_mode_coverage_A': coverage,
        'scope': 'same single-pose losses; structural diagnostics do not establish affinity or mechanism'}
