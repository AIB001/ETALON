"""Local research kernel types; not a replacement for PRISM's producer wire contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from etalon.active.schema import canonical, digest, finite


def nonempty(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonempty text required')


class Record:
    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def id(self) -> str:
        return type(self).__name__.lower() + '-' + digest(self.as_dict())


@dataclass(frozen=True)
class Artifact(Record):
    path: str
    sha256: str
    bytes: int
    source_kind: str
    role: str
    # JSON bindings may include exact evidence_refs for transitive visibility.
    # This preserves the existing serialized Artifact format and digest rules.
    bindings: dict[str, Any]
    parents: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.path, str) or not (
                PurePosixPath(self.path).is_absolute() or PureWindowsPath(self.path).is_absolute()):
            raise ValueError('artifact path must be absolute')
        if len(self.sha256) != 64 or any(c not in '0123456789abcdef' for c in self.sha256):
            raise ValueError('artifact requires SHA256')
        if type(self.bytes) is not int or self.bytes < 0:
            raise ValueError('artifact byte count must be nonnegative')
        if self.source_kind not in {'fixture', 'existing_real_artifact', 'new_execution'}:
            raise ValueError('explicit artifact source required')
        if self.role not in {'diagnostic', 'independent_validation', 'sealed_holdout'}:
            raise ValueError('explicit evidence role required')
        canonical(self.as_dict())


@dataclass(frozen=True)
class Change(Record):
    section: str
    path: tuple[str, ...]
    before: Any
    after: Any
    evidence: tuple[str, ...]
    reason: str

    def __post_init__(self):
        if self.section not in {'design', 'preparation', 'evaluation', 'budget'} or not self.path:
            raise ValueError('change must name a concrete supported configuration field')
        for part in self.path: nonempty(part)
        if self.before == self.after or not self.evidence:
            raise ValueError('change requires a different value and supporting evidence')
        nonempty(self.reason)
        canonical(self.as_dict())


@dataclass(frozen=True)
class Coupling(Record):
    shared_artifacts: tuple[str, ...] = ()
    paired_by: tuple[str, ...] = ()
    resampled: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    def __post_init__(self):
        if set(self.paired_by) & set(self.resampled):
            raise ValueError('a variable cannot be both fixed/paired and independently resampled')
        if (self.shared_artifacts or self.paired_by) and not self.assumptions:
            raise ValueError('coupling requires explicit correlation/identification assumptions')


@dataclass(frozen=True)
class Experiment(Record):
    task_id: str
    kind: str
    bindings: dict[str, str]
    inputs: tuple[str, ...]
    method_id: str
    protocol_instance_id: str
    readouts: tuple[str, ...]
    coupling: Coupling
    changes: tuple[Change, ...] = ()
    action_ids: tuple[str, ...] = ()

    def __post_init__(self):
        for value in (self.task_id, self.method_id, self.protocol_instance_id): nonempty(value)
        if self.kind not in {'file_audit', 'reanalysis', 'replication', 'intervention', 'recovery'}:
            raise ValueError('unknown research operation kind')
        if not self.inputs or not self.readouts or len(set(self.readouts)) != len(self.readouts):
            raise ValueError('explicit inputs and distinct readouts required')
        if not set(self.coupling.shared_artifacts) <= set(self.inputs):
            raise ValueError('shared artifacts must be bound inputs')
        if self.kind == 'intervention' and not self.changes:
            raise ValueError('an intervention requires concrete changes')
        if self.kind in {'recovery', 'reanalysis'} and self.changes:
            raise ValueError('changed conditions require an intervention, not recovery/reanalysis')
        if self.kind == 'replication' and not self.coupling.resampled:
            raise ValueError('independent repetition must specify the independently resampled variables')
        canonical(self.as_dict())


@dataclass(frozen=True)
class Observation(Record):
    experiment_id: str
    status: str
    values: dict[str, Any]
    units: dict[str, str]
    evidence: tuple[str, ...]
    reason: str = ''
    measurement_method_id: str | None = None

    def __post_init__(self):
        if self.status not in {'measured', 'failed', 'missing', 'rejected', 'unsupported', 'not_evaluated'}:
            raise ValueError('unknown observation status')
        if self.status == 'measured':
            if not self.values or set(self.values) != set(self.units) or not self.evidence:
                raise ValueError('measured values require units and evidence')
        elif self.values or self.units or not self.reason:
            raise ValueError('missing/failure must retain a reason and cannot become a numeric label')
        canonical(self.as_dict())


@dataclass(frozen=True)
class Contrast(Record):
    task_id: str
    question: str
    cells: dict[str, str | None]
    readout: str
    coupling: Coupling
    method_id: str

    def __post_init__(self):
        for value in (self.task_id, self.question, self.readout, self.method_id): nonempty(value)
        if set(self.cells) != {'00', '01', '10', '11'}:
            raise ValueError('factorial contrast explicitly records all four cells, including missing cells')
        selected = [v for v in self.cells.values() if v is not None]
        if len(selected) != len(set(selected)):
            raise ValueError('one observation cannot impersonate two intervention cells')


@dataclass(frozen=True)
class Revision(Record):
    task_id: str
    base_configuration_sha256: str
    changes: tuple[Change, ...]
    scope: str
    contrast_ids: tuple[str, ...]

    def __post_init__(self):
        if not self.changes or not self.contrast_ids:
            raise ValueError('revision needs executable field changes and a recorded comparison')
        nonempty(self.scope)
        keys = [(c.section, c.path) for c in self.changes]
        if len(set(keys)) != len(keys):
            raise ValueError('revision cannot change the same field twice')


@dataclass(frozen=True)
class TaskSplit(Record):
    development: tuple[str, ...]
    validation: tuple[str, ...]
    transfer: tuple[str, ...]
    frozen_initial_configuration: dict[str, str]
    independent_objective: str

    def __post_init__(self):
        tasks = self.development + self.validation + self.transfer
        # A task-local improvement need not claim cross-task transfer. Keep the
        # old wire format and identities; an empty transfer set states no test.
        if not all((self.development, self.validation)) or len(set(tasks)) != len(tasks):
            raise ValueError('development/validation tasks must be nonempty and all groups disjoint')
        if set(tasks) != set(self.frozen_initial_configuration):
            raise ValueError('every frozen task needs an initial configuration identity')
        nonempty(self.independent_objective)


def _references(values, name):
    if not isinstance(values, (list, tuple)):
        raise ValueError(name + ' must be a sequence')
    for value in values:
        nonempty(value)
    if len(set(values)) != len(values):
        raise ValueError(name + ' must not contain duplicate references')


@dataclass(frozen=True)
class MethodVersion(Record):
    """Content identity and provenance; no claim that a host used this method."""
    name: str
    roles: tuple[str, ...]
    content_ref: dict
    parents: tuple[str, ...] = ()
    dependencies: dict = field(default_factory=dict)
    entrypoint: str | None = None
    change_summary: str = ''
    evidence_refs: tuple[str, ...] = ()
    verification_basis: str = 'host_reported'
    schema: str = field(default='etalon-method-version/1', init=False)

    def __post_init__(self):
        nonempty(self.name)
        _references(self.roles, 'roles')
        _references(self.parents, 'parents')
        _references(self.evidence_refs, 'evidence_refs')
        if not self.roles or not isinstance(self.content_ref, dict) or not self.content_ref:
            raise ValueError('method roles and content reference required')
        if not isinstance(self.dependencies, dict):
            raise ValueError('dependencies must name known versions or explicit null unknowns')
        if self.verification_basis not in {'host_reported', 'content_verified'}:
            raise ValueError('unknown verification basis')
        canonical(self.as_dict())


@dataclass(frozen=True)
class MethodUse(Record):
    """Append-only attribution, including partial adoption and choosing no method."""
    subject_refs: tuple[dict, ...]
    selected_methods: tuple[dict, ...] = ()
    usage_method_ref: str | None = None
    visible_evidence_refs: tuple[str, ...] = ()
    host_conditions: dict = field(default_factory=dict)
    basis: str = 'host_reported'
    reason: str | None = None
    trigger_event_ref: dict | None = None
    parents: tuple[str, ...] = ()
    schema: str = field(default='etalon-method-use/1', init=False)

    def __post_init__(self):
        if not isinstance(self.subject_refs, (tuple, list)) or not self.subject_refs:
            raise ValueError('at least one subject reference required')
        for ref in self.subject_refs:
            if not isinstance(ref, dict) or ref.get('kind') not in {'task', 'request', 'action', 'result'}:
                raise ValueError('typed subject reference required')
            nonempty(ref.get('id'))
            nonempty(ref.get('scope'))
        if not isinstance(self.selected_methods, (tuple, list)):
            raise ValueError('selected_methods must be a sequence')
        for ref in self.selected_methods:
            if not isinstance(ref, dict):
                raise ValueError('selected method must include method_version_ref')
            nonempty(ref.get('method_version_ref'))
            if 'adoption' in ref and ref['adoption'] not in {'full', 'partial'}:
                raise ValueError('method adoption must be full or partial; no selection uses an empty list')
            for field_name in ('used_parts', 'omitted_parts'):
                if field_name in ref and (not isinstance(ref[field_name], (list, tuple)) or
                        any(not isinstance(p, str) or not p.strip() for p in ref[field_name])):
                    raise ValueError('method parts must be explicit nonempty strings')
            if ref.get('adoption') == 'partial' and not ref.get('used_parts'):
                raise ValueError('partial adoption requires explicit used_parts')
        _references(self.visible_evidence_refs, 'visible_evidence_refs')
        _references(self.parents, 'parents')
        if self.usage_method_ref is not None:
            nonempty(self.usage_method_ref)
        if self.basis not in {'host_reported', 'tool_observed'}:
            raise ValueError('unknown method use basis')
        if not isinstance(self.host_conditions, dict):
            raise ValueError('host_conditions must be an object with null for unknown fields')
        canonical(self.as_dict())


@dataclass(frozen=True)
class MethodEvaluation(Record):
    """A bounded comparison; recording never promotes a candidate or updates M."""
    subjects: tuple[str, ...]
    comparators: tuple[str, ...]
    protocol_ref: str
    task_split_ref: str
    initial_conditions_ref: str
    result_refs: tuple[str, ...]
    accounting_refs: tuple[str, ...]
    assessment_role: str
    conclusion: str
    scope: str
    schema: str = field(default='etalon-method-evaluation/1', init=False)

    def __post_init__(self):
        for name in ('subjects', 'comparators', 'result_refs', 'accounting_refs'):
            _references(getattr(self, name), name)
        if not self.subjects or not self.comparators:
            raise ValueError('evaluation requires explicit subjects and comparators')
        for name in ('protocol_ref', 'task_split_ref', 'initial_conditions_ref', 'conclusion', 'scope'):
            nonempty(getattr(self, name))
        if self.assessment_role not in {'diagnostic', 'independent_validation'}:
            raise ValueError('unknown method assessment role')
        canonical(self.as_dict())


METHOD_RECORD_TYPES = {
    'etalon-method-version/1': MethodVersion,
    'etalon-method-use/1': MethodUse,
    'etalon-method-evaluation/1': MethodEvaluation,
}


def method_record_from_dict(payload: dict) -> MethodVersion | MethodUse | MethodEvaluation:
    """Strict public wire parser: no arbitrary research metadata injection."""
    if not isinstance(payload, dict) or payload.get('schema') not in METHOD_RECORD_TYPES:
        raise ValueError('one of the three supported method schemas is required')
    cls = METHOD_RECORD_TYPES[payload['schema']]
    try:
        return cls(**{key: value for key, value in payload.items() if key != 'schema'})
    except TypeError as exc:
        raise ValueError('invalid method record fields: ' + str(exc)) from exc
