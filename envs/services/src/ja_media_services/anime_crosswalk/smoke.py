from __future__ import annotations

import argparse
import json
from pathlib import Path

from ja_media_services.anime_crosswalk.db import validate_generated_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test an anime crosswalk DB")
    parser.add_argument("db_path", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_generated_db(args.db_path), indent=2, sort_keys=True))
