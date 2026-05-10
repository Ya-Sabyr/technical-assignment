# AcmeCare Provider API — Integration Notes

This is the integration brief for **AcmeCare**, a fictional third-party care
software vendor. It is intentionally imperfect: version notes contradict each
other, examples disagree with the schema, and a few corners are
underspecified. That is — exactly like real provider integrations.

Read this carefully before you write a line of code.

## Endpoints

### `GET /residents`

Returns a paginated list of residents.

**Query params:**

- `cursor` (optional): opaque pagination token. Omit for the first page.

**Response shape:**

```json
{
  "residents": [ /* Resident objects */ ],
  "next_cursor": "<token>"
}
```

When the response has no more pages, `next_cursor` is `null`.

> Operations note: we have seen deployments where the same `next_cursor` is
> returned on consecutive calls during transient backend issues. Guard
> against this.

### Errors

| HTTP | Class | Behaviour |
|------|-------|-----------|
| 200  | OK | Normal response. |
| 400  | Permanent | Malformed request, invalid auth. **Do not retry.** |
| 429  | Transient | Rate limit. Honour `Retry-After` if present. |
| 5xx  | Transient | Server-side. Retry with backoff. |

## Resident schema

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `residentId` | string | yes | Stable identifier per resident. |
| `first_name` | string | yes | Given name. |
| `lastName` | string | yes | Family name. (Note inconsistent casing — see Quirks.) |
| `dob` | string (ISO date) | optional | `YYYY-MM-DD`. May be `null` or missing. |
| `room` | string | optional | Free-form room/unit identifier. |
| `care_level` | integer | optional | `0` = no care, `5` = highest. **See Quirks.** |
| `last_updated` | string (ISO datetime) | yes | When this record was last server-side modified. |
| `is_active` | boolean | optional | Whether the resident is currently in the home. Default `true`. |
| `deleted_at` | string (ISO datetime) | optional *(added v2.0)* | If set, the resident has been removed. |
| `last_modified_by_caregiver` | string (ISO datetime) | optional *(added v1.8)* | Upstream tablet edit timestamp. |

## Binding rules (summary)

If you read nothing else in the changelog, read this. These rules are the
authoritative resolution of the ambiguities discussed below — the changelog
entries explain *why*, the table tells you *what*.

| Rule | Source | Authoritative behaviour |
|------|--------|-------------------------|
| `care_level` may arrive as int, numeric string (`"3"`), or level-prefixed string (`"level_3"`). All three should map to the same internal int. `null` is canonical when the level has not been assessed. | v2.1 changelog | Normalize to `int | None`. Comparison of the prefix is case-insensitive (`"Level_3"` == `"level_3"`). |
| `deleted_at` (when set) supersedes `is_active`. If `deleted_at` is set, treat the resident as inactive even if `is_active` is `true`. | v2.0 changelog | `effective_is_active = (deleted_at is None) and is_active`. |
| `last_updated` is the canonical staleness key for sync. `last_modified_by_caregiver` is informational only. | v1.8 changelog | Use `last_updated` for the `>=` / `>` comparison against the internal record. |

Anything not listed here — including conflict-resolution edge cases, retry
sizing, and counter semantics — is your call. Document the call in your
decision memo.

## Quirks and Gotchas

These are the inconsistencies you will encounter in real deployments. The
binding rules above tell you the documented resolution; the sections below
explain the underlying mess.

### `care_level` polymorphism

Although `care_level` is documented as an integer, the field is in practice
returned in several forms across deployments:

```json
{ "care_level": 3 }            // current spec
{ "care_level": "3" }          // legacy clients (v1.x)
{ "care_level": "level_3" }    // older legacy (pre-v1.x)
{ "care_level": null }         // value not yet captured
```

See changelog v2.1 below.

### Active / inactive semantics

Two mechanisms can mark a resident as inactive:

- `is_active: false` — a temporary or pre-v2.0 way of marking a resident as
  not currently in the home (e.g., on hospital leave).
- `deleted_at: <iso datetime>` — the resident has been removed. Added in v2.0.

These can coexist or contradict. See changelog v2.0.

### Timestamp precedence

`last_updated` is the canonical "last server-side write" timestamp. However,
some integrations also send `last_modified_by_caregiver` — a timestamp
derived from upstream caregiver-tablet edits.

It is possible (but rare) for `last_modified_by_caregiver` to be more recent
than `last_updated`. See changelog v1.8 below.

## Changelog (excerpt)

### v2.1 (2024-09)

- We are migrating `care_level` to integer-only. Legacy clients still send
  strings; you should map them transparently. Numeric strings (`"3"`) and
  level-prefixed strings (`"level_3"`) should round-trip to the same int.
  Prefix comparison is case-insensitive (`"Level_3"` == `"level_3"`).
- `null` is canonical when the resident's care level has not yet been
  assessed.

### v2.0 (2024-06)

- Added `deleted_at`. This field is the new authoritative way to mark a
  resident as removed. **When `deleted_at` is set, treat the resident as
  inactive even if `is_active` is `true`.** We will not retro-fix legacy
  records; some still rely on `is_active: false` only.

### v1.8 (2024-03)

- Added `last_modified_by_caregiver`. This timestamp is informational; it
  may run ahead of `last_updated` for short windows when caregivers edit on
  tablet. The integration team has not yet decided whether stale-write
  protection should consider this field, so for now `last_updated` remains
  canonical.
