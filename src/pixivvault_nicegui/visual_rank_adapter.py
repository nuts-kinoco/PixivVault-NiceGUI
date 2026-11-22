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

    model: visual.ModelSnapshot
    current: visual.Generation | None
    embeddings: Mapping[str, visual.Embedding]
    protected_work_ids: frozenset[str] = frozenset()
    alpha: float = visual.DEFAULT_ALPHA  # Provisional, with no empirical basis.
    gpu_ok: bool = True

    def __post_init__(self):
        if type(self.alpha) not in (int, float) or not 0 <= self.alpha <= 1:
            raise ValueError('alpha must be between zero and one')
        object.__setattr__(self, 'embeddings', MappingProxyType(dict(self.embeddings)))
        object.__setattr__(self, 'protected_work_ids', frozenset(self.protected_work_ids))


def rerank_sure_tail(sure, baseline_order, snapshot, *, sort_key, finalize):
    """Permute eligible sure slots, then check the existing finalizer's output."""
    if (not baseline_order or not snapshot.embeddings or not snapshot.gpu_ok
            or snapshot.alpha == 0 or snapshot.model.generation != snapshot.current):
        return baseline_order

    head = str(baseline_order[0].get('work_id') or '')
    scored = []
    for index, item in enumerate(sure):
        candidate, info = item
        work = str(candidate.get('work_id') or '')
        if work == head or work in snapshot.protected_work_ids:
            continue
        embedding = snapshot.embeddings.get(work)
        if embedding is None or embedding.key.work_id != work:
            continue
        base = info['score']
        adjusted = visual.score(base, snapshot.model, embedding, current=snapshot.current,
                                alpha=snapshot.alpha)
        # An unchanged score can also come from a valid neutral or clipped signal.
        if adjusted == base and snapshot.model.image_signal(embedding) is None:
            continue
        scored.append((index, item, adjusted))

    sorted_rows = sorted(scored, key=lambda row: sort_key(row[1], visual_score=row[2]))
    slots = [index for index, _, _ in scored]
    if slots == [index for index, _, _ in sorted_rows]:
        return baseline_order

    adjusted_sure = list(sure)
    for index, (_, item, _) in zip(slots, sorted_rows):
        adjusted_sure[index] = item

    proposed = finalize(adjusted_sure)
    movable = {id(item[0]) for _, item, _ in scored}
    # Author constraints can move cards across frames. Commit a permutation
    # only when the same slots in the finalized order still hold eligible cards.
    if any(before is not after and (id(before) not in movable or id(after) not in movable)
           for before, after in zip(baseline_order, proposed)):
        return baseline_order
    return proposed
