"""Optional improvement from existing evidence and normal use, without a probe stage.

All persistent values are ordinary Journal Artifacts. These helpers do not call
a model, execute methods, allocate resources or change shared defaults. The host
owns scientific judgment; record identity and observed use are checked here.
"""
from __future__ import annotations

import base64
import json

from etalon.active.schema import canonical, digest
from .schema import Artifact
from .evidence_refs import EvidenceError, validate_ref
from .read_scope import bounded_read

CONTEXT = 'etalon-revision-context/1'
OPPORTUNITY = 'etalon-revision-opportunity/1'
PATCH = 'etalon-method-patch/1'
ASSESSMENT = 'etalon-revision-assessment/1'
EXPERIENCE = 'etalon-method-experience/1'
SCHEMAS = {CONTEXT, OPPORTUNITY, PATCH, ASSESSMENT, EXPERIENCE}


def _conditions(value):
    if not isinstance(value, dict) or not value or any(
        not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
        for k, v in value.items()
    ):
        raise EvidenceError('conditions require explicit nonempty string values; unknown is not a match')


def _unique(refs):
    return [v for _, v in sorted({canonical(validate_ref(r)): r for r in refs}.items())]


def validate_content(payload):
    from .continuation import _fields, _refs, _text, _list
    schema = payload.get('schema')
    common = ('schema', 'task_id', 'scope', 'conditions', 'method_refs', 'supersedes')
    fields = {
        CONTEXT: ('problem_refs', 'evidence_refs', 'target_ref', 'observations', 'missing', 'status', 'opportunity_key'),
        OPPORTUNITY: ('context_ref', 'opportunity_key'),
        PATCH: ('context_ref', 'opportunity_ref', 'base_method_ref', 'candidate_method_ref',
                'workflow_before_ref', 'workflow_after_ref', 'workflow_edits', 'expected_change'),
        ASSESSMENT: ('patch_ref', 'plan_ref', 'observed_step_refs', 'result_refs', 'accounting_refs', 'missing', 'status'),
        EXPERIENCE: ('patch_ref', 'evaluation_ref', 'resolution_ref', 'outcome', 'lesson', 'invalid_when',
                     'support_refs', 'counter_refs', 'capability_id'),
    }
    if schema not in SCHEMAS:
        raise EvidenceError('unknown improvement content')
    _fields(payload, common + fields[schema])
    for key in ('task_id', 'scope'):
        _text(payload[key])
    _conditions(payload['conditions'])
    _refs(payload['method_refs'], nonempty=True)
    _refs(payload['supersedes'])
    for key in fields[schema]:
        if key.endswith('_refs'):
            _refs(payload[key])
        elif key.endswith('_ref') and payload[key] is not None:
            validate_ref(payload[key])
    if schema == CONTEXT:
        _refs(payload['problem_refs'], nonempty=True)
        for observation in _list(payload['observations']):
            _fields(observation, ('ref', 'schema', 'summary'))
            validate_ref(observation['ref'])
            _text(observation['summary'])
        if payload['status'] != ('incomplete' if payload['missing'] else 'ready_for_revision'):
            raise EvidenceError('context status conflicts with missing evidence')
        expected = digest({k: payload[k] for k in ('scope', 'conditions', 'target_ref', 'problem_refs', 'evidence_refs')})
        if expected != payload['opportunity_key']:
            raise EvidenceError('opportunity identity mismatch')
    if schema in {CONTEXT, ASSESSMENT}:
        for item in _list(payload['missing']):
            _text(item)
    if schema == PATCH:
        _text(payload['expected_change'])
        _list(payload['workflow_edits'])
        if (payload['workflow_before_ref'] is None) != (payload['workflow_after_ref'] is None):
            raise EvidenceError('workflow patch needs both versions')
        if payload['workflow_before_ref'] is None and payload['workflow_edits']:
            raise EvidenceError('workflow edits require a workflow')
    if schema == ASSESSMENT and payload['status'] != ('awaiting_evidence' if payload['missing'] else 'ready_for_evaluation'):
        raise EvidenceError('assessment status conflicts with evidence gaps')
    if schema == EXPERIENCE:
        for key in ('lesson', 'invalid_when', 'capability_id'):
            _text(payload[key])
        if payload['outcome'] not in {'adopt', 'retain', 'rollback', 'unresolved'}:
            raise EvidenceError('unknown experience outcome')
        if payload['outcome'] != 'unresolved' and (payload['evaluation_ref'] is None or payload['resolution_ref'] is None):
            raise EvidenceError('resolved experience needs evaluation and disposition')
        if not payload['support_refs'] and not payload['counter_refs']:
            raise EvidenceError('experience needs evidence, including negative evidence')
    return json.loads(canonical(payload))


