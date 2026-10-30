"""Shared CLI/native admission for optional inference, before any storage writes."""
from pathlib import Path
from .managed_downloads import _safe_path
from .media_jobs import validate_media_roots, validate_readonly_boundaries


def validate_similarity_configuration(root, model, provider, automatic, *, writable=(), readonly=(), has_images=False):
    if provider not in ('auto', 'cuda', 'cpu') or type(automatic) is not bool:
        raise ValueError('invalid similarity inference options')
    if root is None:
        if model is not None or automatic or provider != 'auto':
            raise ValueError('similarity inference requires explicit index storage')
        return
    root = Path(root)
    if not root.is_absolute() or not has_images:
        raise ValueError('similarity index requires an explicit saved-image source')
    _safe_path(root)
    protected = list(readonly)
    if model is not None:
        model = Path(model)
        if not model.is_absolute():
            raise ValueError('similarity model requires an absolute path')
        _safe_path(model, file=True)
        if not model.is_file():
            raise ValueError('similarity model is unavailable')
        protected.append(model.parent)
    elif automatic:
        raise ValueError('automatic analysis requires an explicit model')
    validate_media_roots(root, *writable)
    validate_readonly_boundaries((root,), tuple(protected))
