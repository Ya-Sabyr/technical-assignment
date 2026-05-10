"""Mock provider client.

Reads deterministic JSON fixtures and simulates provider behaviour:
- pagination (next_cursor)
- duplicates within a page
- duplicate next_cursor (loop trap)
- rate limits (429)
- transient 5xx
- permanent 4xx

This is the only place that simulates 'the network'. Candidates do not need
to mock HTTP themselves.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dexter_sync.exceptions import (
    PermanentProviderError,
    RateLimitError,
    TransientProviderError,
)


@dataclass
class FailurePlan:
    """Configures synthetic failures the client raises before returning data.

    transient_failures_per_cursor: cursor -> remaining number of TransientProviderError
        to raise before the request succeeds (decremented each call).
    rate_limit_failures_per_cursor: cursor -> remaining number of RateLimitError
        to raise before success.
    permanent_failure_cursors: cursors that always raise PermanentProviderError.
    """

    transient_failures_per_cursor: dict[str | None, int] = field(default_factory=dict)
    rate_limit_failures_per_cursor: dict[str | None, int] = field(default_factory=dict)
    permanent_failure_cursors: set[str | None] = field(default_factory=set)


class MockCareProvider:
    """Fictional third-party care provider."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        page_files: list[str] | None = None,
        failure_plan: FailurePlan | None = None,
        duplicate_cursor_trap: bool = False,
    ) -> None:
        """
        data_dir: directory containing page_*.json fixtures.
        page_files: ordered list of fixture filenames; first cursor is None,
            then each page's next_cursor points at the next file.
        failure_plan: optional synthetic-failure plan (mutated as failures fire).
        duplicate_cursor_trap: if True, every page returns its own cursor as
            next_cursor. Tests that the candidate guards against infinite loops.
        """
        self.data_dir = Path(data_dir)
        self.page_files = page_files or ["provider_page_1.json", "provider_page_2.json"]
        self.failure_plan = failure_plan or FailurePlan()
        self.duplicate_cursor_trap = duplicate_cursor_trap
        self.call_count = 0

    def list_residents(self, cursor: str | None = None) -> dict[str, Any]:
        """Return one page of residents.

        Returns:
            {
                "residents": [ ... raw provider records ... ],
                "next_cursor": str | None,
            }
        """
        self.call_count += 1

        # Simulate failures BEFORE returning data, so retry semantics are tested.
        if cursor in self.failure_plan.permanent_failure_cursors:
            raise PermanentProviderError(f"permanent error at cursor={cursor!r}")

        remaining_429 = self.failure_plan.rate_limit_failures_per_cursor.get(cursor, 0)
        if remaining_429 > 0:
            self.failure_plan.rate_limit_failures_per_cursor[cursor] = remaining_429 - 1
            raise RateLimitError("rate limited", retry_after=0.0)

        remaining_5xx = self.failure_plan.transient_failures_per_cursor.get(cursor, 0)
        if remaining_5xx > 0:
            self.failure_plan.transient_failures_per_cursor[cursor] = remaining_5xx - 1
            raise TransientProviderError(f"transient error at cursor={cursor!r}")

        # Decide which fixture to load.
        page_index = self._cursor_to_index(cursor)
        if page_index >= len(self.page_files):
            return {"residents": [], "next_cursor": None}

        with open(self.data_dir / self.page_files[page_index], encoding="utf-8") as f:
            payload = json.load(f)

        residents = payload.get("residents", [])

        if self.duplicate_cursor_trap:
            # Every page advertises its OWN cursor as the next one — infinite loop bait.
            return {"residents": residents, "next_cursor": cursor or "page_1"}

        next_cursor: str | None
        if page_index + 1 < len(self.page_files):
            next_cursor = f"page_{page_index + 2}"
        else:
            next_cursor = None
        return {"residents": residents, "next_cursor": next_cursor}

    @staticmethod
    def _cursor_to_index(cursor: str | None) -> int:
        if cursor is None:
            return 0
        # Cursors are "page_2", "page_3", ...
        try:
            return int(cursor.split("_")[1]) - 1
        except (IndexError, ValueError) as e:
            raise PermanentProviderError(f"unknown cursor: {cursor!r}") from e
