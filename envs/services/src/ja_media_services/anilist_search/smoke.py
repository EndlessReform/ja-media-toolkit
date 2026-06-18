from __future__ import annotations

import argparse
import json
from pathlib import Path

from ja_media_services.anilist_search.db import get_row_count, open_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test an AniList search DB")
    parser.add_argument("db_path", type=Path)
    args = parser.parse_args()

    if not args.db_path.exists():
        print(f"FAIL: DB does not exist: {args.db_path}")
        raise SystemExit(1)

    con = open_db(args.db_path)
    try:
        row_count = get_row_count(con)
        if row_count <= 0:
            print("FAIL: DB has no rows")
            raise SystemExit(1)
        print(json.dumps({"status": "ok", "rows": row_count}, indent=2))
    finally:
        con.close()
