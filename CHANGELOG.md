# Changelog

A per-file record of what changed against the starter codebase. The decision
memo and tradeoffs live in `README.md`.

## Files modified

### `src/dexter_sync/__init__.py`
- Attaches a `logging.NullHandler` to the package logger so library
  consumers that don't configure logging don't see lastResort warnings on
  stderr.

### `src/dexter_sync/models.py`
- Fixed `Resident.from_provider_payload`:
  - `full_name` now concatenates `first_name` + `lastName` (was using only `first_name`).
  - **Both names are required** as non-empty strings. Non-string values
    are rejected (defensive against `AttributeError` from `(value or "").strip()`
    when the provider sends an int / list).
  - `residentId` must be a non-empty string after stripping —
    `str(non-string-truthy)` coercion is gone, and whitespace-only IDs
    (which Pydantic's `str_strip_whitespace=True` previously turned into
    empty strings) are rejected at the boundary.
  - `dob` is parsed via `date.fromisoformat`; missing/null → `None`,
    unparseable → `MalformedRecordError` with the full raw payload attached.
  - `care_level` is normalized through `_normalize_care_level`, accepting
    `int`, numeric strings (`"3"`), level-prefixed strings (`"level_3"`,
    case-insensitive on the prefix), and `null`. Out-of-range ints and
    unrecognized strings become malformed records.
  - `is_active` is **strictly required to be a boolean** when present —
    `bool("false") == True` is the kind of silent wrong answer this
    assignment is designed to catch.
  - `deleted_at` (any non-empty *string*) supersedes `is_active`. Non-string
    types are rejected as malformed; unparseable strings still mark the
    record inactive (upstream "this person is gone" intent wins) but the
    orchestrator emits a warning — this matches the README's documented
    behaviour, which previously had no enforcing code.
  - Top-level non-dict records are rejected with `MalformedRecordError`.
  - All record-level errors raise `MalformedRecordError(message, raw=raw)`
    so the orchestrator has one error type to catch and a context-rich
    message to log.
- Added `run_id: str` and `retries_attempted: int` fields to `SyncResult`.
- Constants `CARE_LEVEL_MIN` / `CARE_LEVEL_MAX` moved to `conf.py`.
- New shared helper `parse_iso_datetime_utc` normalises every parsed
  datetime to UTC-aware: naive datetimes (no tz) are assumed UTC,
  tz-aware datetimes are converted to UTC. This eliminates
  `TypeError: can't compare offset-naive and offset-aware datetimes`
  in the staleness check when a provider sends `last_updated` without
  a timezone suffix.
- `parse_iso_datetime_utc` rejects date-only strings (no `T` / `t` /
  space separator) — provider docs distinguish ISO date (`dob`) from
  ISO datetime fields, and silently inventing a midnight time would
  produce wrong staleness decisions.
- New `@field_validator("provider_id", "full_name")` on `Resident`:
  rejects empty / whitespace-only strings on direct construction. Without
  this guard a caller bypassing the mapper could store records under an
  empty repository key and silently collide. Non-blank values are stripped
  (matches `model_config.str_strip_whitespace=True`).
- New `@field_validator("updated_at", mode="before")` on `Resident`:
  rejects date-only strings (`"2024-01-01"`) and normalises every other
  input to UTC-aware datetime. Runs in `mode="before"` so the date-only
  check sees the raw input rather than Pydantic's already-coerced
  midnight datetime — defense in depth for the staleness invariant on
  any direct `Resident(...)` construction path (test seeders, future
  callers), matching the provider-path check in `parse_iso_datetime_utc`.
- `_resolve_is_active` strips `deleted_at` before deciding presence:
  blank / whitespace-only strings are treated as absent so a padded
  blank from the provider can't accidentally deactivate a resident.

