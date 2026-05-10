"""Internal data models.

Resident is the internal representation persisted to the repository.
SyncResult summarizes a sync run.

The provider's payload schema is documented in `docs/PROVIDER_API.md` —
read it before extending the mapping below.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dexter_sync.conf import CARE_LEVEL_MAX, CARE_LEVEL_MIN
from dexter_sync.exceptions import MalformedRecordError


def _normalize_care_level(value: Any) -> int | None:
    """Normalize the polymorphic provider `care_level` field to int | None.

    Accepts:
      - None → None (not yet assessed)
      - int in [0, 5] → kept as-is
      - numeric string like "3" → 3
      - level-prefixed string like "level_3" / "Level_3" → 3 (case-insensitive prefix)
      - empty/whitespace string → None

    Anything else (out-of-range int, "high", booleans, lists, etc.) raises
    ValueError so the caller can wrap it in a MalformedRecordError with the
    full raw payload context.
    """
    if value is None:
        return None
    # bool is a subclass of int in Python — guard explicitly so True/False
    # don't silently coerce to 1/0.
    if isinstance(value, bool):
        raise ValueError(f"care_level must not be a boolean, got {value!r}")
    if isinstance(value, int):
        if not CARE_LEVEL_MIN <= value <= CARE_LEVEL_MAX:
            raise ValueError(
                f"care_level={value} out of documented range "
                f"[{CARE_LEVEL_MIN}, {CARE_LEVEL_MAX}]"
            )
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            n = int(s)
        elif s.lower().startswith("level_"):
            tail = s.split("_", 1)[1]
            if not tail.isdigit():
                raise ValueError(f"care_level prefix has non-numeric tail: {value!r}")
            n = int(tail)
        else:
            raise ValueError(f"unrecognized care_level value: {value!r}")
        if not CARE_LEVEL_MIN <= n <= CARE_LEVEL_MAX:
            raise ValueError(
                f"care_level={n} out of documented range "
                f"[{CARE_LEVEL_MIN}, {CARE_LEVEL_MAX}]"
            )
        return n
    raise ValueError(f"care_level has unsupported type {type(value).__name__}: {value!r}")


def _parse_dob(value: Any) -> date | None:
    """Parse the provider's `dob` field. Returns None for null/missing."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"dob must be an ISO date string, got {type(value).__name__}")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"invalid dob {value!r}: {e}") from e


def parse_iso_datetime_utc(value: Any, field_name: str) -> datetime:
    """Parse a required ISO datetime field, normalised to UTC-aware.

    Naive datetimes (no `tzinfo`) are assumed to be UTC — provider docs
    don't mandate a timezone in `last_updated`, so without normalisation
    `datetime.fromisoformat("2024-01-01T00:00:00")` would compare-error
    against a stored tz-aware datetime. UTC is the conservative default
    for server-side timestamps; we document this in the decision memo.
    Aware datetimes are converted to UTC so all comparisons are uniform.

    Date-only strings (e.g. `"2024-01-01"`) are rejected — provider docs
    distinguish ISO date (`dob`) from ISO datetime (`last_updated`,
    `deleted_at`, `last_modified_by_caregiver`). Silently inventing a
    midnight time would create wrong staleness decisions.
    """
    if value is None or value == "":
        raise ValueError(f"missing {field_name}")
    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must be an ISO datetime string, got {type(value).__name__}"
        )
    if not any(sep in value for sep in ("T", "t", " ")):
        raise ValueError(
            f"{field_name} must be an ISO datetime (date+time), got "
            f"date-only string: {value!r}"
        )
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"invalid {field_name} {value!r}: {e}") from e
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _require_non_empty_string(raw: dict[str, Any], field: str) -> str:
    """Return `raw[field]` as a stripped non-empty string, else raise."""
    if field not in raw or raw[field] is None:
        raise ValueError(f"missing {field}")
    value = raw[field]
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a string, got {type(value).__name__}: {value!r}"
        )
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field} must be a non-empty string")
    return stripped


def _build_full_name(raw: dict[str, Any]) -> str:
    """Combine provider `first_name` + `lastName` into the internal full_name.

    Provider docs mark both as required. We enforce both here: a one-sided
    name is treated as malformed so the failure is loud at ingest rather
    than producing partial care records. Non-string values are also
    rejected — defensive against accidental dict/list payloads that would
    otherwise crash the sync with AttributeError.
    """
    first = _require_non_empty_string(raw, "first_name")
    last = _require_non_empty_string(raw, "lastName")
    return f"{first} {last}"