def _local(journal, ref, source_id, schema=None):
    validate_ref(ref)
    if ref['source_id'] != source_id or 'member' in ref:
        raise EvidenceError('local binding required; import a provenance envelope first')
    body = journal.get(ref['record_id'])
    if digest(body) != ref['record_digest']:
        raise EvidenceError('record digest mismatch')
    if 'sha256' in ref and (ref['sha256'],ref['bytes']) != (body.get('sha256'),body.get('bytes')):
        raise EvidenceError('artifact identity mismatch')
    if ref['record_id'].startswith('artifact-'):
        body = json.loads(journal.artifact_path(Artifact(**body)).read_text('utf-8-sig'))
    if schema and body.get('schema') != schema:
        raise EvidenceError('wrong binding schema: expected ' + schema)
    return body


def read_material(workspace, ref):
    """Read a record or generic JSON artifact without guessing a typed schema.

    Binary/large artifacts remain explicit bounded fragments, never silently
    parsed as complete content. Existing reader performs all identity checks.
    """
    from .evidence import read_evidence
    record = read_evidence(workspace, ref, limit=32)
    body = record.get('record')
    if record.get('availability') != 'available' or record.get('missing') or body is None:
        raise EvidenceError('record unavailable or incomplete')
    if not {'path','sha256','bytes','source_kind','role'} <= set(body) and 'member' not in ref:
        return {'ref': ref, 'record': body, 'content': body, 'complete': True}
    result = read_evidence(workspace, ref, view='artifact', limit=32768)
    fragment = result.get('fragment', {})
    if result.get('verification', {}).get('artifact') != 'verified' or fragment.get('encoding') != 'base64':
        raise EvidenceError('artifact is not verified')
    complete = fragment.get('next_offset') is None
    raw = base64.b64decode(fragment['content'])
    try:
        content = json.loads(raw.decode('utf-8-sig')) if complete else None
    except (ValueError, UnicodeError):
        content = None
    return {'ref': result['ref'], 'record': body, 'content': content,
            'complete': complete, 'fragment': fragment if content is None else None}


def build_revision_context(workspace, *, task_id, scope, conditions, target_ref, problem_refs, evidence_refs=()):
    """Read an explicit evidence set. No task replay and no implicit full scan."""
    _conditions(conditions)
    problems, evidence = _unique(problem_refs), _unique(evidence_refs)
    observed, missing = [], []
    for ref in _unique([target_ref, *problems, *evidence]):
        try:
            material = read_material(workspace, ref)
            if not material['complete']:
                missing.append(ref['record_id'] + ': bounded fragment; narrow the evidence')
            content = material['content'] or material['record']
            observed.append({'ref': ref, 'schema': content.get('schema') if isinstance(content, dict) else None,
                             'summary': canonical(content)[:600]})
        except (ValueError, OSError, KeyError) as exc:
            missing.append(ref['record_id'] + ': ' + str(exc)[:200])
    payload = dict(schema=CONTEXT, task_id=task_id, scope=scope, conditions=conditions,
        target_ref=target_ref, method_refs=[target_ref], problem_refs=problems, evidence_refs=evidence,
        observations=observed, missing=missing, status='incomplete' if missing else 'ready_for_revision', supersedes=[])
    payload['opportunity_key'] = digest({k: payload[k] for k in ('scope','conditions','target_ref','problem_refs','evidence_refs')})
    return validate_content(payload)


