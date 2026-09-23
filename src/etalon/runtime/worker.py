"""Internal detached worker entry point; only a recorded owner may claim a job."""

import argparse

from etalon.runtime.service import run_worker

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace")
    parser.add_argument("job_id")
    parser.add_argument("epoch", type=int)
    args = parser.parse_args()
    raise SystemExit(run_worker(args.workspace, args.job_id, args.epoch))
