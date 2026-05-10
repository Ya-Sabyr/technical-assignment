"""In-memory repository.

Stands in for a Firestore-like persistence layer. Pure dict store, no business
logic — keep idempotency / staleness checks in the sync orchestrator.
"""
from __future__ import annotations

from dexter_sync.models import Resident


class InMemoryRepository:
    def __init__(self) -> None:
        self._store: dict[str, Resident] = {}

    def get_resident(self, provider_id: str) -> Resident | None:
        return self._store.get(provider_id)

    def upsert_resident(self, resident: Resident) -> None:
        """Insert or replace by provider_id."""
        self._store[resident.provider_id] = resident

    def list_residents(self) -> list[Resident]:
        return list(self._store.values())

    def count(self) -> int:
        return len(self._store)
