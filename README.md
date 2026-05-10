# Backend Assignment: Provider Sync Reliability Challenge

## Context

At Dexter Health, our backend connects with third-party healthcare software
providers and synchronizes operational care data into our internal systems.

In this assignment, you will work with a small existing Python codebase that
simulates a resident data sync from a third-party provider into a Firestore-like
repository.

The current implementation is a thin happy-path skeleton. Your job is to make
it production-grade enough to be trusted with real care data.

## Setup

Python 3.11 or newer required. On macOS and many Linux distros the system
`python3` is older than 3.11 and the editable install will fail with a
confusing setuptools error — use `python3.11` explicitly to be safe.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
pytest
```

No cloud accounts, Docker, Postgres, Redis, or external services. Everything
runs locally and deterministically.

After setup, `pytest` should report **6 passed, 5 failed**. That is your
starting line — the failing public tests are part of the spec.

## Your Task

Make the running sync trustworthy. The failing public tests and
`docs/PROVIDER_API.md` together describe the gap between what the starter
does and what we need.

In broad strokes:

1. **Reliability** — pagination, retries, rate-limit handling, no infinite
   loops, no silent error swallowing.
2. **Data integrity** — idempotent re-runs, no duplicate residents, no stale
   data overwriting newer internal records, partial failures must not corrupt
   valid data.
3. **Validation & mapping** — read the provider docs carefully. Some fields
   are messier than the starter assumes. Surface validation errors with
   enough context to debug. Map provider `first_name` + `lastName` into the
   internal `full_name` field.
4. **Tests** — add at least 3 meaningful tests beyond the public suite.
   Cover edge cases that aren't already exercised.
5. **Notes & decision memo** — write up your approach, tradeoffs, and the
   design decisions you made when the docs were ambiguous (see
   "Decision memo" at the bottom of this file).

## Assumptions you can make

These corners are intentionally underspecified — exactly like real provider
integrations. Pick a defensible answer and document it. You will not be
punished for a reasonable choice.

- **Stale-write protection:** treating `existing.updated_at >= incoming.updated_at`
  as "skip" is acceptable. Strict `>` is also acceptable. Be consistent.
- **Counter semantics:** `created` for fresh inserts, `updated` for
  newer-overwrites, `skipped` for stale-protected, `failed` for malformed or
  validation errors. A stale-skip increments `skipped`, not `updated=0`.
- **Retry budget:** ≥ 3 attempts on transient / 429 errors is expected.
  Exponential backoff is a plus but not required.
- **Retry timing:** the test simulator uses `retry_after = 0` so retries do
  not wait. If your retry helper layers its own backoff on top, keep the
  per-attempt sleep small (≤ ~1s) — the test grader has a per-test timeout
  and a backoff that ignores `retry_after` may flake.
- **Permanent error mid-pagination:** record it in `result.errors` and stop
  the run cleanly. Do not retry. Do not silently continue.
- **Sync entrypoint signature:** keep
  `run_sync(provider, repository) -> SyncResult` so the grading harness can
  call your code unchanged.

## Timebox

Please block a focused **4-hour window** for the assignment. The timer starts
when you open the PandaDoc briefing we sent you.

It is fine — and expected — that you make tradeoffs to fit the timebox. If
setup issues or life interruptions materially affect your available time, just
note that in your submission. Tell us what you chose to prioritise and what
you would improve with more time.

## AI Tools

You are encouraged to use Claude Code, Codex, Cursor, Copilot, ChatGPT, or
similar tools. Use whatever you would use in a real job.

We evaluate the **final outcome**: reliability, data integrity, tests,
judgment, and your ability to explain the solution. We do not penalise AI
usage.

We will, however, ask you to walk through and defend your solution in the
next interview round, so make sure you understand what you submitted.

## What We Care About

- Correctness and end-to-end reliability
- Data integrity and idempotency
- Validation and graceful failure handling
- Meaningful tests, including for edge cases
- Simple, maintainable code
- Clear tradeoffs, documented decisions, and honest communication

## What We Don't Care About

- Avoiding AI tools
- Perfect architecture
- Unnecessary frameworks
- Real cloud deployment
- Building a full production system
- Adding unrelated features

## Codebase Tour

```
src/dexter_sync/
  models.py            # Pydantic models + provider->internal mapping
  exceptions.py        # Error hierarchy: provider, rate-limit, malformed-record
  provider_client.py   # Mock provider — reads JSON fixtures, simulates failures
  repository.py        # In-memory Firestore-like store, no business logic
  sync.py              # Orchestrator — your main focus
tests/
  conftest.py
  test_public_sync.py            # behavioural tests — several are red
  test_public_provider_client.py # simulator sanity checks (already green)
docs/
  PROVIDER_API.md      # synthetic provider docs — read this carefully
data/
  provider_page_*.json           # paginated provider fixtures
  provider_page_with_errors.json # malformed records
  provider_page_judgment.json    # ambiguity fixtures (see PROVIDER_API.md)
  existing_residents.json        # pre-seeded internal state for staleness test
logs/
  failed_sync.log                # example log shape — read for context
