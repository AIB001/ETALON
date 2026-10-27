"""Analyze original QOV trajectories and continue through one project entry.

CONFIG supplies result, source_config, entry and output absolute paths. This is
trajectory reanalysis, not new MD or independent biological validation.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
from etalon.runtime.files import file_identity, _save
from etalon.runtime.reuse import execute_or_reuse, request_identity
from etalon.research.notes import entry_value, read_entry, save_progress, original
from etalon.research import ResearchJournal
from etalon.active import CampaignStore
from etalon.rescore.contacts import geometry, diagnose


def run(config, *, interrupt_after_result=False):
    result_path, source_config = Path(config['result']), Path(config['source_config'])
    old = json.loads(result_path.read_text())
    prior = json.loads(source_config.read_text())
    entry, root = Path(config['entry']), Path(config['output'])
    root.mkdir(parents=True, exist_ok=True)
    value = entry_value(entry)
    journal = ResearchJournal(CampaignStore(value['database']))
    pair = next((Path(prior['case_root'])/'pairs').glob('*/pair.json'))
    p = json.loads(pair.read_text())
    native = Path(prior['case_root'])/'primary'/p['run_id']/'input'
    files = [result_path, source_config, pair, pair.parent/'exact-input.sdf', pair.parent/'frame.gro',
             native/'ligand.itp', native/'topol.top']
    successful = [row for row in old['rows'] if row['status']=='succeeded']
    for row in successful:
        folder = Path(row['handle']).parent
        files += [folder/'em.gro',Path(row['handle'])/'md.xtc',folder/'CONTACTS.json']
    implementation = [Path(__file__), Path(__file__).parents[1]/'src/etalon/rescore/contacts.py']
    implementation += [Path(__file__).parent/name for name in (
        'snd1_feedback_experiment.py','snd1_anchor_intervention.py','snd1_contact_program.py',
        'snd1_feedback_search.py','snd1_paired_relaxation.py','snd1_task_learning.py')]
    request = request_identity(operation='snd1.contact-loss-diagnosis',
        implementation={str(path):file_identity(path) for path in implementation},
        environment={'python':sys.executable,'numpy':np.__version__},
        inputs={str(path):file_identity(path) for path in files},
        parameters={'distance_A':3.,'angle_deg':150.,'late_start_ps':50.,'paired_timings':7})
    result_file = root/'RESULT.json'
    branch='contact-loss-diagnosis'
    base=dict(task_id=value['task_id'],branch_id=branch,
        question='Does QOV anchor loss primarily reflect separation or angular misalignment?',
        conditions={'target':'SND1','molecule':'QOV','endpoint':'conditioned-local-anchor-retention'},
        capability_id='contact-loss-diagnosis',counterevidence=[],
        invalid_when='Different molecule, assignment, trajectory protocol or endpoint',
        other_pauses={'R4b':'user-paused'})
    request_path=root/'execution'/request['request_digest']/'request.json'
    receipt_path=request_path.with_name('receipt.json')
    if not receipt_path.exists():
        save_progress(journal,entry,{**base,'summary':'Diagnose saved trajectories; original dual-contact failure remains.',
            'status':'running','evidence':[original(result_path)],
            'pending_handles':[{'request':str(request_path),'output':str(result_file),
                'resume':'repeat this command; saved output is recovered before any execution'}],
            'next_action':{'operation':'recover_contact_diagnosis','config':config}},
            publication_id=request['request_digest']+':started')
    def produce():
        import mdtraj as md
        import snd1_feedback_experiment as experiment
        import snd1_paired_relaxation as reference
        start, cpu=time.perf_counter(),time.process_time()
        ctx,*_=experiment.context(prior)
        triples,labels=ctx['case']['triples'],ctx['case']['triple_residues']
        rows=[]; timings=[]
        for row in successful:
            folder=Path(row['handle']).parent
            trajectory=md.load(str(Path(row['handle'])/'md.xtc'),top=str(folder/'em.gro'))
            xyz,boxes=trajectory.xyz.astype(float)*10,trajectory.unitcell_vectors.astype(float)*10
            saved=json.loads((folder/'CONTACTS.json').read_text())
            assert len(saved)==len(trajectory)
            measured={name:[] for name in ('frame_loop','batch')}
            for repeat in range(7):
                # Alternate order under the same input/frame budget.
                order=('frame_loop','batch') if repeat%2==0 else ('batch','frame_loop')
                for method in order:
                    t=time.perf_counter()
                    if method=='frame_loop':
                        loop=reference.contact_series(xyz,boxes,triples,labels)
                    else:
                        ds,angles=geometry(xyz,boxes,triples)
                    measured[method].append(time.perf_counter()-t)
            for label in ('ASN281','ARG255','SER254'):
                indices=np.asarray(labels)==label
                batch=((ds[:,indices]<=3.)&(angles[:,indices]>=150.)).any(axis=1)
                assert batch.tolist()==[c[label]['primary'] for c in loop]
                assert batch.tolist()==[c[label]['primary'] for c in saved]
            rows.append({'mode':row['mode'],'seed':row['seed'],
                'diagnosis':diagnose(ds,angles,labels,trajectory.time),
                'original_endpoint_unchanged':True})
            timings.append({'mode':row['mode'],'seed':row['seed'], 'frames':len(trajectory),
                'seconds':measured,'median_seconds':{k:float(np.median(v)) for k,v in measured.items()}})
        answer=dict(rows=rows,timings=timings,scope='Same saved trajectories, correlated frames; geometry diagnosis not force causality',
            eligible_runs=len(successful),unavailable_runs=len(old['rows'])-len(successful),
            new_MD=0,originals=request['inputs'],request_digest=request['request_digest'],
            cost={'wall_seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu},
            full_host_token_cost=None)
        _save(result_file,answer)
        if interrupt_after_result:
            raise SystemExit(75)
        return {'result':answer,'artifacts':[result_file]}
    def recover():
        answer=json.loads(result_file.read_text())
        if answer['request_digest']!=request['request_digest']:
            raise ValueError('Saved analysis belongs to another request')
        return {'result':answer,'artifacts':[result_file]}
    execution=execute_or_reuse(root/'execution',request,produce,recover=recover)
    if execution['receipt']['status']!='complete':
        raise RuntimeError(execution['receipt']['error'])
    answer=execution['receipt']['result']
    concise=[{'mode':r['mode'],'seed':r['seed'],
        'anchors':{k:{'first_loss':v['first_loss_kind'],'first_loss_ps':v['first_loss_ps'],'late':v['late']}
                   for k,v in r['diagnosis'].items() if k in {'ASN281','ARG255'}}} for r in answer['rows']]
    prior_note=read_entry(entry,branch_id=config['prior_branch'])['note'] if config.get('prior_branch') else {}
    note={**base,'summary':'Saved-trajectory contact losses separated into angular and distance failures; original dynamic result retained.',
        'status':'resolved','outcome':'retain','knowledge':concise,
        'lesson':'Use residue-specific loss geometry to choose the next intervention; aggregate occupancy alone does not identify it.',
        'evidence':[original(result_file),original(Path(execution['receipt_path'])),*[original(path) for path in files]],
        'counterevidence':prior_note.get('counterevidence',[]),
        'pending_handles':[], 'residuals':['The geometric categories do not identify causal force or long-time affinity.'],
        'next_action':{'operation':'choose_intervention_from_loss_geometry','automatic_recompute':False},
        'method_choices':{'readout':'etalon.rescore.contacts.geometry','scope':'same triples and endpoint; see measured timing in details'},
        'methods':{'request':request}}
    rid=save_progress(journal,entry,note,publication_id=request['request_digest']+':result')
    return {'entry':str(entry),'record_id':rid,'reused':execution['reused'],
        'receipt':execution['receipt_path'],'cost':answer['cost'],'knowledge':concise,'timings':answer['timings']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    parser.add_argument('--interrupt-after-result',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(json.loads(args.config.read_text()),interrupt_after_result=args.interrupt_after_result)))
