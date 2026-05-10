import logging as _logging

from dexter_sync.exceptions import (
    MalformedRecordError,
    PermanentProviderError,
    ProviderError,
    RateLimitError,
    TransientProviderError,
)
from dexter_sync.models import Resident, SyncResult
from dexter_sync.provider_client import MockCareProvider
from dexter_sync.repository import InMemoryRepository
from dexter_sync.sync import run_sync

# Library default: a NullHandler on the package logger so callers who
# don't configure logging don't see "lastResort" warnings printed to
# stderr. None of the imported modules emit log records during import,
# so attaching this after the imports is safe.
_logging.getLogger("dexter_sync").addHandler(_logging.NullHandler())

__all__ = [
    "InMemoryRepository",
    "MalformedRecordError",
    "MockCareProvider",
    "PermanentProviderError",
    "ProviderError",
    "RateLimitError",
    "Resident",
    "SyncResult",
    "TransientProviderError",
    "run_sync",
]
