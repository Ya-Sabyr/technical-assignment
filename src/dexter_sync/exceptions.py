"""Error hierarchy for provider sync.

TransientProviderError and RateLimitError are retriable.
PermanentProviderError is not retriable.
MalformedRecordError indicates a record-level problem; the rest of the sync
should continue.
"""


class ProviderError(Exception):
    """Base class for any provider-side error."""


class TransientProviderError(ProviderError):
    """Transient/server-side problem (e.g. 5xx). Retriable."""


class RateLimitError(TransientProviderError):
    """Rate limit hit (HTTP 429). Retriable, may want backoff."""

    def __init__(self, message: str = "rate limited", retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentProviderError(ProviderError):
    """Permanent problem (e.g. 4xx). Not retriable."""


class MalformedRecordError(Exception):
    """A single record failed validation. Should not abort the whole sync."""

    def __init__(self, message: str, *, raw: dict | None = None) -> None:
        super().__init__(message)
        self.raw = raw or {}
