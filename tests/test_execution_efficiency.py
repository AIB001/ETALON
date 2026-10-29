"""Execution facts guide optional RSI; they neither schedule work nor adopt methods."""
import pytest

from etalon.economics.execution import execution_feedback, compare_execution_costs


def sample(**updates):
    return dict(phase='map', requested_workers=8, effective_workers=1,
                rows_completed=95, rows_reused=90, rows_total=100,
                shards_complete=2, shards_total=20,
                map_started_at='2026-10-28T00:00:00+00:00',
                updated_at='2026-10-28T00:00:05+00:00', **updates)


def feedback(progress, **updates):
    return execution_feedback(progress, run_id='run', stage_id='stage', method_id='method/1',
                              input_id='input/1', **updates)


def test_reused_rows_not_credited_as_new_throughput_and_age_is_visible():
    report = feedback(sample(), observed_at='2026-10-28T00:00:15+00:00')
    assert report['new_rows_per_second'] == 1
    assert report['estimated_remaining_map_seconds'] == 5
    assert report['sample_age_seconds'] == 10
    assert report['signals'] == ['requested_parallelism_not_realized']
    again = feedback(sample(), observed_at='2026-10-28T00:01:00+00:00')
    assert again['observation_key'] == report['observation_key']


def test_merge_small_workload_and_missing_telemetry_are_distinct():
    merge = feedback({**sample(), 'phase': 'global_merge'})
    assert merge['signals'] == [] and merge['estimated_remaining_map_seconds'] is None
    assert merge['new_rows_per_second'] is None
    short = feedback({**sample(), 'shards_total': 3})
    assert short['signals'] == ['parallelism_limited_by_remaining_shards']
    missing = feedback({})
    assert missing['signals'] == ['execution_telemetry_unavailable']
    assert missing['new_rows_per_second'] is None
    unknown_reuse = feedback({**sample(), 'rows_reused': None})
    assert unknown_reuse['estimated_remaining_map_seconds'] is None


def test_condition_identity_tracks_inputs_not_timestamp_or_progress_reads():
    first = feedback(sample())
    later = feedback({**sample(), 'rows_completed': 96})
    assert first['observation_key'] == later['observation_key']
    changed = execution_feedback(sample(), run_id='run', stage_id='stage', method_id='method/2', input_id='input/1')
    assert changed['observation_key'] != first['observation_key']


def test_mixed_pipeline_reports_tail_not_false_gpu_worker_deficit():
    report = feedback({**sample(), 'rows_total':1000000, 'cpu_postprocess_workers':8,
        'cpu_active_chunks':2, 'cpu_queued_chunks':0, 'producer_active_batches':0,
        'quality_rows_completed':100, 'quality_rows_reused':90})
    assert report['signals'] == ['cpu_postprocess_tail']
    assert report['quality_rows_per_second'] == 2
    assert report['resource_review']['required'] is True
    assert report['resource_review']['key'] == report['observation_key']


def test_gpu_lane_count_is_not_a_cpu_worker_deficit():
    report = feedback({**sample(), 'requested_workers': 84, 'effective_workers': 2,
                       'execution_lanes': ['cuda:0', 'cuda:1']})
    assert report['signals'] == ['accelerator_parallelism_bounded_by_devices']
    assert report['improvement_opportunities'][0]['choices'] == ['inspect', 'adjust', 'retain']
    assert report['resource_review']['decision_authority'] == 'host'
    assert report['resource_review']['policy'] == 'advisory'


def test_faster_candidate_can_lose_now_but_amortize_later():
    costs = dict(baseline_seconds=30., candidate_seconds=10., development_and_trials_seconds=200.,
                 rollout_seconds=5., baseline_remaining_seconds=15., candidate_remaining_seconds=30.,
                 remaining_development_seconds=20., abandoned_work_seconds=300.)
    once = compare_execution_costs(**costs)
    assert once['projected_saving_seconds'] == -185
    assert once['switch_from_now_seconds'] == 55  # sunk 300 s is not charged twice
    assert once['switch_saving_seconds'] == -40
    assert once['strict_break_even_uses'] == 11
    many = compare_execution_costs(**costs, future_uses=100)
    assert many['projected_saving_seconds'] == 1795
    assert many['model_tokens'] is None
    assert many['decision'].startswith('host_selects')


def test_unknown_complete_cost_is_not_zero_or_an_adoption():
    result = compare_execution_costs(baseline_seconds=30, candidate_seconds=10,
                                    development_and_trials_seconds=None, rollout_seconds=0)
    assert result['projected_saving_seconds'] is None
    assert result['strict_break_even_uses'] is None
    assert result['switch_saving_seconds'] is None


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf'), True])
def test_invalid_measurements_are_not_costs(bad):
    with pytest.raises(ValueError):
        compare_execution_costs(baseline_seconds=bad, candidate_seconds=10,
                                development_and_trials_seconds=5, rollout_seconds=0)