def _saved(journal, schema, source_id):
    """Existing metadata table, exact artifact kind, original bytes verified."""
    from .method_revision import journal_ref
    with journal.store.connection() as db:
        rows = db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:artifact-%' ORDER BY key").fetchall()
    for key, text in rows:
        body = json.loads(text)
        if body.get('bindings', {}).get('schema') == schema:
            ref = journal_ref(journal, key[len('research:'):], source_id=source_id)
            yield ref, _local(journal, ref, source_id, schema)


def register_opportunity(journal, context_ref, directory, *, source_id, source_kind,
                         family_ref=None, external_workspace=None):
    """Stable semantic identity across distinct events; incomplete work is resumable.

    Returned request_id must be used by the caller's existing idempotent executor.
    This is not exactly-once model invocation. A pending request is not completion.
    Optional dispositions close a no-change opportunity even without a patch.
    A reviewed family can reuse a terminal judgment for identical original inputs;
    changed evidence requires a separate, explicit reconsideration review.
    """
    ctx = _local(journal, context_ref, source_id, CONTEXT)
    validate_content(ctx)
    if ctx['status'] != 'ready_for_revision':
        return {'state': 'insufficient_evidence', 'opportunity_ref': None, 'request_id': None, 'patch_refs': []}
    from etalon.host import _consumer_lock
    key = ctx['opportunity_key']
    with _consumer_lock(journal.store.path.parent / ('.revision-' + key + '.lock')):
        result = _register_opportunity(journal, context_ref, directory, source_id, source_kind, ctx)
        from .opportunity_state import resolve_opportunity_state
        state = resolve_opportunity_state(journal, result['opportunity_ref'], source_id=source_id,
                                          external_workspace=external_workspace)
        if state['history_refs']:
            result.update(state=state['state'], lifecycle=state, request_id=state['request_id'],
                          proposal_permission='host_review_required' if state['state'] == 'reopened' else 'not_granted')
        briefs = [r for r, b in _saved(journal, 'etalon-candidate-brief/1', source_id)
                  if b['opportunity_ref'] == result['opportunity_ref']]
        if briefs:
            result['brief_refs'] = briefs
            if not state['history_refs']:
                result['state'] = 'already_proposed'
        if family_ref is not None:
            _family_recovery(journal, result, ctx, family_ref, source_id, external_workspace)
        return result


