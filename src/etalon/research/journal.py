"""Research provenance in the existing campaign transaction; never a second cost ledger."""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.boundary.prism_v1 import file_identity
from .schema import (
    Artifact, Contrast, Coupling, Experiment, MethodEvaluation, MethodUse, MethodVersion,
    Observation, Record, Revision, TaskSplit, method_record_from_dict,
)

PREFIX = 'research:'


class ResearchJournal:
    def __init__(self, store: CampaignStore, *, resolver=None):
        self.store = store
        self.resolver = resolver
        store.configuration()  # Research belongs to a configured campaign, not a shadow database.

    @staticmethod
    def _get(db, identifier: str) -> dict:
        row = db.execute('SELECT body FROM metadata WHERE key=?', (PREFIX + identifier,)).fetchone()
        if row is None:
            raise StateError('unknown research record: ' + identifier)
        return json.loads(row[0])

    def get(self, identifier: str) -> dict:
        with self.store.connection() as db:
            return self._get(db, identifier)

    @staticmethod
    def _put(db, identifier: str, body: dict) -> str:
        value = canonical(body)
        old = db.execute('SELECT body FROM metadata WHERE key=?', (PREFIX + identifier,)).fetchone()
        if old and old[0] != value:
            raise StateError('research identity cannot be rewritten')
        if not old:
            db.execute('INSERT INTO metadata VALUES (?,?)', (PREFIX + identifier, value))
            CampaignStore._event(db, 'research_recorded', {'id': identifier, 'record': body})
        return identifier

    def artifact_path(self, artifact: Artifact) -> Path:
        """Verify current bytes without replacing the artifact's historical locator."""
        if self.resolver is None:
            path = Path(artifact.path)
        else:
            from .evidence_refs import resolve_artifact
            try:
                path = resolve_artifact(artifact, resolver=self.resolver)
            except ValueError as exc:
                raise StateError('artifact hash/size or location mismatch') from exc
        if file_identity(path) != {'sha256': artifact.sha256, 'bytes': artifact.bytes}:
            raise StateError('artifact hash/size mismatch')
        return path

    def _artifacts(self, db, identifiers, *, diagnostic_inputs=False):
        result = []
        for identifier in identifiers:
            record = ResearchJournal._get(db, identifier)
            if not identifier.startswith('artifact-'):
                raise StateError('evidence reference must identify an artifact')
            artifact = Artifact(**record)
            try:
                self.artifact_path(artifact)
            except StateError as exc:
                raise StateError('research artifact changed: ' + identifier) from exc
            if diagnostic_inputs and artifact.role != 'diagnostic':
                raise StateError('independent/holdout reference cannot enter diagnostic/design inputs')
            result.append(artifact)
        return result

    def record(self, value: Record) -> str:
        if not isinstance(value, (Artifact, Experiment, Observation, Contrast, Revision, TaskSplit,
                                  MethodVersion, MethodUse, MethodEvaluation)):
            raise TypeError('unsupported research record type')
        # Frozen dataclasses may contain mutable nested dicts. Reparse a canonical snapshot
        # so validation, digest and persisted bytes all refer to the same payload.
        if isinstance(value, (MethodVersion, MethodUse, MethodEvaluation)):
            value = method_record_from_dict(json.loads(canonical(value.as_dict())))
        with self.store.connection(write=True) as db:
            if isinstance(value, Artifact):
                self.artifact_path(value)
                self._artifacts(db, value.parents)
            elif isinstance(value, Experiment):
                self._artifacts(db, value.inputs, diagnostic_inputs=True)
                for change in value.changes: self._artifacts(db, change.evidence, diagnostic_inputs=True)
                for action in value.action_ids:
                    if not db.execute('SELECT 1 FROM actions WHERE id=?', (action,)).fetchone():
                        raise StateError('execution costs must reference a real existing campaign action')
            elif isinstance(value, Observation):
                experiment = self._get(db, value.experiment_id)
                if not value.experiment_id.startswith('experiment-') or not set(value.values) <= set(experiment['readouts']):
                    raise StateError('observation does not match its experiment readouts')
                self._artifacts(db, value.evidence)
            elif isinstance(value, Contrast):
                self._artifacts(db, value.coupling.shared_artifacts)
                experiments = []
                for observation in value.cells.values():
                    if observation is None: continue
                    body = self._get(db, observation)
                    if not observation.startswith('observation-'):
                        raise StateError('contrast cells must reference observations')
                    experiment = self._get(db, body['experiment_id'])
                    measurement_method = body.get('measurement_method_id') or experiment['method_id']
                    if experiment['task_id'] != value.task_id or measurement_method != value.method_id:
                        raise StateError('contrast requires a common task and comparable measurement definition')
                    if not set(value.coupling.shared_artifacts) <= set(experiment['inputs']):
                        raise StateError('claimed shared artifact is not actually shared by contrast cells')
                    experiments.append(experiment)
                for variable in value.coupling.paired_by:
                    values = [e['bindings'].get(variable) for e in experiments]
                    if None in values or len(set(values)) > 1:
                        raise StateError('paired variable does not match bound experiment identities')
            elif isinstance(value, Revision):
                for contrast in value.contrast_ids:
                    record = self._get(db, contrast)
                    if not contrast.startswith('contrast-') or record['task_id'] != value.task_id:
                        raise StateError('revision requires a comparison for the same task')
                for change in value.changes: self._artifacts(db, change.evidence, diagnostic_inputs=True)
            elif isinstance(value, MethodVersion):
                for parent in value.parents:
                    self._method(db, parent, MethodVersion)
                for identifier in value.evidence_refs:
                    self._reference(db, identifier)
                if value.verification_basis == 'content_verified':
                    # C owns snapshot/path validation. This is byte verification only.
                    from etalon.skills import verify_snapshot
                    verify_snapshot(value.content_ref, resolver=self.resolver)
            elif isinstance(value, MethodUse):
                self._validate_method_use(db, value)
            elif isinstance(value, MethodEvaluation):
                self._validate_method_evaluation(db, value)
            return self._put(db, value.id, value.as_dict())

    @staticmethod
    def _method(db, identifier, cls):
        body = ResearchJournal._get(db, identifier)
        value = method_record_from_dict(body)
        if not isinstance(value, cls) or value.id != identifier:
            raise StateError('method reference has wrong type or identity: ' + identifier)
        return body

    def _reference(self, db, identifier):
        """Resolve existing research evidence without admitting it as a scientific label."""
        body = ResearchJournal._get(db, identifier)
        if identifier.startswith('artifact-'):
            self._artifacts(db, (identifier,))
        return body

    def resolve_method_use_refs(self, identifiers) -> list[dict]:
        """A's optional attribution check; empty references remain legal."""
        if not isinstance(identifiers, (tuple, list)):
            raise ValueError('method_use_refs must be a sequence')
        with self.store.connection() as db:
            return [self._method(db, identifier, MethodUse) for identifier in identifiers]

    def _validate_method_use(self, db, value):
        for ref in value.selected_methods:
            self._method(db, ref['method_version_ref'], MethodVersion)
        if value.usage_method_ref:
            self._method(db, value.usage_method_ref, MethodVersion)
        for parent in value.parents:
            self._method(db, parent, MethodUse)
        for identifier in value.visible_evidence_refs:
            self._reference(db, identifier)
        for ref in value.subject_refs:
            scope = Path(ref['scope'])
            if self.resolver is not None:
                scope = self.resolver.resolve_source(ref['scope'])
            if scope.resolve() != self.store.path:
                raise StateError('method subject scope must identify this campaign database')
            if ref['kind'] == 'action':
                if not db.execute('SELECT 1 FROM actions WHERE id=?', (ref['id'],)).fetchone():
                    raise StateError('method subject action does not exist')
            elif ref['kind'] == 'result':
                if not db.execute('SELECT 1 FROM observations WHERE action_id=?', (ref['id'],)).fetchone():
                    self._reference(db, ref['id'])
        if value.trigger_event_ref is not None:
            ref = value.trigger_event_ref
            if (not isinstance(ref, dict) or not isinstance(ref.get('log_ref'), dict)
                    or not isinstance(ref.get('log_id'), str)):
                raise StateError('trigger event requires its log locator and log identity')
            if type(ref.get('sequence')) is not int or ref['sequence'] < 1:
                raise StateError('trigger event requires a positive integer sequence')
            from etalon.runtime.api import read_events
            page = read_events(ref['log_ref'], after_sequence={
                'log_id': ref['log_id'], 'after_sequence': ref['sequence'] - 1}, limit=1)
            event = page['events'][0] if page['events'] else None
            if (event is None or event['sequence'] != ref['sequence']
                    or event['kind'] != ref.get('event_type')):
                raise StateError('trigger event does not match a committed event')
        if value.basis == 'tool_observed':
            # A selected version in a request, a returned bundle or a completed action
            # does not demonstrate use of its instructions. No execution telemetry
            # currently attests that stronger statement.
            raise StateError('tool_observed method use requires execution telemetry; only host_reported is supported')

    @staticmethod
    def _record_sequence(db, identifier):
        for row in db.execute("SELECT sequence,body FROM events WHERE kind='research_recorded' ORDER BY sequence"):
            if json.loads(row['body']).get('id') == identifier:
                return row['sequence']
        raise StateError('research record lacks a committed event: ' + identifier)

    def _validate_method_evaluation(self, db, value):
        for identifier in (*value.subjects, *value.comparators):
            self._method(db, identifier, MethodVersion)
        self._artifacts(db, (value.protocol_ref, value.initial_conditions_ref))
        split = self._get(db, value.task_split_ref)
        if not value.task_split_ref.startswith('tasksplit-') or TaskSplit(**split).id != value.task_split_ref:
            raise StateError('method evaluation requires an existing TaskSplit')
        results = [(identifier, self._reference(db, identifier)) for identifier in value.result_refs]
        # Accounting points to existing actions/resource executions or verified artifacts;
        # it does not mint a second ledger or infer that missing costs are zero.
        for identifier in value.accounting_refs:
            if db.execute('SELECT 1 FROM actions WHERE id=?', (identifier,)).fetchone():
                continue
            row = db.execute('SELECT body FROM metadata WHERE key=?', ('budget:execution:' + identifier,)).fetchone()
            if row:
                continue
            record = self._reference(db, identifier)
            if (not identifier.startswith('artifact-')
                    or record.get('bindings', {}).get('kind') not in {
                        'accounting', 'cost_receipt', 'resource_accounting'}):
                raise StateError('accounting reference must identify an action, resource execution or accounting artifact')
        if value.assessment_role == 'diagnostic':
            return
        if not results or not value.accounting_refs:
            raise StateError('independent validation requires actual results and accounting references')
        binding = {
            'subjects': list(value.subjects), 'comparators': list(value.comparators),
            'protocol_ref': value.protocol_ref, 'task_split_ref': value.task_split_ref,
            'initial_conditions_ref': value.initial_conditions_ref,
        }
        for identifier, result in results:
            if not identifier.startswith('validation-'):
                raise StateError('independent validation requires an existing frozen-policy validation receipt')
            policy_id = result.get('policy_id', '')
            policy = self._get(db, policy_id)
            if (not policy_id.startswith('phi-') or policy.get('split_id') != value.task_split_ref
                    or policy.get('search_configuration', {}).get('method_evaluation') != binding):
                raise StateError('independent validation must bind methods, protocol, split and initial conditions before release')
            task = result.get('task_id')
            if (task not in split['validation'] + split['transfer']
                    or result.get('initial_configuration_sha256') != split['frozen_initial_configuration'].get(task)
                    or not result.get('comparison')):
                raise StateError('independent validation requires a held-out comparison from the frozen initial state')
            evidence = self._artifacts(db, result.get('evidence', ()))
            if not evidence or any(a.role not in {'independent_validation', 'sealed_holdout'} for a in evidence):
                raise StateError('independent validation evidence role required')
            if any(a.bindings.get('policy_id') != policy_id or a.bindings.get('task_id') != task
                   for a in evidence):
                raise StateError('released evidence must bind the exact frozen policy and held-out task')
            frozen_at = self._record_sequence(db, policy_id)
            for ref in (*value.subjects, *value.comparators, value.protocol_ref, value.initial_conditions_ref):
                if self._record_sequence(db, ref) >= frozen_at:
                    raise StateError('method evaluation inputs must be recorded before the policy freeze')
            if any(self._record_sequence(db, artifact.id) <= frozen_at for artifact in evidence):
                raise StateError('already seen evidence cannot be reused as unseen validation')
            hashes = {artifact.sha256 for artifact in evidence}
            for row in db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:artifact-%'"):
                earlier = json.loads(row['body'])
                if (earlier['sha256'] in hashes
                        and self._record_sequence(db, row['key'].removeprefix(PREFIX)) <= frozen_at):
                    raise StateError('already seen artifact bytes cannot be relabelled as independent validation')
            if self._record_sequence(db, identifier) <= frozen_at:
                raise StateError('validation receipt must follow the policy freeze')

    def accounting(self, experiment_id: str) -> dict:
        """Actual costs stay in CampaignStore. Reuse attribution never incurs a second charge."""
        experiment = self.get(experiment_id)
        actions = {a['id']: a for a in self.store.actions()}
        ids = sorted(set(experiment['action_ids']))
        selected = [actions[key] for key in ids]
        spec, _ = self.store.configuration()
        return {'action_ids': ids, 'existing_ledger_cost': sum(a['cost'] for a in selected),
                'unresolved_action_ids': [a['id'] for a in selected if a['status'] in {'reserved', 'running'}],
                'unit': spec.cost_unit, 'new_execution_cost_from_recording': 0,
                'local_audit_compute_cost': None, 'basis': 'existing ledger attribution; local audit CPU time unmetered'}

    def factorial(self, contrast_id: str) -> dict:
        contrast = self.get(contrast_id)
        cells, unresolved, units = {}, {}, set()
        for cell, identifier in contrast['cells'].items():
            if identifier is None:
                unresolved[cell] = 'missing'
                continue
            observation = self.get(identifier)
            if observation['status'] != 'measured' or contrast['readout'] not in observation['values']:
                unresolved[cell] = observation['status']
                continue
            value = observation['values'][contrast['readout']]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError('factorial contrast requires numeric measurements')
            units.add(observation['units'][contrast['readout']])
            cells[cell] = value
        if len(units) > 1: raise ValueError('contrast units differ')
        return {'contrast_id': contrast_id, 'status': 'unresolved' if unresolved else 'computed',
                'missing_or_failed': unresolved, 'cells': cells,
                'interaction': None if unresolved else cells['11'] - cells['10'] - cells['01'] + cells['00'],
                'unit': next(iter(units), None), 'uncertainty': None,
                'scope': 'descriptive contrast; no independence or causal identification inferred',
                'coupling': contrast['coupling']}

    def apply_revision(self, revision: Revision, configuration: dict) -> dict:
        """Materialize a NEW configuration; no promotion, endpoint rewrite or execution."""
        if digest(configuration) != revision.base_configuration_sha256:
            raise StateError('revision base configuration changed')
        updated = json.loads(canonical(configuration))
        for change in revision.changes:
            node = updated[change.section]
            for part in change.path[:-1]: node = node[part]
            key = change.path[-1]
            if key not in node or canonical(node[key]) != canonical(change.before):
                raise StateError('revision before value does not match the concrete field')
            node[key] = json.loads(canonical(change.after))
        self.record(revision)
        with self.store.connection(write=True) as db:
            receipt = {'revision_id': revision.id, 'configuration': updated,
                       'configuration_sha256': digest(updated), 'status': 'proposed_not_promoted',
                       'requires_independent_validation': True}
            self._put(db, 'configuration-' + digest(receipt), receipt)
        return receipt

    def policy_version(self, split: TaskSplit, search_configuration: dict, *,
                       parent_id: str | None = None, development_evidence: tuple[str, ...] = ()) -> str:
        """Freeze phi's search configuration and lineage; updates are not transfer evidence."""
        self.record(split)
        canonical(search_configuration)
        with self.store.connection(write=True) as db:
            if parent_id:
                parent = self._get(db, parent_id)
                if not parent_id.startswith('phi-') or parent['split_id'] != split.id:
                    raise StateError('policy lineage must preserve the frozen task split')
                if parent['search_configuration'] == search_configuration or not development_evidence:
                    raise StateError('policy update requires a concrete search change and development evidence')
            for identifier in development_evidence:
                observation = self._get(db, identifier)
                experiment = self._get(db, observation['experiment_id'])
                if experiment['task_id'] not in split.development:
                    raise StateError('validation/transfer task cannot train phi')
                self._artifacts(db, observation['evidence'], diagnostic_inputs=True)
            body = {'split_id': split.id, 'parent_id': parent_id, 'search_configuration': search_configuration,
                    'development_evidence': list(development_evidence), 'transfer_status': 'not_evaluated'}
            return self._put(db, 'phi-' + digest(body), body)

    def record_validation(self, policy_id: str, task_id: str, *, initial_configuration_sha256: str,
                          evidence: tuple[str, ...], comparison: dict) -> str:
        """Retain a held-out evaluation record; the caller must supply actual comparisons."""
        with self.store.connection(write=True) as db:
            policy = self._get(db, policy_id)
            split = self._get(db, policy['split_id'])
            if task_id not in split['validation'] + split['transfer']:
                raise StateError('independent validation requires a frozen held-out task')
            if initial_configuration_sha256 != split['frozen_initial_configuration'][task_id]:
                raise StateError('transfer comparison must start from the frozen initial configuration')
            artifacts = self._artifacts(db, evidence)
            if not artifacts or any(a.role not in {'independent_validation', 'sealed_holdout'} for a in artifacts):
                raise StateError('independent evidence role required')
            body = {'policy_id': policy_id, 'task_id': task_id, 'evidence': list(evidence),
                    'comparison': comparison, 'initial_configuration_sha256': initial_configuration_sha256,
                    'scope': 'recorded evaluation only; transfer superiority not automatically certified'}
            return self._put(db, 'validation-' + digest(body), body)


def audit_json(journal: ResearchJournal, artifact: Artifact, *, task_id: str,
               selectors: dict[str, tuple[str, ...]]) -> tuple[str, str]:
    """Consume verified local JSON facts only; does not reinterpret them as affinity labels."""
    identifier = journal.record(artifact)
    if artifact.role != 'diagnostic': raise StateError('holdout cannot be used for diagnostic extraction')
    artifact_location = journal.artifact_path(artifact)
    data = json.loads(artifact_location.read_text(encoding='utf-8'))
    values = {}
    for name, path in selectors.items():
        value = data
        for part in path: value = value[part]
        values[name] = value
    if file_identity(artifact_location) != {'sha256': artifact.sha256, 'bytes': artifact.bytes}:
        raise StateError('artifact changed during audit')
    experiment = Experiment(task_id, 'file_audit', artifact.bindings, (identifier,),
        'json-field-audit/1', digest({'artifact': identifier, 'selectors': selectors}), tuple(selectors),
        Coupling())
    journal.record(experiment)
    observation = Observation(experiment.id, 'measured', values,
                              {name: 'json_value' for name in values}, (identifier,))
    journal.record(observation)
    return experiment.id, observation.id
