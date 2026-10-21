"""Public finite R4 consumer: fit, assess, act and independently released replay.

Input rows must be complete real measured panels or explicitly synthetic fixtures.
This CLI never submits producer work or discovers labels from the filesystem.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

from etalon.active.schema import canonical
from etalon.active import CampaignStore
from etalon.research import ResearchJournal
from etalon.research.rows import verify_dataset, verify_visible_receipts
from etalon.boundary.prism_v1 import file_identity
from etalon.research.diagnostics import Probe, Resource
from etalon.research.transfer import (Catalog, GroupSplit, evaluate_policies, fit_policy,
    load_policy, next_action, probability_assessment, save_policy, calibrate_policy, record_policy)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fit = sub.add_parser('fit')
    fit.add_argument('--training', type=Path, required=True)
    fit.add_argument('--config', type=Path, required=True,
                     help='frozen split/catalog/learner parameters, no evaluation labels')
    fit.add_argument('--output', type=Path, required=True)
    fit.add_argument('--database',type=Path)
    for name in ('calibrate', 'assess', 'evaluate', 'act'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--policy', type=Path, required=True)
        cmd.add_argument('--output', type=Path, required=True)
        cmd.add_argument('--database',type=Path)
        if name == 'act':
            cmd.add_argument('--history', type=Path, required=True)
            cmd.add_argument('--initial-receipt', type=Path)
            cmd.add_argument('--mode', default='phi', choices=('phi0','phi','copy_best','static','myopic','nonmyopic'))
        else: cmd.add_argument('--rows', type=Path, required=True)
        if name == 'calibrate': cmd.add_argument('--selection-report',type=Path,required=True)
        if name in {'evaluate','assess'}:
            cmd.add_argument('--role', default='calibration', choices=('calibration', 'test'))
            cmd.add_argument('--release', type=Path)
    args = parser.parse_args()
    # Refuse output reuse before reading any potentially independent rows.
    if args.output.exists(): raise FileExistsError(args.output)
    if args.command == 'calibrate' and args.selection_report.exists(): raise FileExistsError(args.selection_report)
    wall, cpu = time.perf_counter(), time.process_time()
    if args.database and not args.database.is_file():raise FileNotFoundError('existing research ledger required')
    journal=ResearchJournal(CampaignStore(args.database)) if args.database else None
    def verify_real(rows,role,split_sha):
        if any(row['source_kind']=='measured' for row in rows):
            if journal is None:raise ValueError('real measured rows require an admitted journal dataset')
            verify_dataset(journal,rows,role=role,split_sha256=split_sha)
    if args.command == 'fit':
        config = read(args.config); c = config['catalog']
        catalog = Catalog(tuple(Probe(**p) for p in c['probes']),tuple(Resource(**r) for r in c['resources']),
            {k:tuple(v) for k,v in c['decision_resources'].items()},c['loss_definition'],c['cost_unit'],
            c['abstention'],c['initial_decision'],tuple(c.get('initial_resources',())))
        rows=read(args.training);split=GroupSplit(**config['split']);verify_real(rows,'train',split.sha256)
        result = fit_policy(rows,split=split,catalog=catalog,
            calibration_selection=config.get('calibration_selection'),
            workflow_bindings=config.get('workflow_bindings'),support_guard=config.get('support_guard'),
            evaluation_cost_weight=config.get('evaluation_cost_weight'),**config['learner'])
        if journal:record_policy(journal,result)
        save_policy(result,args.output)
    else:
        policy = load_policy(args.policy)
        if policy['source_kind']=='measured':
            if journal is None:raise ValueError('measured policy requires its original research journal')
            if journal.get('learnedpolicy-'+policy['policy_sha256'])!=policy:raise ValueError('policy differs from journal')
        if args.command == 'act':
            history=read(args.history);initial=read(args.initial_receipt) if args.initial_receipt else None
            if journal:verify_visible_receipts(journal,policy,history,initial)
            result = next_action(policy,history,mode=args.mode,initial_receipt=initial)
        elif args.command == 'calibrate':
            rows=read(args.rows);verify_real(rows,'calibration',policy['split_sha256'])
            result, selection = calibrate_policy(policy,rows)
            if journal:record_policy(journal,result)
            with args.selection_report.open('x',encoding='utf-8') as stream: stream.write(canonical(selection)+'\n')
        else:
            release = read(args.release) if args.release else None
            # Gate before opening test rows, not merely after parsing them.
            if args.role == 'test' and (not release or release.get('policy_sha256') != policy['policy_sha256']
                    or release.get('split_sha256') != policy['split_sha256'] or not release.get('evidence')):
                raise ValueError('independent release required before opening test rows')
            function = probability_assessment if args.command == 'assess' else evaluate_policies
            rows=read(args.rows);verify_real(rows,args.role,policy['split_sha256'])
            result = function(policy,rows,role=args.role,release=release)
        with args.output.open('x',encoding='utf-8') as stream: stream.write(canonical(result)+'\n')
    print(json.dumps({'command':args.command,'output':str(args.output.absolute()),**file_identity(args.output),
        'python':sys.executable,'script':file_identity(__file__),
        'wall_second':time.perf_counter()-wall,'cpu_second':time.process_time()-cpu,
        'scope':'consumer processing only; no scientific execution or budget charge'}))


if __name__ == '__main__': main()