@bounded_read
def _family_recovery(journal, result, ctx, family_ref, source_id, workspace):
    """Reviewed grouping informs reuse; it cannot silently reopen old conclusions."""
    from .defect_family import DEFECT_FAMILY, read_defect_family, validate_local_links
    from .opportunity_state import resolve_opportunity_state
    if family_ref['source_id'] == source_id:
        family = _local(journal, family_ref, source_id, DEFECT_FAMILY)
        validate_local_links(journal, family, source_id, external_workspace=workspace)
    else:
        if workspace is None:
            raise EvidenceError('external family requires an explicit workspace')
        family = read_defect_family(workspace, family_ref)
    members = family['members']
    current = [m for m in members if m['opportunity_ref'] == result['opportunity_ref']]
    if not current or any(not m['accepted'] for m in current):
        raise EvidenceError('family suppression requires unambiguous accepted membership')
    if any(family[k] != ctx[k] for k in ('task_id', 'scope', 'conditions')):
        raise EvidenceError('family recovery cannot broaden task, scope or conditions')
    related, conflicts, foreign_lifecycle = [], [], []
    def originals(member):
        identities = set()
        for ref in member['problem_refs'] + member['evidence_refs']:
            if 'member' in ref:
                material = read_material(workspace, ref)
                identities.add(canonical({k: material['ref'][k] for k in ('sha256', 'bytes')}))
                continue
            if ref['source_id'] == source_id:
                body = journal.get(ref['record_id'])
                if digest(body) != ref['record_digest']:
                    raise EvidenceError('family original record changed')
                if ref['record_id'].startswith('artifact-'):
                    journal.artifact_path(Artifact(**body))
            else:
                body = read_material(workspace, ref)['record']
            identities.add(canonical({k: body[k] for k in ('sha256', 'bytes')})
                           if 'sha256' in body else canonical(body))
        return identities
    current_originals = [originals(m) for m in current]
    for member in members:
        if not member['accepted'] or member['opportunity_ref'] == result['opportunity_ref']:
            continue
        if any(not m['accepted'] and m['opportunity_ref'] == member['opportunity_ref'] for m in members):
            conflicts.append(member['opportunity_ref'])
            continue
        if member['opportunity_ref']['source_id'] != source_id:
            # The resolver's history bound is the explicit local journal. Reading
            # a foreign opportunity through a workspace does not inspect its
            # source's terminal history; absence here must never mean open there.
            unknown = {'opportunity_ref': member['opportunity_ref'], 'state': 'unknown',
                       'coverage': 'external_lifecycle_not_checked',
                       'reason': 'current journal cannot establish the external opportunity head'}
            if unknown not in foreign_lifecycle:
                foreign_lifecycle.append(unknown)
            continue
        prior = resolve_opportunity_state(journal, member['opportunity_ref'], source_id=source_id,
                                           external_workspace=workspace)
        if prior['terminal']:
            related.append({'state': prior, 'same_original_inputs':
                            any(originals(member) == known for known in current_originals)})
    result['family'] = {'ref': family_ref, 'related_terminals': related, 'conflicting_member_refs': conflicts,
                        'foreign_lifecycle': foreign_lifecycle,
                        'meaning': 'reviewed relation; no transfer or scientific benefit inferred'}
    if conflicts or foreign_lifecycle:
        result.update(state='family_review_required', proposal_permission='not_granted')
    elif related and not result.get('lifecycle', {}).get('history_refs'):
        result.update(state='family_terminal' if all(r['same_original_inputs'] for r in related)
                      else 'family_reconsideration_required', proposal_permission='not_granted')


def _register_opportunity(journal, context_ref, directory, source_id, source_kind, ctx):
    from .continuation import record_content
    from .method_revision import journal_ref
    key = ctx['opportunity_key']
    existing = [(r,b) for r,b in _saved(journal, OPPORTUNITY, source_id) if b['opportunity_key'] == key]
    if len(existing) > 1:
        raise EvidenceError('conflicting opportunities require review')
    if existing:
        ref = existing[0][0]
    else:
        body = {k: ctx[k] for k in ('task_id','scope','conditions','method_refs')}
        body.update(schema=OPPORTUNITY, context_ref=context_ref, opportunity_key=key, supersedes=[])
        ref = journal_ref(journal, record_content(journal, body, directory, source_id=source_id, source_kind=source_kind), source_id=source_id)
    patches = [r for r,b in _saved(journal, PATCH, source_id) if b['opportunity_ref'] == ref]
    return {'state': 'already_proposed' if patches else 'resume_pending' if existing else 'new',
            'opportunity_ref': ref, 'request_id': 'revision-' + key, 'patch_refs': patches}


