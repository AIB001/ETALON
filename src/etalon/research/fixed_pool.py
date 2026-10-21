"""Consumer semantics for a paid Vina prefix and two Vinardo subset queries.

Scientific assay losses are supplied separately by the coordinator. These query
outcomes use scores only and never infer an experimental ranking from a scorer.
"""
from itertools import combinations

from etalon.active.schema import finite
from .diagnostics import Probe, Resource
from .transfer import Catalog


def two_chunk_catalog(*, initial_quote: float, first_quote: float, second_quote: float,
                      loss_definition: str) -> Catalog:
    return Catalog(
        probes=(Probe('q1', 'measure', {'scorer': 'vinardo', 'subset': 'first_three', 'reference': 'r0'},
                      ('same', 'changed', 'failed'), ('r1',)),
                Probe('q2', 'measure', {'scorer': 'vinardo', 'subset': 'last_three', 'reference': 'r0'},
                      ('same', 'changed', 'failed'), ('r2',))),
        resources=(Resource('r0', initial_quote, 'wall_second'), Resource('r1', first_quote, 'wall_second'),
                   Resource('r2', second_quote, 'wall_second')),
        decision_resources={'fixed_pool_vina': ('r0',), 'fixed_pool_vinardo': ('r0','r1','r2'), 'abstain': ()},
        loss_definition=loss_definition, cost_unit='wall_second', abstention='abstain',
        initial_decision='fixed_pool_vina', initial_resources=('r0',))


def subset_rank_outcome(*, candidate_ids: tuple[str, ...], vina: dict[str, float | None],
                        vinardo: dict[str, float | None], tolerance: float = 1e-6) -> dict:
    """A pair-order signature includes ties; any missing subset score is failed.

    Only the paid subset is accepted, so another chunk cannot leak into the
    diagnostic. Both score functions minimize, within their own units/scales.
    """
    finite(tolerance, 'fixed score tie tolerance', minimum=0)
    if len(candidate_ids) < 2 or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError('at least two distinct frozen candidate identities required')
    if set(vina) != set(candidate_ids) or set(vinardo) != set(candidate_ids):
        raise ValueError('only the exact paid subset scores may enter a query outcome')
    for scores in (vina, vinardo):
        for value in scores.values():
            if value is not None: finite(value, 'measured score')
    missing = sorted(k for k in candidate_ids if vina[k] is None or vinardo[k] is None)
    if missing:
        return {'outcome':'failed','missing_candidates':missing,'pairs':[],
                'candidate_ids':candidate_ids,'tolerance':tolerance}
    def sign(value): return 0 if abs(value) <= tolerance else 1 if value > 0 else -1
    pairs = [{'candidates':[a,b], 'vina_order':sign(vina[a]-vina[b]),
              'vinardo_order':sign(vinardo[a]-vinardo[b])} for a,b in combinations(candidate_ids,2)]
    return {'outcome':'changed' if any(p['vina_order'] != p['vinardo_order'] for p in pairs) else 'same',
            'candidate_ids':candidate_ids,'pairs':pairs,'missing_candidates':[], 'tolerance':tolerance,
            'scope':'within-subset score ordering only; no affinity labels or other chunk visibility'}
