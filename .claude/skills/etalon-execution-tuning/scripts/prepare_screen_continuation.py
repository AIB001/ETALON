"""Prepare compatible screen handoffs through the ETALON interface."""
from __future__ import annotations
import argparse
import json
from etalon.screening_handoff import prepare_screen_continuation


def prepare(workspace, completed_run, next_config, output_dir, *, docking_backend=None, through_stage=None):
    return prepare_screen_continuation(workspace, completed_run, next_config, output_dir,
                                       docking_backend=docking_backend, through_stage=through_stage)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['workspace', 'completed-run', 'next-config', 'output-dir']:
        p.add_argument('--' + name, required=True)
    p.add_argument('--docking-backend', default=None, help='Optional explicit Uni-Dock plugin version')
    p.add_argument('--through-stage', help='Reuse an explicit committed preparation prefix')
    args = p.parse_args()
    print(json.dumps(prepare(args.workspace, args.completed_run, args.next_config, args.output_dir,
                             docking_backend=args.docking_backend, through_stage=args.through_stage), indent=2))
