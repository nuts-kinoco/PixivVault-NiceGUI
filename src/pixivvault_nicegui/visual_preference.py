"""Pure visual preference fitting and scoring for an immutable generation."""
from collections import defaultdict
from dataclasses import asdict, dataclass
from math import fsum, hypot, isfinite
from numbers import Real
import re


# Initial shadow-evaluation settings. These values have no empirical basis.
DEFAULT_ALPHA = 0.05
_WORK_CONFIDENCE_PRIOR = 4
_AUTHOR_CONFIDENCE_PRIOR = 4
_VARIANT_PRIORITY = {'saved_original': 0, 'view': 1, 'thumb': 2, 'thumb_fallback': 3}
VARIANTS = frozenset(_VARIANT_PRIORITY)
# G0 found 0/12 Top-10 order matches between originals and 192 px thumbnails.
_SCORING_VARIANTS = frozenset({'saved_original', 'view'})
_EMBEDDING_SPACE = 'original-view-v1'
_SHA256 = re.compile(r'[0-9a-f]{64}')


@dataclass(frozen=True, slots=True)
class Generation:
    store_id: str
    number: int
    model_sha: str
    preprocessing_version: str
    judgment_fingerprint: str


@dataclass(frozen=True, slots=True)
class EmbeddingKey:
    work_id: str
    page: int
    variant: str
    image_sha: str
    model_sha: str
    preprocessing_version: str

    def __post_init__(self):
        if (type(self.work_id) is not str or not self.work_id
                or type(self.page) is not int or self.page < 0
                or self.variant not in VARIANTS
                or not _SHA256.fullmatch(self.image_sha)
                or not _SHA256.fullmatch(self.model_sha)
                or type(self.preprocessing_version) is not str or not self.preprocessing_version):
            raise ValueError('invalid embedding identity')

    def matches(self, generation: Generation) -> bool:
        return (self.model_sha, self.preprocessing_version) == (
            generation.model_sha, generation.preprocessing_version)


@dataclass(frozen=True, slots=True)
class Embedding:
    key: EmbeddingKey
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TrainingSample:
    embedding: Embedding
    author_id: str
    judgment: str = ''
    reason: str = ''
    saved: bool = False
    bookmarked: bool = False
    revision: int = 0


@dataclass(frozen=True, slots=True)
class Signal:
    value: float
    confidence: float


@dataclass(frozen=True, slots=True)
class AuthorStyle:
    author_id: str
    vector: tuple[float, ...]
    work_count: int
    concentration: float


def _unit(values, dimension=None):
    try:
        values = tuple(values)
        if not values or (dimension is not None and len(values) != dimension):
            return None
        if any(isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value)
               for value in values):
            return None
        scale = max(map(abs, values))
        if scale == 0:
            return None
        scaled = tuple(float(value / scale) for value in values)
        length = hypot(*scaled)
        return tuple(value / length for value in scaled)
    except (TypeError, ValueError, OverflowError):
        return None


def _mean(vectors, weights):
    total = sum(weights)
    return tuple(fsum(v * w for v, w in zip(column, weights)) / total
                 for column in zip(*vectors))


def _affinity(left, right):
    return max(0.0, min(1.0, fsum(a * b for a, b in zip(left, right))))


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    generation: Generation
    method: str
    dimension: int
    positive: tuple[tuple[tuple[float, ...], float], ...]
    negative: tuple[tuple[tuple[float, ...], float], ...]
    positive_count: int
    negative_count: int
    authors: tuple[AuthorStyle, ...]
    embedding_space: str = _EMBEDDING_SPACE

    def _signal(self, vector, reliability=1.0):
        if self.method == 'nearest':
            positive = max(_affinity(vector, row) * weight for row, weight in self.positive)
            negative = max((_affinity(vector, row) * weight for row, weight in self.negative),
                           default=0.0)
        else:
            row, weight = self.positive[0]
            positive = _affinity(vector, row) * weight
            negative = 0.0
            if self.negative:
                row, weight = self.negative[0]
                negative = _affinity(vector, row) * weight
        count = self.positive_count + self.negative_count
        confidence = reliability * count / (count + _WORK_CONFIDENCE_PRIOR)
        return Signal(0.5 + 0.5 * (positive - negative), confidence)

    def image_signal(self, embedding: Embedding | None) -> Signal | None:
        if (embedding is None or embedding.key.variant not in _SCORING_VARIANTS
                or not embedding.key.matches(self.generation)):
            return None
        vector = _unit(embedding.vector, self.dimension)
        return self._signal(vector) if vector is not None else None

    def author_signal(self, author_id: str) -> Signal | None:
        for author in self.authors:
            if author.author_id == author_id:
                reliability = author.concentration * author.work_count / (
                    author.work_count + _AUTHOR_CONFIDENCE_PRIOR)
                return self._signal(author.vector, reliability)
        return None

    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, generation: Generation, payload: dict) -> 'ModelSnapshot | None':
        """Validate the serialized lineage and dimensions before using stored scores."""
        try:
            dimension = payload['dimension']
            if (payload['generation'] != asdict(generation)
                    or payload.get('embedding_space') != _EMBEDDING_SPACE
                    or payload['method'] not in ('nearest', 'centroid')
                    or type(dimension) is not int or dimension <= 0):
                return None
            banks = []
            for label in ('positive', 'negative'):
                bank = tuple((tuple(vector), weight) for vector, weight in payload[label])
                count = payload[label + '_count']
                expected = count if payload['method'] == 'nearest' else min(1, count)
                if (type(count) is not int or count < 0 or len(bank) != expected
                        or any(not _valid_unit(v, dimension) or not _probability(w) for v, w in bank)):
                    return None
                banks.append(bank)
            if not banks[0]:
                return None
            authors = tuple(AuthorStyle(row['author_id'], tuple(row['vector']),
                                        row['work_count'], row['concentration'])
                            for row in payload['authors'])
            if (any(type(row.author_id) is not str or not row.author_id
                    or type(row.work_count) is not int or row.work_count < 1
                    or not _valid_unit(row.vector, dimension) or not _probability(row.concentration)
                    for row in authors) or len({row.author_id for row in authors}) != len(authors)):
                return None
            return cls(generation, payload['method'], dimension, *banks,
                       payload['positive_count'], payload['negative_count'], authors)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            return None