class Resident(BaseModel):
    """Internal resident record."""

    model_config = ConfigDict(frozen=False, str_strip_whitespace=True)

    provider_id: str
    full_name: str
    date_of_birth: date | None = None
    room: str | None = None
    care_level: int | None = None
    updated_at: datetime
    is_active: bool = True

    @field_validator("provider_id", "full_name")
    @classmethod
    def _reject_blank_identifiers(cls, v: str, info: Any) -> str:
        """Reject empty / whitespace-only strings on direct construction.

        The provider-path mapper already enforces non-empty `residentId` and
        `first_name + lastName`, but a direct `Resident(provider_id="")`
        bypasses that — and an empty key in the repository would produce
        silent collisions across distinct empty-id records. This validator
        is the model-level safety net.
        """
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()

    @field_validator("updated_at", mode="before")
    @classmethod
    def _validate_and_normalize_updated_at(cls, v: Any) -> datetime:
        """Defend the staleness invariant on every `Resident` construction.

        Runs in `mode="before"` so we see the raw input rather than
        Pydantic's already-coerced datetime. This lets us reject date-only
        strings like `"2024-01-01"` (which Pydantic would otherwise
        silently coerce to midnight) and guarantees the persisted
        `updated_at` is always UTC-aware — naive datetimes are assumed UTC
        and aware datetimes are converted to UTC.

        Provider-path callers route through `parse_iso_datetime_utc`
        first, which catches malformed strings with the full raw payload
        attached; this validator is the safety net for direct
        `Resident(...)` construction (test fixtures, future call sites).
        """
        if isinstance(v, str):
            if not any(sep in v for sep in ("T", "t", " ")):
                raise ValueError(
                    f"updated_at must be an ISO datetime (date+time), got "
                    f"date-only string: {v!r}"
                )
            try:
                v = datetime.fromisoformat(v)
            except ValueError as e:
                raise ValueError(f"invalid updated_at {v!r}: {e}") from e
        if not isinstance(v, datetime):
            raise ValueError(
                f"updated_at must be a datetime, got {type(v).__name__}"
            )
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @classmethod
    def from_provider_payload(cls, raw: dict[str, Any]) -> "Resident":
        """Map a raw provider payload into the internal Resident model.

        Any record-level validation error raises MalformedRecordError carrying
        the full raw payload, so the orchestrator can log enough context to
        debug without crashing the run.
        """
        if not isinstance(raw, dict):
            raise MalformedRecordError(
                f"record must be a dict, got {type(raw).__name__}", raw={}
            )

        try:
            provider_id = _require_non_empty_string(raw, "residentId")
            full_name = _build_full_name(raw)
            date_of_birth = _parse_dob(raw.get("dob"))
            care_level = _normalize_care_level(raw.get("care_level"))
            updated_at = parse_iso_datetime_utc(raw.get("last_updated"), "last_updated")
            effective_is_active = _resolve_is_active(raw)
        except ValueError as e:
            raise MalformedRecordError(str(e), raw=raw) from e

        try:
            return cls(
                provider_id=provider_id,
                full_name=full_name,
                date_of_birth=date_of_birth,
                room=raw.get("room"),
                care_level=care_level,
                updated_at=updated_at,
                is_active=effective_is_active,
            )
        except ValidationError as e:
            raise MalformedRecordError(f"validation error: {e}", raw=raw) from e


def _resolve_is_active(raw: dict[str, Any]) -> bool:
    """Compute effective is_active honouring deleted_at precedence.

    Per PROVIDER_API.md (v2.0 changelog), `deleted_at` supersedes
    `is_active`. We strictly require:
      - `is_active` is a bool when present (the docs document it as a
        boolean; coercing strings like "false" to True via `bool(...)` is
        the kind of silent wrong answer this assignment exists to catch).
      - `deleted_at`, if present, is either a string (we let the
        orchestrator emit a warning if it doesn't parse, since upstream
        intent "this person is gone" matters more than a clean timestamp)
        or an empty/null marker. Other types (int, list, dict) are
        rejected as malformed.
    """
    if "is_active" in raw and raw["is_active"] is not None:
        is_active_value = raw["is_active"]
        if not isinstance(is_active_value, bool):
            raise ValueError(
                f"is_active must be a boolean, got {type(is_active_value).__name__}: "
                f"{is_active_value!r}"
            )
    else:
        is_active_value = True

    deleted_at_raw = raw.get("deleted_at")
    if deleted_at_raw is None:
        deleted_marker_present = False
    elif isinstance(deleted_at_raw, str):
        # Parsing happens at orchestrator-warning-time; presence of a
        # non-blank string here is enough to mark the resident deleted.
        # Whitespace-only strings (e.g. "   ") are treated as absent so a
        # padded blank from the provider doesn't accidentally deactivate
        # a resident.
        deleted_marker_present = bool(deleted_at_raw.strip())
    else:
        raise ValueError(
            f"deleted_at must be an ISO datetime string, got "
            f"{type(deleted_at_raw).__name__}: {deleted_at_raw!r}"
        )

    return (not deleted_marker_present) and is_active_value


class SyncResult(BaseModel):
    """Outcome of a sync run."""

    run_id: str = ""
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    retries_attempted: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.created + self.updated + self.skipped + self.failed
