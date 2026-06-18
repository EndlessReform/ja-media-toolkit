from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time
import threading
from pathlib import Path
from typing import Any

import duckdb
import kagglehub

DATASET_HANDLE = "calebmwelsh/anilist-anime-dataset"
CSV_NAME = "anilist_anime_data_complete.csv"

DEFAULT_FORMATS = ("TV", "ONA", "TV_SHORT")
ALL_FORMATS = DEFAULT_FORMATS + ("MOVIE", "OVA", "SPECIAL", "MUSIC")
JSON_COLUMNS = frozenset({"characters", "relations", "staff", "studios", "synonyms"})
RESERVED_DETAIL_COLUMNS = frozenset({"aid", "search_text"})

logger = logging.getLogger("ja_media_services.anilist_search.db")


def _safe_int(value: Any) -> int | str | None:
    """Convert to int if possible, else pass through the raw value (or None)."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return value


@dataclass
class RefreshStatus:
    """Mutable operational state for the background dataset refresh loop."""

    last_attempt_unix: float | None = None
    last_success_unix: float | None = None
    last_failure_unix: float | None = None
    last_failure: str | None = None
    consecutive_failures: int = 0
    last_update_unix: float | None = None
    last_index_rows: int | None = None

    def as_dict(self, *, stale_after_seconds: int) -> dict:
        now = time.time()
        last_success_age_seconds = None
        if self.last_success_unix is not None:
            last_success_age_seconds = round(now - self.last_success_unix, 3)
        return {
            "last_attempt_unix": self.last_attempt_unix,
            "last_success_unix": self.last_success_unix,
            "last_success_age_seconds": last_success_age_seconds,
            "last_failure_unix": self.last_failure_unix,
            "last_failure": self.last_failure,
            "consecutive_failures": self.consecutive_failures,
            "last_update_unix": self.last_update_unix,
            "last_index_rows": self.last_index_rows,
            "stale": (
                self.last_success_unix is None
                or (now - self.last_success_unix) > stale_after_seconds
            ),
        }


def ensure_dataset(data_dir: Path) -> Path:
    """Download only the CSV from Kaggle if not already cached locally."""
    csv_path = data_dir / CSV_NAME
    if csv_path.exists():
        return csv_path

    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading AniList dataset (first run)...")
    kagglehub.dataset_download(DATASET_HANDLE, path=CSV_NAME, output_dir=str(data_dir))
    return csv_path


def try_refresh_dataset(data_dir: Path) -> bool:
    """Check Kaggle for a newer dataset version and return True if CSV changed.

    KaggleHub resolves the unversioned dataset handle to the current upstream
    version before consulting its output_dir cache. That gives this service the
    desired behavior: poll hourly, but download only when Kaggle publishes a
    new version.
    """
    csv_path = data_dir / CSV_NAME
    old_signature = dataset_signature(csv_path) if csv_path.exists() else None

    kagglehub.dataset_download(
        DATASET_HANDLE, path=CSV_NAME, output_dir=str(data_dir)
    )

    new_signature = dataset_signature(csv_path)
    updated = new_signature != old_signature
    if updated:
        logger.warning(
            "AniList dataset changed; index will be rebuilt (old_signature=%s new_signature=%s)",
            old_signature,
            new_signature,
        )
    return updated


def open_db(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open a persistent DuckDB connection with FTS loaded."""
    con = duckdb.connect(str(db_path))
    con.execute("INSTALL fts")
    con.execute("LOAD fts")
    return con


def dataset_signature(csv_path: Path) -> str:
    """Return a cheap fingerprint for the downloaded Kaggle CSV."""
    stat = csv_path.stat()
    return f"size={stat.st_size};mtime_ns={stat.st_mtime_ns}"


def _metadata_value(con: duckdb.DuckDBPyConnection, key: str) -> str | None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    row = con.execute("SELECT value FROM metadata WHERE key = ?", [key]).fetchone()
    return str(row[0]) if row else None