def apply_workflow_edits(workflow, edits, *, base_ref):
    """Pure edits to an optional graph. Exact before values detect stale/conflicting edits."""
    from .composition import validate_workflow
    result = validate_workflow(workflow)
    for edit in edits:
        if set(edit) != {'collection','before','after'} or edit['collection'] not in {'nodes','edges','method_refs'}:
            raise EvidenceError('edits require nodes/edges/method_refs and exact before/after')
        values = result[edit['collection']]
        before, after = edit['before'], edit['after']
        if before is None and after is None or before == after:
            raise EvidenceError('empty workflow edit')
        if before is not None:
            if values.count(before) != 1:
                raise EvidenceError('stale or conflicting workflow edit')
            index = values.index(before)
            values.pop(index)
        else:
            index = len(values)
        if after is not None:
            if after in values:
                raise EvidenceError('duplicate workflow element')
            values.insert(index, after)
    result['supersedes'] = [validate_ref(base_ref)]
    return validate_workflow(result)


def build_method_patch(journal, *, context_ref, opportunity_ref, base_method_ref, candidate_method_ref,
                       expected_change, source_id, workflow_before_ref=None, workflow_edits=()):
    """Produce a checked manifest plus optional new workflow; publication is explicit."""
    ctx = _local(journal, context_ref, source_id, CONTEXT)
    base = _local(journal, base_method_ref, source_id, 'etalon-method-version/1')
    candidate = _local(journal, candidate_method_ref, source_id, 'etalon-method-version/1')
    for method in (base, candidate):
        content = method['content_ref']
        if set(content) == {'artifact_ref'}:
            original = journal.get(content['artifact_ref'])
            journal.artifact_path(Artifact(**original))
        elif set(content) == {'manifest_path', 'manifest_sha256'}:
            from etalon.skills import verify_snapshot
            verify_snapshot(content, resolver=journal.resolver)
        else:
            raise EvidenceError('patch requires complete local Artifact or verified bundle content')
    if ctx['status'] != 'ready_for_revision' or ctx['target_ref'] != base_method_ref:
        raise EvidenceError('patch requires complete context for its exact base')
    if base_method_ref['record_id'] not in candidate['parents'] or base_method_ref == candidate_method_ref:
        raise EvidenceError('candidate must descend from base')
    if not set(base['roles']) & set(candidate['roles']):
        raise EvidenceError('candidate role differs from base')
    if base['content_ref'] == candidate['content_ref']:
        raise EvidenceError('candidate content must change')
    opportunity = _local(journal, opportunity_ref, source_id, OPPORTUNITY)
    if opportunity['opportunity_key'] != ctx['opportunity_key']:
        raise EvidenceError('opportunity and context differ')
    workflow = None
    if workflow_before_ref:
        before = _local(journal, workflow_before_ref, source_id, 'etalon-research-workflow/1')
        workflow = apply_workflow_edits(before, list(workflow_edits), base_ref=workflow_before_ref)
    elif workflow_edits:
        raise EvidenceError('edits need a base workflow')
    payload = {k: ctx[k] for k in ('task_id','scope','conditions')}
    payload.update(schema=PATCH, context_ref=context_ref, opportunity_ref=opportunity_ref,
        base_method_ref=base_method_ref, candidate_method_ref=candidate_method_ref,
        method_refs=[base_method_ref,candidate_method_ref], workflow_before_ref=workflow_before_ref,
        workflow_after_ref=None, workflow_edits=list(workflow_edits), expected_change=expected_change, supersedes=[])
    # Caller publishes workflow first, then fills workflow_after_ref and record_content.
    return {'patch': payload, 'workflow': workflow}


