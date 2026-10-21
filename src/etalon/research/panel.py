"""Factorial preparation/evaluation panels and executable finite selection policies.

Docking scores remain surrogate scores. Missing cells and failed slots never become
favorable numbers. No validation labels enter development policy selection here.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import isclose

from etalon.active.schema import canonical, digest, finite
from etalon.active.store import StateError
from .journal import ResearchJournal
from .schema import Change, Contrast, Coupling, Revision


@dataclass(frozen=True)
class PanelSpec:
    task_id: str
    role: str
    candidates: dict[str, dict]
    preparations: dict[str, dict]
    evaluations: dict[str, dict]
    slots_per_candidate: int
    receptor_state_id: str
    coordinate_frame_id: str
    independent_objective: dict

    def __post_init__(self):
        if self.role not in {'development', 'validation'}:
            raise ValueError('explicit task split required')
        if len(self.candidates) < 2 or set(self.preparations) != {'P0', 'P1'} or set(self.evaluations) != {'E0', 'E1'}:
            raise ValueError('panel requires chemical candidates and both P/E factors')
        if type(self.slots_per_candidate) is not int or self.slots_per_candidate <= 0:
            raise ValueError('positive attempted-slot denominator required')
        if not self.receptor_state_id or not self.coordinate_frame_id or not self.independent_objective:
            raise ValueError('common frame/receptor and independent objective required')
        for candidate in self.candidates.values():
            if not all(candidate.get(k) for k in ('smiles', 'chemical_state_id')):
                raise ValueError('candidate chemical identity required, separate from atom mapping')
        for evaluator in self.evaluations.values():
            triple = (evaluator.get('kind'), evaluator.get('unit'), evaluator.get('direction'))
            if triple not in {('docking_score','kcal/mol','min'), ('polar_pair_count','heavy_atom_pair_count','max')}:
                raise ValueError('explicit supported proxy/scorer definition and direction required')
            if not evaluator.get('method_id') or not evaluator.get('source_identity'):
                raise ValueError('scorer method and source must be frozen')
        canonical(self.as_dict())

    def as_dict(self):
        from dataclasses import asdict
        return asdict(self)

    @property
    def id(self):
        return 'panelspec-' + digest(self.as_dict())


def freeze_panel(journal: ResearchJournal, spec: PanelSpec) -> str:
    with journal.store.connection(write=True) as db:
        return journal._put(db, spec.id, spec.as_dict())


def record_cell(journal: ResearchJournal, panel_id: str, *, candidate: str,
                preparation: str, evaluation: str, slots: list[dict],
                execution_ids: tuple[str, ...]) -> str:
    """Slots reference actual admitted observations, never caller-supplied score values.

    Each slot declares its readout and pose/mapping identity. A failed/missing
    observation preserves its status and the intended denominator.
    """
    with journal.store.connection(write=True) as db:
        spec = PanelSpec(**journal._get(db, panel_id))
        if candidate not in spec.candidates or preparation not in spec.preparations or evaluation not in spec.evaluations:
            raise StateError('unknown panel factor')
        if len(slots) != spec.slots_per_candidate or len({s['slot'] for s in slots}) != len(slots):
            raise StateError('all attempted slots including failures must be represented once')
        if {s['slot'] for s in slots} != set(range(1, len(slots)+1)):
            raise StateError('slot denominator is not contiguous')
        rows = []
        for slot in slots:
            obs = journal._get(db, slot['observation_id'])
            if not slot['observation_id'].startswith('observation-'):
                raise StateError('panel requires admitted observations')
            ex = journal._get(db, obs['experiment_id'])
            expected = {'chemical_state_id': spec.candidates[candidate]['chemical_state_id'],
                        'receptor_state_id': spec.receptor_state_id,
                        'coordinate_frame_id': spec.coordinate_frame_id}
            if ex['task_id'] != spec.task_id or any(ex['bindings'].get(k) != v for k, v in expected.items()):
                raise StateError('candidate/task/frame identity mismatch')
            if (obs.get('measurement_method_id') or ex['method_id']) != spec.evaluations[evaluation]['method_id']:
                raise StateError('scorer method differs from frozen evaluator')
            journal._artifacts(db, obs['evidence'], diagnostic_inputs=True)
            if not slot.get('mapping_id') or (obs['status']=='measured' and not slot.get('pose_id')):
                raise StateError('measured pose and mapping identities must remain separate')
            if ex['bindings'].get('pose_id') is not None and ex['bindings']['pose_id']!=slot.get('pose_id'):
                raise StateError('slot pose differs from admitted observation')
            score = None
            if obs['status'] == 'measured':
                if obs['units'].get(slot['readout']) != spec.evaluations[evaluation]['unit']:
                    raise StateError('score unit mismatch')
                score = obs['values'][slot['readout']]
                finite(score, 'panel readout')
            rows.append({**slot, 'status': obs['status'], 'score': score})
        for execution_id in execution_ids:
            if not db.execute('SELECT 1 FROM metadata WHERE key=?', ('budget:execution:' + execution_id,)).fetchone():
                raise StateError('panel execution is not in the shared budget ledger')
        body = {'panel_id': panel_id, 'candidate': candidate, 'preparation': preparation,
                'evaluation': evaluation, 'slots': rows, 'execution_ids': list(execution_ids),
                'score_scope': 'structural/docking proxy; not experimental affinity'}
        # Cell factor identity is unique; corrections require a new frozen panel.
        key = 'panelcell-' + digest([panel_id, candidate, preparation, evaluation])
        return journal._put(db, key, body)


def summarize_panel(journal: ResearchJournal, panel_id: str) -> dict:
    spec = PanelSpec(**journal.get(panel_id))
    cells = {}; missing = []; policies = {}
    for candidate, preparation, evaluation in product(spec.candidates, spec.preparations, spec.evaluations):
        name = '/'.join((candidate, preparation, evaluation))
        key = 'panelcell-' + digest([panel_id, candidate, preparation, evaluation])
        try:
            cell = journal.get(key)
        except StateError:
            missing.append(name); cells[name] = None; continue
        measured = [s for s in cell['slots'] if s['status'] == 'measured']
        sign = 1 if spec.evaluations[evaluation]['direction'] == 'min' else -1
        best = min(measured, key=lambda s: (sign*s['score'], s['slot'])) if measured else None
        cells[name] = {'cell_id': key, 'attempted': len(cell['slots']), 'accepted': len(measured),
                       'failed_or_missing': len(cell['slots'])-len(measured),
                       'selected_pose': best, 'score': best['score'] if best else None,
                       'execution_ids': cell['execution_ids']}
    for preparation, evaluation in product(spec.preparations, spec.evaluations):
        rows = [(candidate, cells['/'.join((candidate, preparation, evaluation))]) for candidate in spec.candidates]
        pending = any(cell is None for _, cell in rows)
        available = [(candidate,cell) for candidate,cell in rows if cell is not None and cell['score'] is not None]
        complete = len(available) == len(rows)
        sign = 1 if spec.evaluations[evaluation]['direction'] == 'min' else -1
        ranked = sorted(available, key=lambda item: (sign*item[1]['score'], item[0])) if not pending else []
        tied = len(ranked)>1 and isclose(ranked[0][1]['score'], ranked[1][1]['score'], abs_tol=1e-6, rel_tol=0)
        status = 'abstain_missing_cells' if pending else 'failed_all_candidates' if not ranked else 'scored'
        policies[preparation + '/' + evaluation] = {
            'status': status, 'coverage_complete': complete,
            'ranking': [candidate for candidate, _ in ranked],
            'selected_candidate': ranked[0][0] if ranked and not tied else None,
            'top_tie': bool(tied), 'units': spec.evaluations[evaluation]['unit'],
            'kind': spec.evaluations[evaluation]['kind'],
            'uncertainty': None, 'uncertainty_reason': 'correlated poses; no calibrated assay-error model',
        }
    return {'panel_id': panel_id, 'task_id': spec.task_id, 'role': spec.role,
            'cells': cells, 'missing_cells': missing, 'policies': policies,
            'independent_objective': spec.independent_objective,
            'causal_attribution': 'unresolved; factorial differences are descriptive'}


def choose_development_policy(journal: ResearchJournal, panel_id: str, *,
                              losses: dict[str, float | None], evidence: tuple[str, ...],
                              loss_definition: dict, baseline: str = 'P0/E0') -> dict:
    """Finite workflow comparison; this is not an improvement-policy learning update.

    Losses must be independently evaluated DEVELOPMENT losses, not Vina energies
    compared across evaluators. The caller's evaluator and source remain explicit.
    """
    panel = summarize_panel(journal, panel_id)
    if panel['role'] != 'development':
            raise StateError('validation task cannot select a development workflow')
    if set(losses) != set(panel['policies']) or baseline not in losses or not loss_definition:
        raise ValueError('all candidate policy losses and fixed objective must be explicit')
    with journal.store.connection(write=True) as db:
        journal._artifacts(db, evidence, diagnostic_inputs=True)
        if not evidence:
            raise StateError('development loss evidence required')
        admissible = {}
        for policy, value in losses.items():
            if value is not None:
                finite(value, 'development loss', minimum=0)
                status=panel['policies'][policy]['status']
                if status != 'scored' and not (status=='failed_all_candidates' and value==1):
                    raise StateError('cannot optimize a policy with missing scores')
                admissible[policy] = value
        complete = len(admissible) == len(losses)
        best = min(admissible, key=lambda p: (admissible[p], p != baseline, p)) if complete else baseline
        result = {'panel_id': panel_id, 'losses': losses, 'loss_definition': loss_definition,
                  'evidence': list(evidence), 'baseline_workflow': baseline, 'selected_workflow': best,
                  'status': 'empirical_proposal' if complete else 'abstain_incomplete_independent_loss',
                  'mechanism_posterior': None, 'effective_independent_tasks': 1,
                  'generalization_uncertainty': 'not_estimable_from_one_development_task',
                  'promotion': 'none', 'transfer_status': 'not_evaluated',
                  'improvement_policy_update': 'not_learned; workflow selection only'}
        journal._put(db, 'workflowselection-' + digest(result), result)
        return result


def propose_joint_revision(journal: ResearchJournal, *, task_id: str, configuration: dict,
                           updated_fields: dict[str, dict], evidence: tuple[str, ...],
                           contrast_ids: tuple[str, ...]) -> dict:
    """Materialize concrete reviewed config fields; executor still enforces capability."""
    if not {'design', 'preparation'} <= set(updated_fields):
        raise ValueError('joint revision must explicitly change design admission and preparation')
    changes = []
    for section, fields in updated_fields.items():
        for key, after in fields.items():
            before = configuration[section][key]
            if canonical(before) != canonical(after):
                changes.append(Change(section, (key,), before, after, evidence,
                                      'explicit test of graph/coordinate separation with frozen evaluation'))
    if not {'design', 'preparation'} <= {change.section for change in changes}:
        raise ValueError('nonempty changes in both admission and preparation required')
    revision = Revision(task_id, digest(configuration), tuple(changes),
                        'proposed executable test; no affinity or RSI gain asserted', contrast_ids)
    return journal.apply_revision(revision, configuration)


def preparation_contrast(journal: ResearchJournal, panel_id: str, *,
                         candidate_order: tuple[str, str], evaluation: str) -> str:
    """D=chemical candidate, P=preparation; hold evaluator fixed, preserve absent cells."""
    spec = PanelSpec(**journal.get(panel_id)); panel = summarize_panel(journal, panel_id)
    if len(set(candidate_order)) != 2 or not set(candidate_order) <= set(spec.candidates):
        raise ValueError('two distinct chemical candidate identities required')
    cells = {}; readouts = set()
    for d, candidate in enumerate(candidate_order):
        for p, preparation in enumerate(('P0','P1')):
            cell = panel['cells'][candidate + '/' + preparation + '/' + evaluation]
            selected = cell['selected_pose'] if cell else None
            cells[str(d)+str(p)] = selected['observation_id'] if selected else None
            if selected: readouts.add(selected['readout'])
    if len(readouts)>1:raise StateError('comparison has incompatible readouts')
    readout=next(iter(readouts)) if readouts else ('polar_pair_count' if spec.evaluations[evaluation]['kind']=='polar_pair_count' else 'vina_total')
    contrast = Contrast(spec.task_id, 'Chemical candidate by preparation at fixed evaluator ' + evaluation,
                        cells, readout, Coupling((), ('receptor_state_id','coordinate_frame_id'), (),
                        ('best-of-equal-slot docking scores; correlated preparations; no assay affinity claim',)),
                        spec.evaluations[evaluation]['method_id'])
    return journal.record(contrast)


def freeze_selection(journal: ResearchJournal, *, development_panel_id: str,
                     selection: dict, revision_receipt: dict, validation_panel_id: str,
                     validation_request_sha256s: tuple[str, ...] = ()) -> dict:
    """Freeze executable P/E rules before held-out panel measurements or label access."""
    dev = PanelSpec(**journal.get(development_panel_id))
    target = PanelSpec(**journal.get(validation_panel_id))
    if dev.role != 'development' or target.role != 'validation' or dev.task_id == target.task_id:
        raise StateError('development and validation tasks must be distinct')
    if selection.get('panel_id') != development_panel_id:
        raise StateError('selection does not bind development evidence')
    if not revision_receipt.get('revision_id'):
        raise StateError('a concrete nonempty revision is required')
    revision = journal.get(revision_receipt['revision_id'])
    if revision['task_id'] != dev.task_id or not revision['changes']:
        raise StateError('revision task mismatch or empty changes')
    target_summary = summarize_panel(journal, validation_panel_id)
    if any(v is not None for v in target_summary['cells'].values()):
        raise StateError('freeze must precede validation panel observations')
    if not all(selection.get(k) in {'P0/E0', 'P0/E1', 'P1/E0', 'P1/E1'} for k in ('baseline_workflow','selected_workflow')):
        raise StateError('selection rules are not executable panel policies')
    body = {
        'schema': 'etalon-joint-decision-freeze/1', 'task_id': dev.task_id,
        'development_panel_id': development_panel_id, 'validation_panel_id': validation_panel_id,
        'validation_request_sha256s':list(validation_request_sha256s),
        'selection': selection, 'revision': revision_receipt,
        'policies': {
            'static_P0_E0': {'policy': 'P0/E0', 'origin': 'frozen static baseline'},
            'static_P0_E1': {'policy': 'P0/E1', 'origin': 'predeclared evaluation intervention'},
            'static_P1_E0': {'policy': 'P1/E0', 'origin': 'predeclared preparation intervention'},
            'selected_workflow': {'policy': selection['selected_workflow'], 'origin': 'finite empirical development selection'},
            'copy_best': {'policy': selection['selected_workflow'], 'origin': 'copy same best development configuration'},
            'static_P1_E1': {'policy': 'P1/E1', 'origin': 'predeclared joint intervention control'},
        },
        'myopic': {'status': 'not_fitted', 'reason': 'no calibrated predictive query-value distribution'},
        'policy_equivalence': 'selected_workflow equals copy_best; neither is a learned improvement policy',
        'improvement_policy_learning': 'not_demonstrated',
        'independent_validation_accessed': False, 'new_task_labels_accessed': False,
        'promotion': 'none', 'scientific_judgment': 'unresolved',
        'budget': journal.store.balance(),
    }
    body['freeze_sha256'] = digest(body)
    with journal.store.connection(write=True) as db:
        journal._put(db, 'decision-' + body['freeze_sha256'], body)
    return body


def execute_frozen_policies(journal: ResearchJournal, decision_sha256: str) -> dict:
    """Run frozen candidate-ranking rules over admitted validation scores, without labels.

    Reuses a common fully observed panel. Cost attribution is reported explicitly;
    this is an off-policy evaluation, not four independent experiments.
    """
    decision = journal.get('decision-' + decision_sha256)
    if digest({k: v for k, v in decision.items() if k != 'freeze_sha256'}) != decision_sha256:
        raise StateError('frozen decision changed')
    panel = summarize_panel(journal, decision['validation_panel_id'])
    results = {}
    for name, rule in decision['policies'].items():
        policy = rule['policy']
        execution_ids = set()
        for key, cell in panel['cells'].items():
            if cell and key.endswith('/' + policy):
                execution_ids.update(cell['execution_ids'])
        results[name] = {**panel['policies'][policy], 'policy': policy,
                         'execution_ids': sorted(execution_ids)}
    report = {'schema': 'etalon-frozen-policy-predictions/1', 'decision_sha256': decision_sha256,
              'validation_panel_id': panel['panel_id'], 'task_id': panel['task_id'],
              'predictions': results, 'missing_cells': panel['missing_cells'],
              'labels_accessed': False, 'experimental_agreement': 'awaiting_independent_evaluator',
              'scope': 'shared-panel offline policy replay; no independent execution or transfer superiority',
              'myopic': decision['myopic'], 'budget': journal.store.balance()}
    with journal.store.connection(write=True) as db:
        journal._put(db, 'predictions-' + digest(report), report)
    return report
