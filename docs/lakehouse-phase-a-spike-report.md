# DuckLake on Garage: Phase A spike report

Date: 2026-07-14
Status: substrate accepted; literal cross-machine concurrency and backup
regimen confirmation remain operational follow-ups

## Scope and isolation

The spike used DuckDB/DuckLake 1.5.4 with the existing PostgreSQL 17 development
database as catalog and Garage as S3-compatible object storage. Destructive
work was isolated to:

- PostgreSQL schema `ja_media_ducklake_phase_a`; and
- Garage path `s3://ja-media-prod/audio/anime/lakehouse/phase-a/`.

The six existing identity-ledger relations in PostgreSQL `public` were not
modified. The repeatable harness is
The now-removed disposable spike harness enforced guardrails; its reset mode refused
catalog schemas without the `_phase_a` suffix and object prefixes without a
`phase-a` component.

## Measured results

| Check | Result | Evidence |
| --- | --- | --- |
| PostgreSQL catalog + Garage data path attach | Pass | Catalog initialized in the guarded schema; Parquet written through the path-style Garage endpoint. |
| Small append/data inlining | Pass | A three-row append created no table object in Garage. |
| Inlined-data flush | Pass | `ducklake_flush_inlined_data` reported rows and created Parquet in Garage. |
| Bulk append | Pass | A 25-row insert wrote successfully and remained readable. |
| `ALTER TABLE ADD COLUMN` | Pass | A nullable column was added and accepted a post-alter row without rewriting old rows. |
| Time travel | Pass | The pre-alter snapshot returned 28 rows while current state returned 29. |
| Snapshot expiry + cleanup | Pass | A 1,000-row dropped probe table had objects before expiry and none after `ducklake_cleanup_old_files`. |
| Concurrent clients | Partial | Two independent processes attached before a barrier and concurrently committed 20 rows each. This measures catalog conflict handling from one host, not the network path from two hosts. |
| Catalog recovery | Pass | A custom-format schema dump was made, the catalog schema was dropped, the dump was restored, and DuckLake read all 69 expected rows from Garage. |

The final clean run observed nine snapshots before expiry. Four objects from the
preceding interrupted/recovery runs were removed by the guarded reset before
the final measurement.

## Findings

Garage exposed no incompatibility in the operations exercised. DuckLake's
commit protocol worked with PostgreSQL while objects were written and deleted
through Garage using path-style addressing. Data inlining behaved as intended
for small metadata writes, and explicit flushing materialized those rows to
Parquet. The later C2 design does not use this as justification for
row-at-a-time domain writes: automatic identity products are recomputed and
replaced as batches, while human binding decisions live in PostgreSQL.

The recovery drill confirms the architectural warning in the migration plan:
Garage data is insufficient without the PostgreSQL catalog. Restoring the
catalog made the existing Parquet readable again; deleting the catalog without
a dump would lose table membership and snapshot history.

The workstation's default `pg_dump` was PostgreSQL 14 and refused the PostgreSQL
17 server. The harness now prefers Homebrew's current `libpq` tools (v18 during
the spike) and supports `JA_MEDIA_PG_BIN_DIR` for an explicit compatible client.

## Operational follow-ups

1. Run simultaneous appends from two configured operator machines. The
   single-host two-process result is strong evidence for transactional catalog
   behavior, but it does not exercise a second host's PostgreSQL and Garage
   network path.
2. Confirm or add the durable PostgreSQL backup job and its recovery-point
   objective for the DuckLake catalog. The operational procedure is documented
   in `site/src/content/docs/setup/lakehouse.md`; repository inspection cannot
   prove the external backup system includes this catalog.

These follow-ups do not invalidate the completed local implementation gates.
They remain deployment-readiness work because repository inspection cannot
verify multi-host networking or the external backup system.
