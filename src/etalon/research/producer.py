"""Pinned consumer for PRISM-owned diagnostic contracts; executes only its public CPU CLI.

Run this initial adapter in the actual execution host/environment. Cross-host orchestration
can launch that fixed snapshot; remote paths are never silently treated as local files.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from etalon.active.runner import ExecutionPending
from etalon.active.schema import canonical, digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import CLITransport, file_identity, _WRITE_SCRIPT
from .journal import ResearchJournal
from .schema import Artifact, Change, Coupling, Experiment, Observation

SCHEMA_HASHES = {
    'request': 'b947ceed663c91c047ecacdc0cd557f2cf659a861bd1b4744a526681f00a8a72',
    'result': '7c873911ea733ac9cec558acf4e5c495af00ff8114431282f43707c9a67a1cd9',
}


def validate(value: dict, kind: str) -> None:
    from jsonschema import Draft202012Validator
    path = Path(__file__).parent / 'contracts' / f'diagnostic-action-{kind}-v1.schema.json'
    if file_identity(path)['sha256'] != SCHEMA_HASHES[kind]: raise StateError('pinned diagnostic schema changed')
    Draft202012Validator(json.loads(path.read_text())).validate(value)
    canonical(value)


_BIND = """import json,sys
from prism.diagnostic.contract import bind,semantic_request
from prism.design.bundle import identifier
r=bind(json.load(sys.stdin))
print(json.dumps({'request':r,'semantic':semantic_request(r),'execution_id':identifier('execution',{'idempotency_key':r['idempotency_key']})}))
"""
_CLI = """import json,subprocess,sys
p=subprocess.run([sys.executable,'-m','prism.diagnostic',*sys.argv[1:]],capture_output=True,text=True)
print(json.dumps({'exit_code':p.returncode,'response':json.loads(p.stdout),'stderr':p.stderr}))
"""
_RECHECK = """import json,sys
from prism.diagnostic.structure import bound_design
g=bound_design(json.load(sys.stdin))
print(json.dumps({'checked':True,'design':str(g.document.path)}))
"""


class DiagnosticClient:
    def __init__(self, transport: CLITransport):
        if transport.host is not None:
            raise ValueError('run diagnostic consumer on execution host; no implicit remote artifact mapping')
        self.transport = transport

    def capabilities(self):
        result = self.transport._run([self.transport.python, '-m', 'prism.diagnostic', 'capabilities'])
        for key, sha in SCHEMA_HASHES.items():
            if result['schemas'][key]['sha256'] != sha:
                raise StateError('producer/consumer diagnostic schema mismatch')
        return result

    def bind(self, request):
        return self.transport._run([self.transport.python, '-c', _BIND], canonical(request).encode())

    def run(self, request, root):
        path = str(Path(self.transport.request_root) / (digest(request) + '.json'))
        self.transport._run([self.transport.python, '-c', _WRITE_SCRIPT, path], canonical(request).encode())
        return self.transport._run([self.transport.python, '-c', _CLI, 'run', '--request', path, '--root', str(root)])


def _artifact(root: Path, ref: dict) -> Path:
    name = ref['path']; relative = PurePosixPath(name)
    if relative.is_absolute() or relative.as_posix() != name or any(p in {'','.', '..'} for p in name.split('/')) or '\\' in name or ':' in name:
        raise StateError('unsafe diagnostic artifact path')
    path = (root / name).resolve()
    if not path.is_relative_to(root) or file_identity(path) != {k: ref[k] for k in ('sha256','bytes')}:
        raise StateError('diagnostic artifact hash/size/root mismatch')
    return path


def execute(journal: ResearchJournal, client: DiagnosticClient, request: dict, *,
            root: str | Path, task_id: str, source_kind: str) -> dict:
    """Explicit public driver boundary: prepare -> reserve -> call/read -> verify -> record.

    Unknown transport outcomes retain reservations. Retries with acknowledged local results
    only read those results; absent result remains pending and never starts a second worker.
    """
    capability = client.capabilities()
    bound = client.bind(request)
    if bound['request'] != request: raise StateError('request fingerprints differ from producer bind')
    validate(request, 'request')
    if (request['capability'] != capability['capability'] or request['operation'] not in capability['operations']
            or not set(request['measurements']) <= set(capability['measurements'])):
        raise StateError('unsupported diagnostic capability/operation/readout; not dispatched')
    if any(ref['role'] in {'sealed_holdout', 'independent_validation'} for ref in request['inputs'].values()):
        raise StateError('held-out reference cannot enter diagnostics')
    for ref in request['inputs'].values(): client.transport.verify(ref)
    root = Path(root).resolve()
    result_path = root / bound['execution_id'] / 'result.json'
    request_hash = digest(bound['semantic'])
    provenance = {'kind': 'prism_diagnostic', 'task_id': task_id, 'producer': client.transport.fingerprint(),
                  'output_root': str(root), 'source_kind': source_kind,
                  'experiment_id': request['experiment_id'], 'program_id': request['program_id'],
                  'action_id': request['action_id']}
    reservation = journal.store.reserve_resource_execution(bound['execution_id'], request_hash,
        request['budget']['wall_seconds'], unit='wall_second', provenance=provenance)
    if reservation['new_reservation']:
        try:
            cli = client.run(request, root)
        except Exception as error:
            journal.store.settle_resource_execution(bound['execution_id'], measured_cost=None,
                receipt={'status':'transport_outcome_unknown','error':str(error)})
            raise ExecutionPending('diagnostic outcome unknown; no automatic redispatch') from error
        if not result_path.is_file():
            journal.store.settle_resource_execution(bound['execution_id'], measured_cost=None, receipt=cli)
            raise ExecutionPending('producer did not leave an acknowledged terminal result')
    else:
        if not result_path.is_file(): raise ExecutionPending('existing diagnostic has no terminal receipt; never redispatch')
        # Producer-owned transitive scientific input checking, without launching a worker.
        client.transport._run([client.transport.python, '-c', _RECHECK], canonical(request).encode())
        cli = {'exit_code': None, 'response': {'replayed': True, 'result': str(result_path)},
               'scope': 'consumer reads acknowledged result only'}
    result = json.loads(result_path.read_text(encoding='utf-8')); validate(result, 'result')
    for key in ('capability','experiment_id','program_id','action_id','idempotency_key','method_id','protocol_instance_id','coupling'):
        if result[key] != request[key]: raise StateError('diagnostic result identity mismatch: ' + key)
    if (result['request_sha256'] != request_hash or result['execution_id'] != bound['execution_id']
            or result['cost']['charge_identity'] != bound['execution_id']):
        raise StateError('diagnostic request/execution/cost identity mismatch')
    if {k:{f:v for f,v in ref.items() if f!='path'} for k,ref in result['input_bindings'].items()} != bound['semantic']['inputs']:
        raise StateError('diagnostic result input binding differs')
    # Real incurred cost is retained even if artifact/geometry admission below fails.
    journal.store.settle_resource_execution(bound['execution_id'], measured_cost=result['cost']['wall_second'],
        receipt={'result_sha256':digest(result),'path':str(result_path),'cost':result['cost'],
                 'status':result['status'],'basis':'producer measured wall_second; CPU separately reported'})
    files = {ref['path']: _artifact(result_path.parent, ref) for ref in result['artifacts']}
    counts = result['counts']; rows = result['outcomes']
    if (counts['attempted'] != len(rows) or counts['succeeded'] != sum(r['status']=='SUCCEEDED' for r in rows)
            or counts['failed'] != len(rows)-counts['succeeded']
            or counts['not_attempted'] != counts['requested']-len(rows)
            or [r['slot'] for r in rows] != list(range(1,len(rows)+1))):
        raise StateError('diagnostic slot denominator mismatch')
    inputs = []
    for ref in request['inputs'].values():
        artifact = Artifact(ref['path'], ref['sha256'], ref['bytes'], source_kind, 'diagnostic', request['input_identity'] or {})
        inputs.append(journal.record(artifact))
    output_source_kind = 'fixture' if source_kind == 'fixture' else 'new_execution'
    result_artifact = Artifact(str(result_path), **file_identity(result_path), source_kind=output_source_kind,
        role='diagnostic', bindings=result['input_identity'] or {}, parents=tuple(inputs))
    journal.record(result_artifact)
    changes = ()
    if request['operation_nature'] == 'intervention':
        changes = (Change('design', ('requested_intervention',), None, request['intervention'], tuple(inputs),
                          'explicit diagnostic request definition; benefit is not asserted'),)
    experiment = Experiment(task_id, request['operation_nature'],
        {**(result['input_identity'] or {}), 'wire_experiment_id':request['experiment_id'],
         'wire_program_id':request['program_id'], 'wire_action_id':request['action_id']}, tuple(inputs),
        request['method_id'], request['protocol_instance_id'],
        ('geometry','identity','failure','core_max_displacement_A','core_rmsd_A','polar_pairs_by_atom_map'),
        Coupling(tuple(inputs), (), ('seed',) if request['coupling']['independent_replicate'] else (),
                 ('producer coupling retained; same-core conformers are not independent physical repeats',)), changes)
    journal.record(experiment)
    observations = []
    for row in rows:
        status = 'measured' if row['status']=='SUCCEEDED' else 'rejected' if row['status']=='REJECTED' else 'failed'
        evidence = [result_artifact.id]
        values, units = {}, {}
        if status == 'measured':
            if row.get('measurement'):
                ref = row['measurement']; path = _artifact(result_path.parent, ref)
                if ref['path'] not in files: raise StateError('measurement not present in complete artifact manifest')
                measured = json.loads(path.read_text())
                if measured.get('schema_version')!='prism-structural-measurement/1' or measured.get('length_unit')!='angstrom':
                    raise StateError('unexpected structural measurement definition/units')
                if measured.get('md_supported') or measured.get('affinity_supported') or measured.get('exact_graph') is not True:
                    raise StateError('unexpected scientific scope or exact graph failure')
                art = Artifact(str(path), ref['sha256'], ref['bytes'], output_source_kind, 'diagnostic', row.get('identity',{}), (result_artifact.id,))
                polar_counts = {}
                for pair in measured['polar_pairs_within_3_5A']:
                    key = str(pair['atom_map']); polar_counts[key] = polar_counts.get(key,0) + 1
                evidence.append(journal.record(art))
                values={'geometry':measured,'core_max_displacement_A':measured['core_max_displacement_A'],
                        'core_rmsd_A':measured['core_rmsd_A'],'polar_pairs_by_atom_map':polar_counts}
                units={'geometry':'structural_proxy; distances in angstrom','core_max_displacement_A':'angstrom',
                       'core_rmsd_A':'angstrom','polar_pairs_by_atom_map':'heavy_atom_pair_count_by_atom_map'}
            else:
                values={'identity':row.get('identity')}; units={'identity':'content_identity'}
        observation = Observation(experiment.id, status, values, units, tuple(evidence),
            '' if status=='measured' else canonical({'slot':row['slot'],'error':row.get('error'),'status':row['status']}),
            measurement_method_id='measurement-' + digest({'schema':'prism-structural-measurement/1',
                'producer_module':result['producer']['modules']['prism.diagnostic.structure']['sha256'],
                'core_tolerance_A':request['parameters']['core_tolerance_A'],
                'readouts':request['measurements']}) if 'geometry' in values else 'prism-input-identity/1')
        observations.append(journal.record(observation))
    if not rows:
        status = 'unsupported' if result['status']=='UNSUPPORTED' else 'failed'
        observations.append(journal.record(Observation(experiment.id,status,{}, {},(result_artifact.id,),canonical(result['error']))))
    receipt = {'schema':'etalon-diagnostic-admission/1','request_sha256':request_hash,'execution_id':bound['execution_id'],
        'experiment_id':experiment.id,'wire_experiment_id':request['experiment_id'],'program_id':request['program_id'],
        'observation_ids':observations,'result_path':str(result_path),'result_sha256':digest(result),
        'producer_status':result['status'],'scientific_judgment':'unresolved','cost':result['cost'],
        'additional_execution_on_this_call':reservation['new_reservation'] and not cli['response'].get('replayed',False),
        'incremental_producer_wall_second_on_this_call':result['cost']['wall_second'] if reservation['new_reservation'] and not cli['response'].get('replayed',False) else 0.,
        'cost_attribution':'original producer execution charged once per campaign; replay incurs no duplicate original fee',
        'scope':'structural diagnostic evidence only; no active learning affinity label',
        'original_coupling':request['coupling'],'cli':cli}
    with journal.store.connection(write=True) as db:
        journal._put(db, 'admission-' + digest([request_hash,digest(result)]),
                     {k:v for k,v in receipt.items() if k not in {'additional_execution_on_this_call','incremental_producer_wall_second_on_this_call','cli'}})
    return receipt
