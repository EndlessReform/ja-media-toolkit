# Partitions, identity, and PostgreSQL ledger

Status: approved shape; migrations and repository code not yet implemented.

## Identity boundary

Before acceptance, work is keyed by evidence:

```text
bronze_capture[capture-01J...]
episode_hint[capture-01J...]
binding_candidate[capture-01J...]
```

After acceptance, consumer-oriented work is keyed by a locator:

```text
episode_binding[anilist-15451/e003]
portable_aac[anilist-15451/e003]
display_bundle[anilist-15451/e003]
```

Titles are metadata, never keys. Episode components are stored as text so
special-numbering policy is not accidentally constrained by an integer column.

## Database and configuration

Use database `ja_media_data` and a dedicated least-privilege principal on the
shared PostgreSQL server. The server's persistent volume must be flash-backed.

The data code location receives:

```text
JA_MEDIA_DATA_DATABASE_URL=postgresql+psycopg://<principal>:<password>@<host>:5432/ja_media_data
```

The value belongs in the code-location environment or local ignored `.env`, not
in repository files. SQLAlchemy owns connections and transactions; Alembic owns
schema migrations. Dagster's `DAGSTER_PG_*` settings point to a different
database and principal.

## Common provenance columns

Every derived claim includes enough information to reproduce or invalidate it:

| Column | Meaning |
| --- | --- |
| `input_data_version` | ETag/hash/version of the exact upstream evidence |
| `recipe_version` | Version of parser, resolver, policy, or model |
| `created_at` | UTC commit time (`timestamptz`) |
| `dagster_run_id` | Nullable diagnostic only; never identity |

Domain IDs are generated outside PostgreSQL and stored as text so exports and
reimports preserve identity.

## `bronze_captures`

Hot index over canonical Garage manifests:

```text
capture_id              text primary key
series_namespace        text not null
series_id               text not null
manifest_bucket         text not null
manifest_key            text not null
manifest_etag           text not null
manifest_schema_version integer not null
first_observed_at       timestamptz not null
last_observed_at        timestamptz not null
unique (manifest_bucket, manifest_key)
```

This table is rebuildable. The Garage manifest remains the bronze contract.

## `episode_hints`

Immutable resolver claims:

```text
hint_id                 text primary key
capture_id              text references bronze_captures
candidate_namespace     text not null
candidate_series_id     text not null
candidate_episode       text not null
method                  text not null
confidence              double precision
evidence                jsonb not null
input_data_version      text not null
recipe_version          text not null
created_at              timestamptz not null
dagster_run_id          text
```

The idempotency key covers capture, input version, recipe version, method, and
candidate locator. A capture may have zero, one, or several hints.

## `episode_bindings`

Immutable accepted or rejected decisions:

```text
binding_id              text primary key
namespace               text not null
series_id               text not null
episode                 text not null
audio_capture_id        text references bronze_captures
decision                text not null       -- accepted or rejected
decision_method         text not null       -- automatic or human-confirmed
decision_evidence       jsonb not null
input_data_version      text not null
recipe_version          text not null
supersedes_binding_id   text references episode_bindings
created_at              timestamptz not null
dagster_run_id          text
```

Rows are never rewritten to correct history.

## `current_episode_bindings`

Small mutable projection updated in the same transaction that accepts or
supersedes a binding:

```text
namespace               text not null
series_id               text not null
episode                 text not null
binding_id              text unique references episode_bindings
audio_capture_id        text unique references bronze_captures
updated_at              timestamptz not null
primary key (namespace, series_id, episode)
```

The primary key prevents two current captures for one locator. The unique audio
capture constraint enforces the initial one-capture/one-episode policy. Inputs
that need multi-episode semantics are quarantined until that policy is designed
from real corpus evidence.

## `episode_resolution_issues`

Inspectable review queue, not merely failed Dagster runs:

```text
issue_id                text primary key
capture_id              text references bronze_captures
hint_id                 text references episode_hints
kind                    text not null       -- conflict, overlap, ambiguous, invalid
details                 jsonb not null
status                  text not null       -- open, resolved, ignored
created_at              timestamptz not null
resolved_at             timestamptz
resolution_note         text
```

Infrastructure failures fail and retry the Dagster run. A legitimate ambiguous
mapping commits an open issue and is a successful, inspectable domain outcome.

## Subtitle tracks

When binding subtitles becomes part of the spike, use a child table rather
than a JSON array:

```text
episode_binding_tracks(binding_id, provider, track_id, role)
```

No subtitle table is required for the first audio-only binding gate.

## Snapshot location

PostgreSQL is the live ledger. A separate Dagster asset exports consistent,
versioned snapshots after database commits:

```text
audio/anime/silver/catalog/{table}/versions/{data_version}/
  data.parquet
  manifest.json        # written last
```

Snapshots support recovery and corpus analytics. They are not the point-lookup
path used by incremental processing.
