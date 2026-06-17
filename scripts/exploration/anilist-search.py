#!/usr/bin/env -S uv run --with duckdb --with rich --with kagglehub --with fastapi --with uvicorn --no-sync
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "duckdb",
#     "rich",
#     "kagglehub",
#     "fastapi",
#     "uvicorn",
# ]
# ///
"""Resolve anime names to AniList IDs via cached DuckDB FTS index.

Builds a BM25 index over title_romaji, title_english, title_native, and
synonyms. Defaults to TV series only; use --include-movies/--include-ova
to widen the net.

Subcommands:
    search <query>   CLI one-shot search (default)
    server           Start FastAPI HTTP server with /search endpoint
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import kagglehub
import uvicorn
from fastapi import FastAPI, Query
from rich.console import Console
from rich.table import Table

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_DIR = SCRIPT_DIR / "cache"
CACHE_DB = CACHE_DIR / "anime_index.db"
DATASET_HANDLE = "calebmwelsh/anilist-anime-dataset"
CSV_NAME = "anilist_anime_data_complete.csv"

# Default formats to include (no movies/OVAs)
DEFAULT_FORMATS = ("TV", "ONA", "TV_SHORT")
ALL_FORMATS = DEFAULT_FORMATS + ("MOVIE", "OVA", "SPECIAL", "MUSIC")

logger = logging.getLogger("anilist-search")


# ---------------------------------------------------------------------------
# Dataset & index helpers
# ---------------------------------------------------------------------------

def ensure_dataset() -> Path:
    """Download only the CSV from Kaggle if not already cached locally."""
    csv_path = CACHE_DIR / CSV_NAME
    if csv_path.exists():
        return csv_path

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    console = Console()
    console.print("[yellow]Downloading dataset (first run or --refresh)...[/]")
    kagglehub.dataset_download(DATASET_HANDLE, path=CSV_NAME, output_dir=str(CACHE_DIR))
    return csv_path


def try_refresh_dataset() -> bool:
    """Attempt to re-download; returns True if the CSV was updated."""
    csv_path = CACHE_DIR / CSV_NAME
    old_mtime = csv_path.stat().st_mtime if csv_path.exists() else 0

    # kagglehub.dataset_download is cheap when file is current (~0.2s header check).
    # Only re-downloads if remote Last-Modified >= local mtime.
    kagglehub.dataset_download(
        DATASET_HANDLE, path=CSV_NAME, output_dir=str(CACHE_DIR)
    )

    new_mtime = csv_path.stat().st_mtime
    updated = new_mtime > old_mtime
    if updated:
        logger.info("Dataset updated, index will be rebuilt.")
    return updated


def build_index(csv_path: Path, con: duckdb.DuckDBPyConnection) -> None:
    """Materialize search-relevant columns and build a BM25 index.

    The search_text column concatenates title variants with repetition for
    weighting: english (3x) > romaji (2x) > native (1x) > synonyms (1x).
    Stemming and stopwords are disabled since we're indexing Japanese too.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS anime (
            aid VARCHAR PRIMARY KEY,
            title_romaji VARCHAR,
            title_english VARCHAR,
            title_native VARCHAR,
            format VARCHAR,
            search_text VARCHAR
        )
    """)

    count = con.execute("SELECT COUNT(*) FROM anime").fetchone()[0]
    if count > 0:
        return

    # DuckDB's CSV reader is lazy (vectorized row-by-row), so selecting only needed
    # columns avoids materializing the full 62-column schema in memory.
    con.execute(f"""
        INSERT INTO anime (aid, title_romaji, title_english, title_native, format, search_text)
        SELECT
            id::VARCHAR,
            title_romaji,
            title_english,
            title_native,
            format,
            COALESCE(title_english, '') || ' ' || COALESCE(title_english, '') || ' ' ||
            COALESCE(title_english, '') || ' ' ||
            COALESCE(title_romaji, '') || ' ' || COALESCE(title_romaji, '') || ' ' ||
            COALESCE(title_native, '') || ' ' ||
            CASE WHEN synonyms IS NOT NULL THEN
                (SELECT string_agg(value, ' ') FROM json_each(synonyms))
            ELSE '' END
        FROM read_csv_auto('{csv_path}')
        WHERE id IS NOT NULL
    """)

    con.execute(
        "PRAGMA create_fts_index('anime', 'aid', 'search_text', "
        "stemmer='none', stopwords='none')"
    )


def rebuild_if_needed(csv_path: Path, con: duckdb.DuckDBPyConnection) -> None:
    """Rebuild the index if the CSV is newer than the DuckDB file."""
    csv_mtime = csv_path.stat().st_mtime
    db_mtime = CACHE_DB.stat().st_mtime if CACHE_DB.exists() else 0

    if csv_mtime > db_mtime:
        logger.info("CSV newer than index, rebuilding...")
        con.execute("DROP TABLE IF EXISTS anime")
        build_index(csv_path, con)


def open_db() -> duckdb.DuckDBPyConnection:
    """Open a persistent DuckDB connection with FTS loaded."""
    con = duckdb.connect(str(CACHE_DB))
    con.execute("INSTALL fts")
    con.execute("LOAD fts")
    return con


# ---------------------------------------------------------------------------
# Search logic
# ---------------------------------------------------------------------------

def search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    top_k: int = 3,
    formats: tuple[str, ...] = DEFAULT_FORMATS,
) -> list[tuple]:
    """Search anime by name using BM25, filtered to the given formats."""
    format_filter = ", ".join(f"'{f}'" for f in formats)
    return con.execute(f"""
        SELECT aid, title_english, title_native, title_romaji, format, score
        FROM (
            SELECT aid, title_english, title_native, title_romaji, format,
                   fts_main_anime.match_bm25(aid, ?) AS score
            FROM anime
            WHERE format IN ({format_filter})
        ) sq
        WHERE score IS NOT NULL
        ORDER BY score DESC
        LIMIT ?
    """, [query, top_k]).fetchall()


# ---------------------------------------------------------------------------
# Result formatters
# ---------------------------------------------------------------------------

def build_table(results: list[tuple]) -> Table:
    """Format results as a Rich table."""
    table = Table(title="Anime Search Results")
    table.add_column("AL", justify="right", style="cyan", width=6)
    table.add_column("EN", style="bold green")
    table.add_column("JA", style="magenta")
    table.add_column("Romaji", style="yellow")
    table.add_column("Format", justify="center", style="dim", width=7)
    table.add_column("Score", justify="right", style="dim", width=8)

    for row in results:
        aid, english, native, romaji, fmt, score = row
        table.add_row(
            str(aid),
            english or "-",
            native or "-",
            romaji or "-",
            fmt or "-",
            f"{score:.4f}",
        )

    return table


def results_to_dicts(results: list[tuple]) -> list[dict]:
    """Format results as plain dicts for JSON serialization."""
    out = []
    for aid, english, native, romaji, fmt, score in results:
        out.append({
            "anilist_id": int(aid) if aid else None,
            "title_english": english,
            "title_native": native,
            "title_romaji": romaji,
            "format": fmt,
            "score": round(score, 4),
        })
    return out


# ---------------------------------------------------------------------------
# Format filter helpers
# ---------------------------------------------------------------------------

def resolve_formats(include_movies: bool = False, include_ova: bool = False, all_formats: bool = False) -> tuple[str, ...]:
    if all_formats:
        return ALL_FORMATS
    if include_movies:
        return DEFAULT_FORMATS + ("MOVIE",)
    if include_ova:
        return DEFAULT_FORMATS + ("OVA",)
    return DEFAULT_FORMATS


# ---------------------------------------------------------------------------
# CLI (search subcommand)
# ---------------------------------------------------------------------------

def run_search(args: argparse.Namespace) -> None:
    console = Console()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.refresh:
        if CACHE_DB.exists():
            CACHE_DB.unlink()
        csv_file = CACHE_DIR / CSV_NAME
        if csv_file.exists():
            csv_file.unlink()

    csv_path = ensure_dataset()
    con = open_db()
    build_index(csv_path, con)

    formats = resolve_formats(args.include_movies, args.include_ova, args.all_formats)
    results = search(con, args.query, args.top_k, formats)
    con.close()

    if not results:
        console.print("[red]No results found.[/]")
        sys.exit(1)

    console.print(build_table(results))


# ---------------------------------------------------------------------------
# Server (FastAPI)
# ---------------------------------------------------------------------------

class AppState:
    """Holds the persistent DuckDB connection and dataset path."""
    con: duckdb.DuckDBPyConnection | None = None
    csv_path: Path | None = None
    _lock = threading.Lock()


app_state = AppState()


def _background_refresh(interval_seconds: int = 3600) -> None:
    """Run in a daemon thread; polls Kaggle every *interval_seconds* for updates."""
    while True:
        time.sleep(interval_seconds)
        try:
            updated = try_refresh_dataset()
            if updated and app_state.csv_path:
                with app_state._lock:
                    rebuild_if_needed(app_state.csv_path, app_state.con)  # type: ignore[arg-type]
        except Exception:
            logger.exception("Background dataset refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = ensure_dataset()

    con = open_db()
    build_index(csv_path, con)

    app_state.con = con
    app_state.csv_path = csv_path

    # Start background refresh thread
    t = threading.Thread(target=_background_refresh, daemon=True)
    t.start()
    logger.info("Server started — background refresh enabled (hourly)")

    yield

    con.close()
    app_state.con = None


def create_app() -> FastAPI:
    app = FastAPI(
        title="AniList Anime Search",
        description="BM25 search over the AniList anime dataset, resolved to AniList IDs.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/search")
    def search_endpoint(
        query: str = Query(..., min_length=1, description="Anime name to search for"),
        k: int = Query(3, ge=1, le=50, description="Number of results (default: 3)"),
        include_movies: bool = Query(False, description="Include MOVIE format"),
        include_ova: bool = Query(False, description="Include OVA format"),
        all_formats: bool = Query(False, description="Include all formats"),
    ) -> list[dict]:
        con = app_state.con
        if con is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="Index not ready")

        formats = resolve_formats(include_movies, include_ova, all_formats)
        with app_state._lock:
            results = search(con, query, k, formats)
        return results_to_dicts(results)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "rows": (
            app_state.con.execute("SELECT COUNT(*) FROM anime").fetchone()[0]  # type: ignore[union-attr]
        )}

    return app


def run_server(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


# ---------------------------------------------------------------------------
# Main / arg parsing
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search anime by name, resolve to AniList ID"
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- search subcommand (also the default behavior) ---
    search_p = subparsers.add_parser("search", help="One-shot CLI search")
    search_p.add_argument("query", help="Anime name to search for")
    search_p.add_argument("-k", "--top-k", type=int, default=3, help="Number of results (default: 3)")
    search_p.add_argument("--refresh", action="store_true", help="Force re-download and rebuild index")
    fmt = search_p.add_mutually_exclusive_group()
    fmt.add_argument("--include-movies", action="store_true", help="Also include MOVIE format")
    fmt.add_argument("--include-ova", action="store_true", help="Also include OVA format")
    fmt.add_argument("--all-formats", action="store_true", help="Include all formats")

    # --- server subcommand ---
    server_p = subparsers.add_parser("server", help="Start FastAPI HTTP server")
    server_p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    server_p.add_argument("--port", type=int, default=8100, help="Port (default: 8100)")

    # Backward compat: if first arg looks like a query string (no subcommand),
    # prepend "search" so the subparser picks it up.
    argv = sys.argv[1:]
    if argv and argv[0] not in ("search", "server", "-h", "--help"):
        argv.insert(0, "search")

    args = parser.parse_args(argv)

    if args.command == "server":
        run_server(args)
    else:
        run_search(args)


if __name__ == "__main__":
    main()