```

## Submission

Submit either:
1. A GitHub repository link (private or public, both fine), or
2. A zip file with your solution.

Please include in your README:
- What you changed and why
- Tradeoffs you made
- What you would improve with more time
- How you used AI tools (if applicable) and how you verified output
- How you ran and validated your solution
- Your honest time spent

---

## Your Notes

A per-file record of what changed against the starter codebase lives in
[`CHANGELOG.md`](./CHANGELOG.md). This section covers tradeoffs, decisions,
and how to run the solution.

### Tradeoffs

- Retry backoff is intentionally tiny (0.05s → 0.5s cap, ±20% jitter,
  configured in `src/dexter_sync/conf.py`). The brief warns the grader has
  per-test timeouts and the simulator returns `retry_after=0`. A real
  deployment would raise `RETRY_MAX_BACKOFF_SECONDS` and probably the
  jitter band — change the constants in `conf.py` rather than hunting
  through `retry.py`.
- No async, no real HTTP client, no external retry library — keeping the
  surface minimal for the timebox.
- The repository stays a dumb dict store. Idempotency / staleness lives in
  the orchestrator as the brief asks.
- Out-of-range `care_level` ints are treated as malformed rather than
  silently clamped. If a future provider extends the documented 0–5 range
  this would need a code change — the alternative (silently storing the
  value) was deemed worse for a healthcare integration.
- A malformed `deleted_at` timestamp is treated as "deleted" with a
  warning rather than failing the record. Upstream intent ("this person
  is gone") matters more than a clean timestamp.
- JSON logging is opt-in via `dexter_sync.logging_config.configure_json_logging()`.
  The default install registers a `NullHandler` so library use stays quiet
  and `configure_json_logging` defaults `propagate=False` to avoid
  double-emission when an app already has root handlers.
- Naive datetimes from the provider (`last_updated`, `deleted_at`,
  `last_modified_by_caregiver` without a tz suffix) are normalised to UTC
  rather than rejected. This keeps comparisons safe across the strict-`>`
  staleness check; the alternative — rejecting any naive timestamp — would
  fail every record from a provider that simply omits the offset. The
  trade-off is documented in the decision memo.

### What I would improve with more time

- Per-cursor circuit breaking on top of the existing duplicate-cursor trap
  (e.g. give up on a given cursor after N transient failures across runs,
  not just within a single run).
- An optional async provider interface — paginated cursors are inherently
  sequential, but `asyncio.gather` over multiple per-region providers
  would parallelize fan-out cleanly.
- Hypothesis-based property tests for `_normalize_care_level` to fuzz the
  string / int / boundary space beyond the parametrized cases.
- Persisting `SyncResult.run_id` + counters to a metrics sink (Prometheus,
  StatsD) so retry rates and skip rates are observable over time.

### How I used AI tools

I used Claude Code (Opus) for the initial implementation plan and coding
pass. The plan was authored interactively, validated through a Plan
sub-agent, and checked against the public test contract, provider binding
rules, and malformed-record fixture before changes were accepted.

I also used Codex as a review partner after the main implementation. That
review focused on edge cases not fully covered by the starter tests:
malformed page envelopes, duplicate-cursor loops, non-dict records,
non-string / blank `residentId`, boolean coercion, whitespace
`deleted_at`, date-only datetimes, naive-vs-aware timestamp comparisons,
logging propagation, and retry-counter semantics. Each finding was turned
into a targeted test or documentation note before being kept. Every edit
was followed by `pytest`; the full suite passes 110/110 (11 public + 99
private).

### How to run and validate

```
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e ".[dev]"
pytest -q
pytest -q --durations=10   # confirms no test sleeps > ~0.2s
```

Slowest tests are the retry tests at ~0.15s each (one per-attempt
backoff), well under the 1s budget the brief mentions.

### Honest time spent

No more than 3 hours of focused work, including planning, reading the
provider docs, implementing the mapper / retry helper / orchestrator,
writing the private test suite, and a follow-up pass implementing the
"What I would improve with more time" items (jitter, run_id propagation,
JSON formatter, retry counter, idempotency test).

### Decision memo

1. **`care_level`** — normalized in `_normalize_care_level`. Accepts `int`
   (range-checked 0–5), numeric strings, `level_<n>` strings (case-insensitive
   on the prefix), and `null`. Anything else (`"high"`, `7`, booleans) is
   treated as a malformed record so a single bad row never silently
   downgrades a resident's care tier.
2. **`is_active` vs `deleted_at`** — `deleted_at` wins, per the v2.0
   changelog. `effective_is_active = (deleted_at is None) and is_active`.
   `deleted_at` represents a hard upstream removal; `is_active=false` is a
   softer "not currently in the home" signal. When the two conflict we
   trust the harder one.
3. **Staleness key** — `last_updated`. The v1.8 changelog leaves
   `last_modified_by_caregiver` informational, and using a tablet-edit
   timestamp for staleness would let unsynced caregiver edits clobber
   server-confirmed state. When `last_modified_by_caregiver > last_updated`
   we still write/skip on `last_updated` but emit a `result.warnings`
   entry so an operator can spot the divergence.
4. **Past the table** —
   - *Unknown `care_level` variant* (`"high"`, `7`, etc.): mapper raises
     `MalformedRecordError`; sync increments `failed` and continues. No
     value is preferable to silently storing a wrong tier.
   - *`deleted_at` in the future or un-set on a later sync*:
     presence-is-truthy — any non-blank `deleted_at` string marks the
     record inactive immediately (we don't compare against `now()`).
     Blank / whitespace-only strings are treated as absent so a padded
     blank from the provider doesn't accidentally deactivate. A later
     sync that omits `deleted_at` resurrects the resident as active
     because mapping is fresh per record (no sticky tombstone).
   - *`last_modified_by_caregiver` ahead of `last_updated`*: appended to
     `result.warnings`; not used in the `>` staleness comparison. A future
     "trust caregiver edits" feature flag could read this without
     re-architecting.