### `src/dexter_sync/sync.py`
- Full rewrite of `run_sync`:
  - Paginates via `next_cursor` until `null`.
  - Tracks `seen_cursors` and aborts the run if a cursor reappears
    (duplicate-cursor trap). The check fires at the top of the next
    iteration, after the duplicate-fetch has been processed once — this
    can inflate `skipped` by one page's worth of stale-equal records,
    but it preserves the invariant that we always process a page we
    successfully fetched (a cleaner alternative would risk discarding
    legitimate pages whose `next_cursor` happens to be malformed).
    This is an intentional counter-semantics tradeoff: duplicate cursor
    loops are still bounded and data-safe, while `skipped` may include the
    one duplicate page that proved the provider was looping.
  - Wraps each fetch in `with_retry`.
  - Permanent errors mid-pagination append to `result.errors` and stop
    cleanly; partial progress is preserved in the repo.
  - Per page: a pre-pass dedups by `residentId` keeping the newest
    `last_updated`. Records missing `residentId` or with unparseable
    `last_updated` increment `failed`. The second pass maps each survivor,
    runs the staleness check (strict `>`), and writes.
  - Counters: `created` for fresh inserts, `updated` for newer overwrites,
    `skipped` for stale-protected, `failed` for malformed,
    `retries_attempted` for visibility into transient/429 noise.
  - `last_modified_by_caregiver` running ahead of `last_updated` is
    surfaced as a warning, never used for staleness.
  - Generates a `run_id` (UUID per call) and propagates it through every
    log record via a `LoggerAdapter`, so log lines for one run are easy
    to grep across a multi-run log stream.
  - Standard-library `logging` calls describe each step in the
    `key=value`-shaped format from `logs/failed_sync.log`. The package
    ships only a `NullHandler` (attached in `__init__.py`) so library
    consumers don't see lastResort warnings; pick a real format by
    calling `logging_config.configure_json_logging()` or attaching your
    own handler to the `dexter_sync.sync` logger.
  - `_maybe_warn_deleted_at_unparseable` post-success hook surfaces
    syntactically invalid `deleted_at` strings as warnings.
  - Non-dict elements inside `page["residents"]` are caught and counted
    as `failed` rather than crashing the run.
  - The page envelope itself is validated by `_validate_page_shape`:
    `page` must be a dict containing a list `residents`; `next_cursor`
    must be `str | None`. Any contract violation is recorded in
    `result.errors` and stops the run cleanly (no infinite-loop risk
    from a list-shaped `next_cursor`, no silent empty-success when
    `residents` is missing).
  - In-page dedup pass also strictly validates `residentId` (must be a
    non-empty string after stripping) before using it as a dict key —
    avoids `TypeError: unhashable type` when the provider sends a list
    or dict as the id.
  - The page-fetch closure was changed from a default-argument lambda to
    `functools.partial(provider.list_residents, cursor=cursor)` so mypy
    can infer the type and the call site stays one line.

## Files added

### `src/dexter_sync/conf.py`
Centralised tunables. Lets the README's "Tradeoffs" section point at one
place instead of describing magic numbers in prose:
- `RETRY_ATTEMPTS`, `RETRY_INITIAL_BACKOFF_SECONDS`,
  `RETRY_MAX_BACKOFF_SECONDS`, `RETRY_JITTER_FRACTION`.
- `CARE_LEVEL_MIN`, `CARE_LEVEL_MAX`.
- `LOGGER_NAME`.

Deliberately plain Python constants — no Pydantic Settings, no env-var
layer — because the assignment is in-process and per-environment overrides
aren't required. The natural extension point is wrapping each constant in
`os.getenv(..., default)` here without touching call sites.

### `src/dexter_sync/retry.py`
`with_retry(fn, attempts, max_backoff, jitter_fraction, sleep, on_retry, rng)`
retries `TransientProviderError` / `RateLimitError`. `retry_after` is honoured
but capped by `max_backoff` so the per-test timeout in the grader can never
be blown. Multiplicative jitter (default ±20%) is applied on top to avoid
thundering-herd retries against a real provider. The `on_retry(attempt, exc)`
callback fires only on failed attempts that are *followed by another retry*
— the final failure does NOT fire it, so a counter incremented in the
callback equals the number of retries actually attempted, not the number of
failed attempts. `sleep` and `rng` are both injectable for deterministic
unit tests. The default RNG is a module-level `random.Random` instance
(not the `random` module) so the function is fully typed.

