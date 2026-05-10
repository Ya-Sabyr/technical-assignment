"""Public tests for the sync orchestrator.

These define the expected behaviour the candidate must implement.
Several of these will FAIL against the starter — that is by design.
"""
from __future__ import annotations

import json

from dexter_sync.sync import run_sync


def test_happy_path_minimal_sync(single_page_provider, repository):
    """A clean single-page sync persists every record by provider_id."""
    run_sync(single_page_provider, repository)
    assert repository.count() == 5
    assert repository.get_resident("RES-1001") is not None
    assert repository.get_resident("RES-1005") is not None


def test_pagination_across_two_pages(provider, repository):
    """All pages must be processed.

    page_1 has 5 distinct residents (RES-1001, RES-1002, RES-1003, RES-9000,
    RES-1005). page_2 has 4 records: RES-1001 (duplicate of page_1 with newer
    updated_at), RES-1004, RES-1006, RES-1007. Distinct after dedup by
    provider_id: 8.
    """
    run_sync(provider, repository)
    ids = {r.provider_id for r in repository.list_residents()}
    assert ids == {
        "RES-1001",
        "RES-1002",
        "RES-1003",
        "RES-1004",
        "RES-1005",
        "RES-1006",
        "RES-1007",
        "RES-9000",
    }


def test_duplicate_resident_handling_uses_newest(provider, repository):
    """When the same provider_id appears twice across pages with different
    updated_at, the newest version must win.

    RES-1001 in page_1 has room='101' (older); in page_2 room='101A' (newer).
    """
    run_sync(provider, repository)
    res = repository.get_resident("RES-1001")
    assert res is not None
    assert res.room == "101A"


def test_malformed_record_does_not_crash_full_sync(errors_provider, repository):
    """A bad record must not abort the run. Valid records should still persist.

    Fixture has 5 records: 3 valid (RES-1100, RES-1102, RES-1104) and 2
    unambiguously invalid (RES-1101 has invalid dob; one record has no
    residentId).
    """
    result = run_sync(errors_provider, repository)
    assert repository.get_resident("RES-1100") is not None
    assert repository.get_resident("RES-1102") is not None
    assert repository.get_resident("RES-1104") is not None
    assert result.failed >= 2
    assert len(result.errors) >= 2


def test_stale_provider_payload_does_not_overwrite_newer_internal(
    provider, seeded_repository
):
    """Seed contains RES-9000 with updated_at=2024-06-01.
    Provider returns RES-9000 with updated_at=2024-01-15 (older).
    The internal record must remain unchanged.
    """
    run_sync(provider, seeded_repository)
    res = seeded_repository.get_resident("RES-9000")
    assert res is not None
    assert res.room == "900-CURRENT"


def test_judgment_fixture_is_loadable(data_dir):
    """Smoke check: the ambiguity fixture exists and parses.

    `data/provider_page_judgment.json` exercises the polymorphic `care_level`,
    `deleted_at`, and `last_modified_by_caregiver` fields documented in
    `docs/PROVIDER_API.md`. If you have not opened that file yet, do so —
    your decision memo is graded on it.
    """
    with open(data_dir / "provider_page_judgment.json", encoding="utf-8") as f:
        payload = json.load(f)
    assert "residents" in payload
    assert len(payload["residents"]) == 5


def test_sync_result_counts_are_meaningful(single_page_provider, repository):
    """SyncResult counters must reflect what actually happened.

    5 fresh creates, no updates, no skips, no failures.
    """
    result = run_sync(single_page_provider, repository)
    assert result.created == 5
    assert result.updated == 0
    assert result.skipped == 0
    assert result.failed == 0
