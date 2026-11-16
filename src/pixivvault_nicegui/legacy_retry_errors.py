"""Typed local retry boundaries; exception messages must not contain source data."""


class LegacyRetrySourceChanged(ValueError):
    """The entire admitted frozen source is no longer trustworthy."""


class LegacyRetryRowUnavailable(ValueError):
    """A single immutable selection no longer has a supported remote artwork."""


class DownloadQueueCapacityReached(ValueError):
    """Nothing was accepted: wait for capacity without repeating the GET."""


class LegacyRetryCancelled(RuntimeError):
    """The originating UI generation or application is no longer current."""
