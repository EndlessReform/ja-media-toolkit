from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_FORMATS = ("TV", "ONA", "TV_SHORT")
ALL_FORMATS = DEFAULT_FORMATS + ("MOVIE", "OVA", "SPECIAL", "MUSIC")

logger = logging.getLogger("ja_media_services.anilist_search.db")


def _safe_int(value: Any) -> int | str | None:
    """Convert to int if possible, else pass through the raw value (or None)."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return value


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
) -> tuple[int, duckdb.DuckDBPyConnection]:
    """Build a fresh derived database and atomically publish it.

    Repeated in-place FTS rebuilds can leave deleted catalog dependencies.
    Building a sibling database preserves the active index until its complete
    replacement, including the new FTS catalog, is known to be valid.
    """
    rebuild_path = db_path.with_name(f".{db_path.name}.rebuild")
    rebuild_artifacts = (rebuild_path, Path(f"{rebuild_path}.wal"))
    rebuild_con: duckdb.DuckDBPyConnection | None = None

    for artifact in rebuild_artifacts:
        artifact.unlink(missing_ok=True)
    try:
        rebuild_con = open_db(rebuild_path)
        row_count = build_index(csv_path, rebuild_con, force=True)
        rebuild_con.close()
        rebuild_con = None

        con.close()
        rebuild_path.replace(db_path)
        return row_count, open_db(db_path)
    except Exception:
        if rebuild_con is not None:
            rebuild_con.close()
        for artifact in rebuild_artifacts:
            artifact.unlink(missing_ok=True)
        raise


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