def assess_revision(journal, *, patch_ref, plan_ref, observed_step_refs=(), source_id):
    """Check normal-use receipts against the plan; return gaps, never launch a probe."""
    patch = _local(journal, patch_ref, source_id, PATCH)
    plan = _local(journal, plan_ref, source_id, 'etalon-research-step/1')
    if plan.get('operation') != 'evaluation_plan':
        raise EvidenceError('expected an evaluation plan')
    d = plan['detail']
    if d['subjects'] != [patch['candidate_method_ref']] or d['comparators'] != [patch['base_method_ref']]:
        raise EvidenceError('comparison does not bind exact patch versions')
    initial = _local(journal, d['initial_conditions_ref'], source_id)
    if initial.get('conditions') != patch['conditions']:
        raise EvidenceError('comparison initial conditions differ from patch')
    missing, results, costs = [], [], []
    if not observed_step_refs:
        missing.append('candidate has no observed normal execution; wait for use or supply existing receipts')
    for ref in _unique(observed_step_refs):
        step = _local(journal, ref, source_id, 'etalon-research-step/1')
        detail = step['detail']
        if step.get('operation') != 'node' or detail.get('state') not in {'completed','reused','failed','partial'}:
            missing.append(ref['record_id'] + ': no terminal normal-use observation')
            continue
        if patch['workflow_after_ref'] and detail.get('workflow_ref') != patch['workflow_after_ref']:
            missing.append(ref['record_id'] + ': workflow differs')
        uses = [_local(journal, u, source_id, 'etalon-method-use/1') for u in detail['use_refs']]
        attributed = [u for u in uses if any(m['method_version_ref'] == patch['candidate_method_ref']['record_id'] for m in u['selected_methods'])]
        if not attributed or any(any(u['host_conditions'].get(k) != v for k,v in patch['conditions'].items()) for u in attributed):
            missing.append(ref['record_id'] + ': method use or conditions do not match')
        if not detail['receipt_refs'] or not step['accounting_refs']:
            missing.append(ref['record_id'] + ': missing receipt/accounting')
        results.extend(step['output_refs'] + detail['receipt_refs'])
        costs.extend(step['accounting_refs'])
    payload = {k: patch[k] for k in ('task_id','scope','conditions','method_refs')}
    payload.update(schema=ASSESSMENT, patch_ref=patch_ref, plan_ref=plan_ref,
        observed_step_refs=_unique(observed_step_refs), result_refs=_unique(results), accounting_refs=_unique(costs),
        missing=missing, status='awaiting_evidence' if missing else 'ready_for_evaluation', supersedes=[])
    return validate_content(payload)


def record_experience(journal, *, patch_ref, outcome, lesson, invalid_when, capability_id,
                      support_refs, counter_refs=(), evaluation_ref=None, resolution_ref=None,
                      supersedes=(), directory, source_id, source_kind):
    from .continuation import record_content
    from .method_revision import journal_ref
    patch = _local(journal, patch_ref, source_id, PATCH)
    payload = {k: patch[k] for k in ('task_id','scope','conditions','method_refs')}
    payload.update(schema=EXPERIENCE, patch_ref=patch_ref, evaluation_ref=evaluation_ref,
        resolution_ref=resolution_ref, outcome=outcome, lesson=lesson, invalid_when=invalid_when,
        capability_id=capability_id, support_refs=_unique(support_refs), counter_refs=_unique(counter_refs), supersedes=list(supersedes))
    rid = record_content(journal, payload, directory, source_id=source_id, source_kind=source_kind)
    return journal_ref(journal, rid, source_id=source_id)


def retrieve_experience(workspace, *, source_ids, capability_id, conditions, limit=10, cursor=None,
                        related_condition_keys=()):
    """Bounded exact-condition retrieval; retain negative/conflicting entries for host review."""
    from .evidence import query_evidence
    _conditions(conditions)
    if not isinstance(source_ids, list) or not source_ids:
        raise EvidenceError('explicit source scope required')
    page = query_evidence(workspace, query=EXPERIENCE,
        filters={'source_ids':source_ids, 'capability_ids':[capability_id]}, limit=limit, cursor=cursor)
    if not isinstance(related_condition_keys, (list, tuple)) or any(
            not isinstance(k, str) or k not in conditions for k in related_condition_keys):
        raise EvidenceError('related keys must explicitly name query conditions')
    matches, excluded, related = [], [], []
    for item in page['items']:
        content = read_material(workspace, item['ref'])['content']
        if not isinstance(content, dict) or content.get('schema') != EXPERIENCE:
            continue
        validate_content(content)
        unmet = [k for k,v in content['conditions'].items() if conditions.get(k) != v]
        if unmet:
            excluded.append({'ref':item['ref'], 'unmatched_conditions':unmet})
            if related_condition_keys and all(content['conditions'].get(k) == conditions[k] for k in related_condition_keys):
                related.append({'ref':item['ref'], 'experience':content, 'unmatched_conditions':unmet,
                                'applicability':'hypothesis_only; requires new evaluation'})
        else:
            matches.append({'ref':item['ref'], 'experience':content})
    return {'matches':matches, 'excluded':excluded, 'related':related, 'coverage':page['coverage'],
            'warnings':page['warnings'], 'next_cursor':page.get('next_cursor'),
            'selection':'host_decides; adoption is not implied; inspect supersedes and counterevidence'}


