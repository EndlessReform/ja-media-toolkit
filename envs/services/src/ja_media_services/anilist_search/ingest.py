from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from ja_media_services.anilist_search.dataset import ensure_dataset
from ja_media_services.anilist_search.db import build_index, open_db

logger = logging.getLogger("ja_media_services.anilist_search.ingest")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download AniList dataset and build search index")
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/anilist-search"))
    parser.add_argument("--db-path", type=Path, default=Path("/var/lib/anilist-search/anime_index.db"))
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.db_path.exists():
        args.db_path.unlink()

    csv_path = ensure_dataset(args.data_dir)
    con = open_db(args.db_path)
    build_index(csv_path, con)

    row_count = con.execute("SELECT COUNT(*) FROM anime").fetchone()[0]
    con.close()

    result = {"status": "ok", "rows": row_count, "db_path": str(args.db_path)}
    print(json.dumps(result, indent=2))
    logger.info("Index built: %d rows → %s", row_count, args.db_path)