def _probability(value):
    return type(value) in (int, float) and 0 <= value <= 1


def _valid_unit(vector, dimension):
    return _unit(vector, dimension) is not None and abs(hypot(*vector) - 1) < 1e-6


def _weight(rows):
    judgments = {(row.judgment, row.reason if row.judgment == 'hate' else '')
                 for row in rows if row.judgment and row.judgment != 'opened'}
    if len(judgments) > 1:
        return 0.0  # Conflicting judgments at the same revision are ambiguous.
    judgment, reason = next(iter(judgments), ('', ''))
    if (judgment, reason) == ('hate', 'style'):
        return -1.0
    explicit = {'superlike': 1.0, 'like': 0.8}.get(judgment)
    if explicit is not None:
        return explicit
    if judgment in ('skip', 'hate'):
        return 0.0
    return 0.35 if any(row.saved or row.bookmarked for row in rows) else 0.0


def fit(generation: Generation, samples, *, method='nearest') -> ModelSnapshot | None:
    """Fit one weighted centroid per sign or compare the nearest labelled works.

    The representative image is deterministic per work. No clusters are fitted
    before measured data establishes stable groups. All weights are provisional.
    """
    if method not in ('nearest', 'centroid'):
        raise ValueError('unknown visual preference method')
    by_work = defaultdict(list)
    for row in samples:
        by_work[row.embedding.key.work_id].append(row)

    positive, negative = [], []
    author_vectors = defaultdict(list)
    dimension = None
    for _, rows in sorted(by_work.items()):
        latest = max(row.revision for row in rows)
        rows = [row for row in rows if row.revision == latest]
        weight = _weight(rows)
        choices = []
        for row in rows:
            key = row.embedding.key
            if key.variant not in _SCORING_VARIANTS or not key.matches(generation):
                continue
            vector = _unit(row.embedding.vector)
            if vector is not None:
                choices.append((_VARIANT_PRIORITY[key.variant], key.page, key.image_sha,
                                vector, row.author_id))
        if not choices:
            continue
        _, _, _, vector, _ = min(choices)
        if weight or any(row.saved for row in rows):
            if dimension is None:
                dimension = len(vector)
            elif dimension != len(vector):
                return None
        if weight > 0:
            positive.append((vector, weight))
        elif weight < 0:
            negative.append((vector, -weight))
        if any(row.saved for row in rows):
            originals = [choice for choice in choices if choice[0] == 0]
            if originals:
                _, _, _, original, author = min(originals)
                if author:
                    author_vectors[author].append(original)

    if not positive:
        return None
    authors = []
    for author, vectors in sorted(author_vectors.items()):
        mean = _mean(vectors, [1.0] * len(vectors))
        vector = _unit(mean, dimension)
        if vector is not None:
            authors.append(AuthorStyle(author, vector, len(vectors), min(1.0, hypot(*mean))))

    counts = len(positive), len(negative)
    if method == 'centroid':
        positive = _centroid(positive)
        negative = _centroid(negative)
        if not positive:
            return None
        if not negative:
            # A cancelling negative centroid provides no direction.
            counts = counts[0], 0
    return ModelSnapshot(generation, method, dimension, tuple(positive), tuple(negative),
                         *counts, tuple(authors))


def _centroid(rows):
    if not rows:
        return ()
    vectors, weights = zip(*rows)
    mean = _mean(vectors, weights)
    vector = _unit(mean)
    if vector is None:
        return ()
    return ((vector, min(1.0, hypot(*mean)) * sum(weights) / len(rows)),)


def blend(base_score: float, signal: Signal | None, *, alpha: float) -> float:
    if signal is None or alpha == 0 or signal.confidence == 0 or signal.value == 0.5:
        return base_score
    if not _probability(alpha):
        raise ValueError('alpha must be between zero and one')
    return max(0.0, min(1.0, base_score + alpha * signal.confidence * (signal.value - 0.5)))


def score(base_score: float, snapshot: ModelSnapshot | None, candidate: Embedding | None, *,
          current: Generation | None, alpha: float = DEFAULT_ALPHA, gpu_ok: bool = True) -> float:
    """Score an image-ready candidate using a generation read at application time."""
    if (snapshot is None or candidate is None or not gpu_ok or alpha == 0
            or snapshot.generation != current):
        return base_score
    return blend(base_score, snapshot.image_signal(candidate), alpha=alpha)
