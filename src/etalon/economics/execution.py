"""Compact execution observations and cost arithmetic; the host chooses changes."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _seconds(end, start):
    try:
        return max(0., (datetime.fromisoformat(end.replace('Z', '+00:00')) -
                        datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds())
    except (AttributeError, TypeError, ValueError):
        return None


def execution_feedback(progress, *, run_id, stage_id, method_id, input_id, observed_at=None):
    """Read one producer snapshot without polling, writing, proposing, or routing.

    Rates concern completed *new* map rows in this attempt. Merge/publication and
    discarded prior work are not silently included in a remaining-map estimate.
    A worker count is a pool limit, not CPU occupancy or proof of inefficiency.
    """
    now = observed_at or datetime.now(timezone.utc).isoformat()
    phase = progress.get('phase')
    requested = _number(progress.get('requested_workers'))
    effective = _number(progress.get('effective_workers'))
    completed = _number(progress.get('rows_completed'))
    reused = _number(progress.get('rows_reused'))
    total = _number(progress.get('rows_total'))
    elapsed = _seconds(progress.get('updated_at'), progress.get('map_started_at'))
    fresh_rows = completed - reused if completed is not None and reused is not None else None
    rate = (fresh_rows / elapsed if phase in {'map', 'map_complete'}
            and fresh_rows is not None and fresh_rows > 0 and elapsed else None)
    remaining = max(0., total - completed) / rate if phase == 'map' and rate and total is not None else None
    signals = []
    cpu_pool = _number(progress.get('cpu_postprocess_workers'))
    if phase == 'map' and not cpu_pool and requested and effective is not None and 0 < effective < requested:
        shards = _number(progress.get('shards_total'))
        done = _number(progress.get('shards_complete'))
        if shards is not None and done is not None and shards - done <= effective:
            signals.append('parallelism_limited_by_remaining_shards')
        elif (progress.get('execution_lanes') and
              all(str(lane).startswith('cuda:') for lane in progress['execution_lanes']) and
              effective == len(progress['execution_lanes'])):
            signals.append('accelerator_parallelism_bounded_by_devices')
        else:
            signals.append('requested_parallelism_not_realized')
    if not progress:
        signals.append('execution_telemetry_unavailable')
    worker_phases = progress.get('worker_phases', {})
    if (phase == 'map' and any(worker_phases.get(p, 0) for p in ('pose_relax', 'pose_check'))
            and not worker_phases.get('gpu_search_and_decode', 0)):
        signals.append('cpu_pose_work_without_active_gpu_batch')
    active_chunks = _number(progress.get('cpu_active_chunks'))
    queued_chunks = _number(progress.get('cpu_queued_chunks'))
    producer_active = _number(progress.get('producer_active_batches'))
    if (phase == 'map' and cpu_pool and active_chunks is not None
            and 0 < active_chunks < cpu_pool and queued_chunks == 0 and producer_active == 0):
        signals.append('cpu_postprocess_tail')
    quality_rows = _number(progress.get('quality_rows_completed'))
    quality_reused = _number(progress.get('quality_rows_reused'))
    quality_rate = ((quality_rows - quality_reused) / elapsed
                    if phase in {'map', 'map_complete'} and elapsed
                    and quality_rows is not None and quality_reused is not None else None)
    basis = dict(run_id=run_id, stage_id=stage_id, method_id=method_id, input_id=input_id,
                 phase=phase, requested_workers=requested, effective_workers=effective,
                 signals=signals)
    key = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
    # These are prompts for host judgment, not a routing decision or a policy.
    opportunities = []
    if signals:
        opportunities.append({'level': 'execution', 'signals': list(signals),
                              'choices': ['inspect', 'adjust', 'retain']})
    if progress.get('shard_counts') and progress['shard_counts'].get('total') is None:
        opportunities.append({'level': 'observability', 'signals': ['shard_total_unknown'],
                              'choices': ['inspect', 'retain']})
    return {'basis': basis, 'observation_key': key, 'signals': signals,
            'rows_completed': completed, 'rows_total': total, 'rows_reused': reused,
            'worker_phases': worker_phases,
            'shard_counts': progress.get('shard_counts'),
            'improvement_opportunities': opportunities,
            'cpu_postprocess_workers': cpu_pool,
            'quality_rows_per_second': quality_rate,
            'quality_chunks_completed': progress.get('quality_chunks_completed'),
            'quality_chunks_reused': progress.get('quality_chunks_reused'),
            'producer_batches_completed': progress.get('producer_batches_completed'),
            'producer_batches_reused': progress.get('producer_batches_reused'),
            'resource_review': {
                'required': bool(total is not None and total >= 100000 or signals),
                'policy': 'advisory', 'decision_authority': 'host',
                'when': 'first representative completion and material phase/throughput change',
                'key': key,
                'action': 'inspect measured throughput and reuse once; retain or compare a bounded change; do not gate computation'},
            'new_rows_per_second': rate, 'estimated_remaining_map_seconds': remaining,
            'sample_age_seconds': _seconds(now, progress.get('updated_at')),
            'observed_at': now, 'producer_updated_at': progress.get('updated_at'),
            'interpretation': 'host hypothesis; counts are not utilization; ETA excludes merge and later stages'}


def compare_execution_costs(*, baseline_seconds, candidate_seconds, development_and_trials_seconds,
                            rollout_seconds, future_uses=1, baseline_remaining_seconds=None,
                            candidate_remaining_seconds=None, remaining_development_seconds=None,
                            abandoned_work_seconds=None, model_tokens=None):
    """Compare equal-quality executions and switch timing, without deciding adoption.

    Development/trials covers all elapsed one-time work (including failed trials,
    model work and evaluation); do not sum overlapping parallel wall intervals.
    Switching compares costs still ahead. Already spent work is recorded separately,
    not charged a second time. The host supplies the restart/reuse-dependent remainder.
    Missing measurements stay unknown. Quality/comparability is a separate judgment.
    """
    if isinstance(future_uses, bool) or not isinstance(future_uses, int) or future_uses < 1:
        raise ValueError('future_uses must be a positive integer')
    values = dict(baseline_seconds=baseline_seconds, candidate_seconds=candidate_seconds,
                  development_and_trials_seconds=development_and_trials_seconds,
                  rollout_seconds=rollout_seconds, baseline_remaining_seconds=baseline_remaining_seconds,
                  candidate_remaining_seconds=candidate_remaining_seconds,
                  remaining_development_seconds=remaining_development_seconds,
                  abandoned_work_seconds=abandoned_work_seconds)
    for name, value in values.items():
        if value is not None and _number(value) is None:
            raise ValueError(f'{name} must be finite nonnegative seconds or None')
    known = all(values[k] is not None for k in (
        'baseline_seconds', 'candidate_seconds', 'development_and_trials_seconds', 'rollout_seconds'))
    baseline_total = baseline_seconds * future_uses if baseline_seconds is not None else None
    candidate_total = (development_and_trials_seconds + rollout_seconds + candidate_seconds * future_uses
                       if known else None)
    saving = baseline_seconds - candidate_seconds if baseline_seconds is not None and candidate_seconds is not None else None
    break_even = ((math.floor((development_and_trials_seconds + rollout_seconds) / saving) + 1)
                  if known and saving > 0 else None)
    switch_total = (remaining_development_seconds + rollout_seconds + candidate_remaining_seconds
                    if all(v is not None for v in (remaining_development_seconds, rollout_seconds,
                                                   candidate_remaining_seconds)) else None)
    return {'costs': values, 'future_uses': future_uses, 'baseline_total_seconds': baseline_total,
            'candidate_total_seconds': candidate_total,
            'projected_saving_seconds': baseline_total - candidate_total if known else None,
            'strict_break_even_uses': break_even,
            'switch_from_now_seconds': switch_total,
            'switch_saving_seconds': baseline_remaining_seconds - switch_total
                if baseline_remaining_seconds is not None and switch_total is not None else None,
            'model_tokens': model_tokens, 'quality': 'host must check equivalent required outputs',
            'decision': 'host_selects; positive timing alone does not authorize adoption'}