def validate_local_links(journal, content, source_id):
    """Validation also runs for callers publishing hand-built payloads."""
    schema = content['schema']
    if schema == OPPORTUNITY:
        ctx = _local(journal, content['context_ref'], source_id, CONTEXT)
        if ctx['status'] != 'ready_for_revision' or content['opportunity_key'] != ctx['opportunity_key']:
            raise EvidenceError('opportunity requires complete matching context')
    elif schema == PATCH:
        built = build_method_patch(journal, context_ref=content['context_ref'], opportunity_ref=content['opportunity_ref'],
            base_method_ref=content['base_method_ref'], candidate_method_ref=content['candidate_method_ref'],
            expected_change=content['expected_change'], source_id=source_id,
            workflow_before_ref=content['workflow_before_ref'], workflow_edits=content['workflow_edits'])
        if built['workflow'] is not None and built['workflow'] != _local(journal, content['workflow_after_ref'], source_id, 'etalon-research-workflow/1'):
            raise EvidenceError('workflow result does not match exact edits')
        for key in ('task_id','scope','conditions','method_refs'):
            if content[key] != built['patch'][key]:
                raise EvidenceError('patch changes its bound context')
    elif schema == ASSESSMENT:
        expected = assess_revision(journal, patch_ref=content['patch_ref'], plan_ref=content['plan_ref'],
                                   observed_step_refs=content['observed_step_refs'], source_id=source_id)
        if expected != content:
            raise EvidenceError('assessment does not match actual use records')
    elif schema == EXPERIENCE:
        patch = _local(journal, content['patch_ref'], source_id, PATCH)
        for key in ('scope','conditions','method_refs'):
            if content[key] != patch[key]:
                raise EvidenceError('experience cannot silently broaden patch scope')
        if content['outcome'] != 'unresolved':
            evaluation = _local(journal, content['evaluation_ref'], source_id, 'etalon-method-evaluation/1')
            resolution = _local(journal, content['resolution_ref'], source_id, 'etalon-research-step/1')
            if evaluation['subjects'] != [patch['candidate_method_ref']['record_id']] or evaluation['comparators'] != [patch['base_method_ref']['record_id']]:
                raise EvidenceError('experience evaluation differs from patch')
            if resolution.get('operation') != 'decision' or content['evaluation_ref'] not in resolution['input_refs'] or not any(
                a['disposition'] == content['outcome'] for a in resolution['detail']['alternatives']):
                raise EvidenceError('experience outcome differs from resolution')
            selected = patch['candidate_method_ref'] if content['outcome'] == 'adopt' else patch['base_method_ref']
            if resolution['method_refs'] != [selected] or (content['outcome']=='adopt' and (
                    not evaluation['result_refs'] or not evaluation['accounting_refs'])):
                raise EvidenceError('experience adoption lacks evaluated selection and accounting')
        for ref in content['supersedes']:
            prior = _local(journal, ref, source_id, EXPERIENCE)
            if prior['scope'] != content['scope'] or prior['conditions'] != content['conditions'] or prior['capability_id'] != content['capability_id']:
                raise EvidenceError('experience successor changes applicability')
