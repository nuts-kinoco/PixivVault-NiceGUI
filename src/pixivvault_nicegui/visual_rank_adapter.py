"""Pure, opt-in visual ordering for the discovery sure frame."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from . import visual_preference as visual


@dataclass(frozen=True, slots=True)
class VisualPreferenceSnapshot:
    """Already-prepared embeddings and the generation current at ranking time.

    The caller supplies None for OFF or unavailable evidence, and supplies IDs
    of previously presented or in-use cards to pin in the pending order.
    """

    model: visual.ModelSnapshot | None
    current: visual.Generation | None
    embeddings: Mapping[str, visual.Embedding]
    protected_work_ids: frozenset[str | int] = frozenset()
    alpha: float = visual.DEFAULT_ALPHA  # Provisional, with no empirical basis.
    gpu_ok: bool = True

    def __post_init__(self):
        if type(self.alpha) not in (int, float) or not 0 <= self.alpha <= 1:
            raise ValueError('alpha must be between zero and one')
        object.__setattr__(self, 'embeddings', MappingProxyType(dict(self.embeddings)))
        object.__setattr__(self, 'protected_work_ids', frozenset(map(str, self.protected_work_ids)))


def rerank_sure_tail(sure, baseline_order, snapshot, *, sort_key, finalize, is_deferred=None):
    """Permute eligible sure slots, then check the existing finalizer's output."""
    model = snapshot.model
    if (not baseline_order or model is None or not snapshot.embeddings or not snapshot.gpu_ok
            or snapshot.alpha == 0 or model.generation != snapshot.current):
        return baseline_order

    head = str(baseline_order[0].get('work_id') or '')
    pools = {False: [], True: []}
    for index, item in enumerate(sure):
        candidate, info = item
        work = str(candidate.get('work_id') or '')
        if work == head or work in snapshot.protected_work_ids:
            continue
        embedding = snapshot.embeddings.get(work)
        if embedding is None or embedding.key.work_id != work:
            continue
        base = info['score']
        adjusted = visual.score(base, model, embedding, current=snapshot.current,
                                alpha=snapshot.alpha, gpu_ok=snapshot.gpu_ok)
        # An unchanged score can also come from a valid neutral or clipped signal.
        if adjusted == base and model.image_signal(embedding) is None:
            continue
        deferred = is_deferred is not None and is_deferred(candidate.get('author_id'))
        pools[deferred].append((index, sort_key(item), sort_key(item, visual_score=adjusted)))

    # Exchange only slots that go to the same side of the rejection split.
    adjusted_sure = list(sure)
    for pool in pools.values():
        changed_priorities = {corrected[:2] for _, original, corrected in pool
                              if original[:2] != corrected[:2]}
        if not changed_priorities:
            continue
        # Only ties involving a changed score use the work-ID-only tie break.
        ordered = sorted(pool, key=lambda row: row[2] if row[2][:2] in changed_priorities else row[1])
        for (index, _, _), (source, _, _) in zip(pool, ordered):
            adjusted_sure[index] = sure[source]

    if all(before is after for before, after in zip(sure, adjusted_sure)):
        return baseline_order

    proposed = finalize(adjusted_sure)
    movable = {id(sure[index][0]) for pool in pools.values() for index, _, _ in pool}
    # Author constraints can move cards across frames. If any fixed slot, even
    # beyond the display limit, moves, discard the whole proposal. The guard
    # cannot assume a subset of swaps would also keep quota and cooldown valid.
    if any(before is not after and (id(before) not in movable or id(after) not in movable)
           for before, after in zip(baseline_order, proposed)):
        return baseline_order
    return proposed