def _set_metadata_value(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute(
        """
        INSERT INTO metadata (key, value)
        VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        [key, value],
    )


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def build_index(csv_path: Path, con: duckdb.DuckDBPyConnection, *, force: bool = False) -> int:
    """Materialize the AniList CSV and build a BM25 index.

    The search_text column concatenates title variants with repetition for
    weighting: english (3x) > romaji (2x) > native (1x) > synonyms (1x).
    Stemming and stopwords are disabled to preserve exact Japanese matching.

    The table intentionally keeps the full CSV payload, not just the FTS
    columns. AniList's Kaggle export is useful as a local metadata cache for
    ASR biasing and inventory work, and rebuilding this derived table is cheap
    enough that a wide row is simpler than maintaining a separate detail store.
    """
    signature = dataset_signature(csv_path)
    existing_signature = _metadata_value(con, "dataset_signature")
    if (
        not force
        and existing_signature == signature
        and _table_exists(con, "anime")
        and get_row_count(con) > 0
    ):
        row_count = get_row_count(con)
        logger.info(
            "AniList search index is current (rows=%d dataset_signature=%s)",
            row_count,
            signature,
        )
        return row_count

    logger.warning(
        "Rebuilding AniList search index (force=%s old_signature=%s new_signature=%s)",
        force,
        existing_signature,
        signature,
    )
    con.execute("DROP SCHEMA IF EXISTS fts_main_anime CASCADE")
    con.execute("DROP TABLE IF EXISTS anime")
    con.execute("""
        CREATE TABLE anime AS
        SELECT
            id::VARCHAR AS aid,
            title_romaji,
            title_english,
            title_native,
            format,
            * EXCLUDE (id, title_romaji, title_english, title_native, format),
            COALESCE(title_english, '') || ' ' || COALESCE(title_english, '') || ' ' ||
            COALESCE(title_english, '') || ' ' ||
            COALESCE(title_romaji, '') || ' ' || COALESCE(title_romaji, '') || ' ' ||
            COALESCE(title_native, '') || ' ' ||
            CASE WHEN synonyms IS NOT NULL THEN
                COALESCE((SELECT string_agg(value, ' ') FROM json_each(synonyms)), '')
            ELSE '' END
            AS search_text
        FROM read_csv_auto(?)
        WHERE id IS NOT NULL
    """, [str(csv_path)])
    con.execute("CREATE UNIQUE INDEX anime_aid_idx ON anime(aid)")

    con.commit()
    con.execute(
        "PRAGMA create_fts_index('anime', 'aid', 'search_text', "
        "stemmer='none', stopwords='none')"
    )
    con.commit()
    row_count = get_row_count(con)
    _set_metadata_value(con, "dataset_signature", signature)
    _set_metadata_value(con, "indexed_at_unix", str(time.time()))
    _set_metadata_value(con, "row_count", str(row_count))
    logger.warning("AniList search index rebuilt successfully (rows=%d)", row_count)
    return row_count


def rebuild_if_needed(
    csv_path: Path, db_path: Path, con: duckdb.DuckDBPyConnection
) -> int:
    """Rebuild the index when the downloaded CSV fingerprint changed."""
    del db_path
    return build_index(csv_path, con)


def rebuild_from_cached_csv(
    csv_path: Path, db_path: Path, con: duckdb.DuckDBPyConnection
) -> int:
    """Rebuild derived DuckDB tables from the cached AniList CSV.

    The Kaggle CSV cache is durable across container restarts, but the DuckDB
    schema is code-owned derived state. Startup rebuilds it unconditionally so
    deploys can widen or otherwise change the materialized table without asking
    users to manually clear the volume.
    """
    del db_path
    return build_index(csv_path, con, force=True)


def search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    top_k: int = 3,
    formats: tuple[str, ...] = DEFAULT_FORMATS,
) -> list[dict]:
    """Search anime by name using BM25, filtered to the given formats."""
    format_filter = ", ".join(f"'{f}'" for f in formats)
    rows = con.execute(f"""
        SELECT aid, title_english, title_native, title_romaji, season, seasonYear, format, score
        FROM (
            SELECT aid, title_english, title_native, title_romaji, season, seasonYear, format,
                   fts_main_anime.match_bm25(aid, ?) AS score
            FROM anime
            WHERE format IN ({format_filter})
        ) sq
        WHERE score IS NOT NULL
        ORDER BY score DESC
        LIMIT ?
    """, [query, top_k]).fetchall()

    return [
        {
            "anilist_id": int(row[0]) if row[0] else None,
            "title_english": row[1],
            "title_native": row[2],
            "title_romaji": row[3],
            "season": row[4],
            "season_year": _safe_int(row[5]),
            "format": row[6],
            "score": round(float(row[7]), 4),
        }
        for row in rows
    ]


def _available_detail_columns(con: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    rows = con.execute("PRAGMA table_info('anime')").fetchall()
    return tuple(str(row[1]) for row in rows)


def _detail_columns(
    con: duckdb.DuckDBPyConnection, requested_fields: tuple[str, ...] | None
) -> tuple[str, ...]:
    available = _available_detail_columns(con)
    available_set = set(available)
    public_columns = tuple(
        column for column in available if column not in RESERVED_DETAIL_COLUMNS
    )
    if requested_fields is None:
        return public_columns

    unknown = sorted(set(requested_fields) - available_set)
    forbidden = sorted(set(requested_fields) & RESERVED_DETAIL_COLUMNS)
    if unknown or forbidden:
        bad = ", ".join([*unknown, *forbidden])
        raise ValueError(f"Unknown AniList metadata field(s): {bad}")
    return tuple(requested_fields)


def _parse_detail_value(column: str, value: Any) -> Any:
    if value is None or column not in JSON_COLUMNS or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.warning("AniList metadata column %s did not contain valid JSON", column)
        return value


def fetch_anime_metadata(
    con: duckdb.DuckDBPyConnection,
    anilist_id: int | str,
    *,
    fields: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Return one AniList metadata row by ID, optionally narrowed to fields.

    The endpoint using this helper is intentionally a broad escape hatch while
    downstream workflows shake out which specialized projections are worth
    owning. Column names are validated against DuckDB metadata before SQL is
    assembled so callers can request fields without opening an injection path.
    """
    columns = _detail_columns(con, fields)
    select_columns = ["aid", *columns]
    quoted_columns = ", ".join(f'"{column}"' for column in select_columns)
    row = con.execute(
        f"SELECT {quoted_columns} FROM anime WHERE aid = ?", [str(anilist_id)]
    ).fetchone()
    if row is None:
        return None
    payload = dict(zip(select_columns, row, strict=True))
    payload["anilist_id"] = int(payload.pop("aid"))
    return {
        column: _parse_detail_value(column, value)
        for column, value in payload.items()
    }


def resolve_formats(
    include_movies: bool = False,
    include_ova: bool = False,
    all_formats: bool = False,
) -> tuple[str, ...]:
    if all_formats:
        return ALL_FORMATS
    formats = list(DEFAULT_FORMATS)
    if include_movies:
        formats.append("MOVIE")
    if include_ova:
        formats.append("OVA")
    return tuple(formats)


def get_row_count(con: duckdb.DuckDBPyConnection) -> int:
    """Return the number of indexed anime rows."""
    return con.execute("SELECT COUNT(*) FROM anime").fetchone()[0]


def background_refresh(
    data_dir: Path,
    db_path: Path,
    con: duckdb.DuckDBPyConnection,
    lock: threading.Lock,
    status: RefreshStatus,
    interval_seconds: int = 3600,
) -> None:
    """Run in a daemon thread; polls Kaggle for updates and rebuilds if needed."""
    csv_path = data_dir / CSV_NAME
    while True:
        time.sleep(interval_seconds)
        status.last_attempt_unix = time.time()
        try:
            updated = try_refresh_dataset(data_dir)
            row_count = None
            if updated:
                with lock:
                    row_count = rebuild_if_needed(csv_path, db_path, con)
                status.last_update_unix = time.time()
            else:
                with lock:
                    row_count = get_row_count(con)
            status.last_success_unix = time.time()
            status.last_failure = None
            status.consecutive_failures = 0
            status.last_index_rows = row_count
            logger.info(
                "AniList dataset refresh completed (updated=%s rows=%s)",
                updated,
                row_count,
            )
        except Exception as exc:
            status.last_failure_unix = time.time()
            status.last_failure = f"{type(exc).__name__}: {exc}"
            status.consecutive_failures += 1
            logger.exception(
                "AniList background dataset refresh failed (consecutive_failures=%d)",
                status.consecutive_failures,
            )
