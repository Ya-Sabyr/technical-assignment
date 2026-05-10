"""Sync orchestrator.

Reads residents from a provider, writes them to a repository, and returns a
SyncResult describing what happened.

The orchestrator is intentionally the only place that owns business logic:
pagination, retries, idempotency, staleness, and counter accounting all
live here. The provider client just simulates network I/O, and the
repository is a dumb dict store.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from functools import partial
from typing import Any

from dexter_sync.retry import with_retry
from dexter_sync.conf import LOGGER_NAME
from dexter_sync.exceptions import (
    MalformedRecordError,
    PermanentProviderError,
    TransientProviderError,
)
from dexter_sync.models import Resident, SyncResult, parse_iso_datetime_utc
from dexter_sync.provider_client import MockCareProvider
from dexter_sync.repository import InMemoryRepository

_base_logger = logging.getLogger(LOGGER_NAME)


def run_sync(
    provider: MockCareProvider,
    repository: InMemoryRepository,
) -> SyncResult:
    """Sync residents from the provider into the repository.

    Behaviour:
      - Paginates via next_cursor until null.
      - Retries transient/429 errors with a tiny capped backoff + jitter.
      - Permanent errors mid-pagination are recorded and stop the run cleanly
        (partial progress is preserved).
      - Detects duplicate-cursor traps (same cursor returned consecutively)
        and aborts pagination.
      - Stale-write protection (strict >): an incoming record with
        last_updated <= the stored updated_at is counted as `skipped`.
      - In-page duplicates by residentId are deduped to the newest version
        before any write; older intra-page copies are silently dropped.
      - Malformed records increment `failed` and append a context-rich
        entry to `result.errors` without aborting the run.

    Every emitted log record carries `run_id` (UUID per call) so log lines
    can be correlated across the run.
    """
    run_id = uuid.uuid4().hex
    result = SyncResult(run_id=run_id)
    logger = logging.LoggerAdapter(_base_logger, {"run_id": run_id})

    seen_cursors: set[str | None] = set()
    cursor: str | None = None

    logger.info(
        "sync.run started provider=%s run_id=%s",
        type(provider).__name__, run_id,
    )

    def _record_retry(attempt: int, exc: TransientProviderError) -> None:
        result.retries_attempted += 1
        logger.warning(
            "sync.fetch_page retry cursor=%r attempt=%d error=%s",
            cursor, attempt, exc,
        )

    while True:
        if cursor in seen_cursors:
            msg = f"duplicate cursor detected: {cursor!r}; aborting pagination"
            result.errors.append(msg)
            logger.warning("sync.pagination duplicate_cursor=%r aborting", cursor)
            break
        seen_cursors.add(cursor)

        try:
            page = with_retry(
                partial(provider.list_residents, cursor=cursor),
                on_retry=_record_retry,
            )
        except PermanentProviderError as e:
            result.errors.append(
                f"permanent provider error at cursor={cursor!r}: {e}"
            )
            logger.error("sync.fetch_page permanent cursor=%r error=%s", cursor, e)
            break
        except TransientProviderError as e:
            result.errors.append(
                f"transient provider error after retries at cursor={cursor!r}: {e}"
            )
            logger.error("sync.fetch_page exhausted cursor=%r error=%s", cursor, e)
            break

        try:
            raw_records, next_cursor = _validate_page_shape(page)
        except ValueError as e:
            result.errors.append(
                f"invalid page shape at cursor={cursor!r}: {e}"
            )
            logger.error(
                "sync.fetch_page invalid_shape cursor=%r error=%s", cursor, e
            )
            break

        logger.info(
            "sync.fetch_page cursor=%r received=%d next_cursor=%r",
            cursor, len(raw_records), next_cursor,
        )

        _process_page(raw_records, repository, result, logger)

        if next_cursor is None:
            break
        cursor = next_cursor

    logger.info(
        "sync.run finished created=%d updated=%d skipped=%d failed=%d "
        "retries=%d errors=%d",
        result.created, result.updated, result.skipped,
        result.failed, result.retries_attempted, len(result.errors),
    )
    return result


def _process_page(
    records: list[dict[str, Any]],
    repository: InMemoryRepository,
    result: SyncResult,
    logger: logging.LoggerAdapter,
) -> None:
    """Process one page: dedup intra-page duplicates, then map and upsert."""
    survivors: dict[str, tuple[datetime, dict[str, Any]]] = {}

    for raw in records:
        if not isinstance(raw, dict):
            result.failed += 1
            result.errors.append(
                f"malformed record [provider_id=<unknown>]: record must be a "
                f"dict, got {type(raw).__name__}: {raw!r}"
            )
            logger.warning("sync.map non_dict_record raw=%r", raw)
            continue

        pid_raw = raw.get("residentId")
        if pid_raw is None or pid_raw == "":
            result.failed += 1
            result.errors.append(
                f"malformed record [provider_id=<missing>]: missing residentId "
                f"raw={raw!r}"
            )
            logger.warning("sync.map missing_residentId raw=%r", raw)
            continue
        if not isinstance(pid_raw, str):
            result.failed += 1
            result.errors.append(
                f"malformed record [provider_id=<invalid>]: residentId must be a "
                f"string, got {type(pid_raw).__name__}: {pid_raw!r} raw={raw!r}"
            )
            logger.warning(
                "sync.map non_string_residentId raw=%r", raw
            )
            continue
        pid = pid_raw.strip()
        if not pid:
            result.failed += 1
            result.errors.append(
                f"malformed record [provider_id=<blank>]: residentId is "
                f"whitespace-only raw={raw!r}"
            )
            logger.warning("sync.map blank_residentId raw=%r", raw)
            continue

        last_updated_raw = raw.get("last_updated")
        try:
            ts = parse_iso_datetime_utc(last_updated_raw, "last_updated")
        except ValueError as e:
            result.failed += 1
            result.errors.append(
                f"malformed record [provider_id={pid}]: {e} raw={raw!r}"
            )
            logger.warning(
                "sync.map invalid_last_updated provider_id=%s raw=%r", pid, raw
            )
            continue

        prev = survivors.get(pid)
        if prev is None or ts > prev[0]:
            survivors[pid] = (ts, raw)
        # else: older intra-page duplicate — silently dropped.

    for ts, raw in survivors.values():
        try:
            resident = Resident.from_provider_payload(raw)
        except MalformedRecordError as e:
            result.failed += 1
            pid = raw.get("residentId", "<missing>")
            result.errors.append(
                f"malformed record [provider_id={pid}]: {e} raw={e.raw!r}"
            )
            logger.warning(
                "sync.map malformed provider_id=%s reason=%s", pid, e
            )
            continue

        existing = repository.get_resident(resident.provider_id)
        if existing is None:
            repository.upsert_resident(resident)
            result.created += 1
            logger.info(
                "sync.upsert provider_id=%s action=created updated_at=%s",
                resident.provider_id, resident.updated_at.isoformat(),
            )
        elif resident.updated_at > existing.updated_at:
            repository.upsert_resident(resident)
            result.updated += 1
            logger.info(
                "sync.upsert provider_id=%s action=updated "
                "old_updated_at=%s new_updated_at=%s",
                resident.provider_id,
                existing.updated_at.isoformat(),
                resident.updated_at.isoformat(),
            )
        else:
            result.skipped += 1
            logger.info(
                "sync.upsert provider_id=%s action=skipped reason=stale "
                "internal=%s incoming=%s",
                resident.provider_id,
                existing.updated_at.isoformat(),
                resident.updated_at.isoformat(),
            )

        _maybe_warn_caregiver_ahead(raw, resident, result)
        _maybe_warn_deleted_at_unparseable(raw, resident, result)


def _validate_page_shape(page: Any) -> tuple[list[Any], str | None]:
    """Validate the provider's pagination envelope.

    Per PROVIDER_API.md the shape is `{"residents": [...], "next_cursor": str|null}`.
    A `None` page, a missing `residents` key, a non-list `residents`, or a
    non-string non-null `next_cursor` are all contract violations — we
    refuse to assume the run is fine in any of these cases.
    """
    if not isinstance(page, dict):
        raise ValueError(
            f"page must be a dict, got {type(page).__name__}"
        )
    if "residents" not in page:
        raise ValueError("page is missing 'residents' key")
    residents = page["residents"]
    if not isinstance(residents, list):
        raise ValueError(
            f"page['residents'] must be a list, got {type(residents).__name__}"
        )
    next_cursor = page.get("next_cursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ValueError(
            f"page['next_cursor'] must be a string or null, got "
            f"{type(next_cursor).__name__}: {next_cursor!r}"
        )
    return residents, next_cursor


def _maybe_warn_deleted_at_unparseable(
    raw: dict[str, Any],
    resident: Resident,
    result: SyncResult,
) -> None:
    """Warn when `deleted_at` is a non-empty string that doesn't ISO-parse.

    The mapper accepts the record (the resident is marked inactive — see
    decision memo) but a malformed timestamp is suspicious enough to be
    visible in ops. Non-string deleted_at types are rejected at the mapper
    boundary, so we only need to handle the unparseable-string case here.
    """
    val = raw.get("deleted_at")
    if not val or not isinstance(val, str) or not val.strip():
        # Absent / blank / whitespace-only — mapper already treated this
        # as not-deleted, so nothing to warn about.
        return
    try:
        parse_iso_datetime_utc(val, "deleted_at")
    except ValueError:
        result.warnings.append(
            f"{resident.provider_id}: unparseable deleted_at {val!r}; "
            f"record treated as inactive"
        )


def _maybe_warn_caregiver_ahead(
    raw: dict[str, Any],
    resident: Resident,
    result: SyncResult,
) -> None:
    """Surface `last_modified_by_caregiver` running ahead of `last_updated`.

    Per PROVIDER_API.md v1.8, this field is informational only — we do NOT
    use it for staleness — but it's worth flagging so an operator can spot
    caregiver-tablet edits that haven't been server-confirmed yet.
    """
    caregiver_raw = raw.get("last_modified_by_caregiver")
    if (
        not caregiver_raw
        or not isinstance(caregiver_raw, str)
        or not caregiver_raw.strip()
    ):
        return
    try:
        caregiver_ts = parse_iso_datetime_utc(
            caregiver_raw, "last_modified_by_caregiver"
        )
    except ValueError:
        result.warnings.append(
            f"{resident.provider_id}: unparseable last_modified_by_caregiver "
            f"{caregiver_raw!r}"
        )
        return
    if caregiver_ts > resident.updated_at:
        result.warnings.append(
            f"{resident.provider_id}: last_modified_by_caregiver "
            f"({caregiver_ts.isoformat()}) is ahead of last_updated "
            f"({resident.updated_at.isoformat()}); informational only"
        )