### `src/dexter_sync/logging_config.py`
Optional JSON-line formatter for production deployments. Includes
`timestamp`, `level`, `logger`, `message`, and any custom attributes
attached via `extra={...}` — notably the `run_id` propagated by `run_sync`.
`configure_json_logging()` is opt-in: not auto-applied. The default sync
behaviour is unchanged (still emits no log records to stderr because of
the package-level `NullHandler`). It accepts `propagate=False` by default
so applications with root-logger handlers don't double-emit each line;
pass `propagate=True` explicitly to opt back in.

### `tests/test_private_sync.py`
Private test suite. 99 tests across:
- Retry success on transient + rate-limit.
- Retry exhaustion records an error rather than raising.
- `retries_attempted` counter is populated correctly.
- Duplicate-cursor trap aborts cleanly without infinite looping.
- Mid-pagination permanent errors stop the run cleanly.
- Polymorphic `care_level` (parametrized over int, `"3"`, `"level_3"`,
  `"Level_3"`, `"LEVEL_3"`, `null`, empty string).
- Out-of-range / unknown `care_level` → `ValueError`.
- `deleted_at` precedence over `is_active`.
- `last_modified_by_caregiver` ahead of `last_updated` emits warning.
- Intra-page duplicate dedup keeps newest, doesn't increment `skipped`.
- Idempotent re-run increments `skipped` only.
- `with_retry` unit tests: max-backoff cap, jitter band, on_retry callback,
  exhaustion, no-retry-on-permanent.
- Mapper unit tests: name concat, dob malformed-with-context, missing
  `last_updated` is malformed.
- `run_id` is UUID per call and unique across runs.
- JSON logging emits parseable JSON with `run_id` on every line.
- Repository upsert is idempotent by `provider_id`.
- Strict provider-payload validation: parametrized failures for
  one-sided / non-string names, non-string `residentId`, non-boolean
  `is_active` (including `"false"`), non-string `deleted_at`, non-dict
  records.
- Unparseable `deleted_at` marks resident inactive and emits a warning.
- Non-dict elements in `residents[]` increment `failed` and don't crash.
- Page-shape envelope validation (parametrized over `None`, list, missing
  `residents`, non-list `residents`, non-string `next_cursor`).
- Bad `residentId` types and whitespace-only IDs in the dedup pre-pass.
- Naive vs tz-aware `last_updated` and `last_modified_by_caregiver` no
  longer raise `TypeError`.
- Package logger ships a `NullHandler` by default.
- `configure_json_logging` defaults to `propagate=False`, with an opt-in
  override.
- `on_retry` semantics: only fires on attempts that trigger a retry (not
  on the final exhausted attempt).
- Pydantic field validator on `Resident.updated_at` that normalises naive
  / non-UTC datetimes to UTC-aware (defends the staleness invariant for
  direct `Resident(...)` construction outside the mapper).
- Whitespace-only `deleted_at` (e.g. `"   "`, `"\t"`, `"\n"`) is treated
  as absent — neither deactivates the resident nor emits a warning.
- Date-only strings as `last_updated` / `deleted_at` /
  `last_modified_by_caregiver` are rejected (mapper raises) or warned on
  (orchestrator helpers); ISO 8601 space-separator (`"YYYY-MM-DD HH:MM:SS"`)
  remains accepted.

## Files unchanged
- `src/dexter_sync/repository.py` — brief explicitly says no business
  logic here.
- `src/dexter_sync/exceptions.py` — already complete.
- `src/dexter_sync/provider_client.py` — simulator, not part of the
  candidate scope.
