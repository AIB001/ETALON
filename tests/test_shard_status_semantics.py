"""A partial directory inventory is never the declared size of a run."""
from molcascade.parallel.shards import shard_progress_counts
from molcascade.parallel.progress import write_progress


def test_missing_plan_is_unknown_even_with_completed_and_active_directories(tmp_path):
    for index in range(24):
        (tmp_path/f'shard-{index:05d}.done').write_text('{}')
    for index in range(30):
        (tmp_path/f'shard-{index:05d}').mkdir()
    result = shard_progress_counts(tmp_path)
    assert result == {'complete': 24, 'started_or_materialized': 30, 'total': None}
    write_progress(tmp_path, shards_total=100)
    assert shard_progress_counts(tmp_path)['total'] == 100
    write_progress(tmp_path, shards_total=24)
    assert shard_progress_counts(tmp_path)['total'] is None


def test_cli_displays_unknown_instead_of_a_partial_total(tmp_path, capsys):
    from datetime import datetime, timezone
    from molcascade import cli
    from molcascade.runtime.cache import checkpoint_directory_name
    from molcascade.runtime.models import StageRunState, StageRunStatus, RunState, RunStatus
    key = 'cache:sha256:' + 'b'*64
    checkpoint = tmp_path/checkpoint_directory_name(key)
    checkpoint.mkdir()
    (checkpoint/'shard-00000.done').write_text('{}')
    (checkpoint/'shard-00000').mkdir()
    (checkpoint/'shard-00001').mkdir()
    stage = StageRunState(stage_id='dock', plugin_key='docking.unidock@0.2.0',
                          status=StageRunStatus.RUNNING, cache_key=key)
    now = datetime.now(timezone.utc)
    state = RunState(run_id='job', revision_id='c'*64, status=RunStatus.RUNNING,
                     stages=(stage,), started_at=now, updated_at=now)
    cli._print_run_state(state, checkpoints_root=tmp_path)
    printed = capsys.readouterr().out
    assert 'shards_complete=1 shards_total=unknown shards_started_or_materialized=2' in printed
