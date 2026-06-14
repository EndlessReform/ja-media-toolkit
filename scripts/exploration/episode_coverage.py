#!/usr/bin/env python
"""Explore episode coverage in the Kitsunekko filename inventory.

This is intentionally an exploration script, not a stable parser. The report is
meant to answer two practical questions before we design the persistent service:

* If every subtitle source for a title is merged, what contiguous episode spans
  appear to be available?
* Which release groups look like they covered a full run, and which look like
  they started a run and then stopped?

The filename parser is conservative but imperfect. The script therefore writes
CSV files with examples and parser-confidence hints so suspicious rows can be
smoke-tested by eye.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


SXXEYY_RE = re.compile(r"(?i)\bS(?P<season>\d{1,2})E(?P<episode>\d{1,4})\b")
EPISODE_WORD_RE = re.compile(
    r"(?ix)\b(?:episode|ep|eps|話|第)\s*[._ -]?\s*(?P<episode>\d{1,4})"
)
DATED_PAREN_EP_RE = re.compile(
    r"(?x)\b(?:19|20)\d{2}[.-]\d{1,2}[.-]\d{1,2}\s*-\s*\((?P<episode>\d{1,4})\)"
)
LEADING_GROUP_RE = re.compile(r"^\[(?P<group>[^\]]+)\]")
BRACKET_RE = re.compile(r"\[(?P<tag>[^\]]+)\]")
LANG_SUFFIX_RE = re.compile(
    r"(?i)\.(?P<lang>[a-z]{2,3}(?:-[a-z0-9]+)?)(?:\[[^\]]+\])?\.(?:srt|ass|ssa)$"
)
BARE_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?P<number>\d{1,4})(?![A-Za-z0-9])")

IGNORED_BARE_NUMBERS = {
    480,
    540,
    576,
    720,
    1080,
    1280,
    1920,
    2160,
    264,
    265,
}


@dataclass(frozen=True)
class EpisodeGuess:
    """A parsed episode number plus enough context to audit bad guesses."""

    episode: int | None
    method: str
    confidence: str
    token: str = ""


@dataclass
class SubtitleRow:
    """One row from the inventory with derived exploration fields."""

    fname: str
    extension: str
    anilist_id: int
    name: str
    english_name: str
    japanese_name: str
    group: str
    language_hint: str
    tags: tuple[str, ...]
    episode_guess: EpisodeGuess


@dataclass
class TitleCoverage:
    """Aggregated episode information for one AniList title."""

    anilist_id: int
    name: str = ""
    english_name: str = ""
    japanese_name: str = ""
    rows: int = 0
    parsed_rows: int = 0
    episodes: set[int] = field(default_factory=set)
    parser_methods: Counter[str] = field(default_factory=Counter)


@dataclass
class GroupCoverage:
    """Aggregated episode information for one leading bracket group and title."""

    anilist_id: int
    group: str
    name: str = ""
    rows: int = 0
    parsed_rows: int = 0
    episodes: set[int] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)
    unparsed_examples: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to kitsuinfo_filenames.jsonl.gz or uncompressed JSONL.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("scripts/exploration/out"),
        help="Directory for Markdown and CSV outputs.",
    )
    parser.add_argument(
        "--min-title-span",
        type=int,
        default=6,
        help="Smallest merged contiguous span to include in the title report.",
    )
    parser.add_argument(
        "--complete-threshold",
        type=float,
        default=0.9,
        help="Group coverage ratio treated as probably complete.",
    )
    return parser.parse_args()


def open_jsonl(path: Path) -> Iterable[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def normalize_group(fname: str) -> str:
    match = LEADING_GROUP_RE.search(fname)
    if not match:
        return "(no leading group)"
    group = re.sub(r"\s+", " ", match.group("group")).strip()
    return group or "(empty leading group)"


def language_hint(fname: str) -> str:
    suffix_match = LANG_SUFFIX_RE.search(fname)
    if suffix_match:
        return suffix_match.group("lang").lower()

    bracket_tags = [tag.lower() for tag in BRACKET_RE.findall(fname)]
    for tag in bracket_tags:
        if "jpn" in tag or "japanese" in tag:
            return "jpn"
        if re.search(r"\bjp\b|\bja\b", tag):
            return "jp"
        if "chs" in tag or "cht" in tag:
            return "zh"
        if "eng" in tag or re.search(r"\ben\b", tag):
            return "en"
    return "unknown"


def parse_episode(fname: str) -> EpisodeGuess:
    sxxeyy_match = SXXEYY_RE.search(fname)
    if sxxeyy_match:
        token = sxxeyy_match.group(0)
        return EpisodeGuess(
            episode=int(sxxeyy_match.group("episode")),
            method="sxxeyy",
            confidence="high",
            token=token,
        )

    word_match = EPISODE_WORD_RE.search(fname)
    if word_match:
        token = word_match.group(0)
        return EpisodeGuess(
            episode=int(word_match.group("episode")),
            method="episode_word",
            confidence="medium",
            token=token,
        )

    dated_paren_match = DATED_PAREN_EP_RE.search(fname)
    if dated_paren_match:
        token = dated_paren_match.group(0)
        return EpisodeGuess(
            episode=int(dated_paren_match.group("episode")),
            method="dated_parenthesized",
            confidence="medium",
            token=token,
        )

    candidates: list[tuple[int, str]] = []
    for match in BARE_NUMBER_RE.finditer(fname):
        number = int(match.group("number"))
        token = match.group("number")
        before = fname[max(0, match.start() - 2) : match.start()].lower()
        after = fname[match.end() : match.end() + 2].lower()
        if number in IGNORED_BARE_NUMBERS:
            continue
        if 1900 <= number <= 2099:
            continue
        if before.endswith("x") or after.startswith("p"):
            continue
        if 0 < number <= 1500:
            candidates.append((number, token))

    if candidates:
        number, token = candidates[0]
        confidence = "medium" if number <= 250 else "low"
        return EpisodeGuess(
            episode=number,
            method="bare_number",
            confidence=confidence,
            token=token,
        )

    return EpisodeGuess(episode=None, method="unparsed", confidence="none")


def row_from_json(raw: dict) -> SubtitleRow:
    fname = raw["fname"]
    return SubtitleRow(
        fname=fname,
        extension=str(raw.get("extension", "")).lower(),
        anilist_id=int(raw["anilist_id"]),
        name=str(raw.get("name") or ""),
        english_name=str(raw.get("english_name") or ""),
        japanese_name=str(raw.get("japanese_name") or ""),
        group=normalize_group(fname),
        language_hint=language_hint(fname),
        tags=tuple(BRACKET_RE.findall(fname)),
        episode_guess=parse_episode(fname),
    )


def contiguous_spans(episodes: set[int]) -> list[tuple[int, int]]:
    if not episodes:
        return []
    sorted_eps = sorted(episodes)
    spans: list[tuple[int, int]] = []
    start = prev = sorted_eps[0]
    for episode in sorted_eps[1:]:
        if episode == prev + 1:
            prev = episode
            continue
        spans.append((start, prev))
        start = prev = episode
    spans.append((start, prev))
    return spans


def span_len(span: tuple[int, int]) -> int:
    return span[1] - span[0] + 1


def format_spans(spans: list[tuple[int, int]], limit: int = 8) -> str:
    rendered = [
        str(start) if start == end else f"{start}-{end}" for start, end in spans[:limit]
    ]
    if len(spans) > limit:
        rendered.append(f"...+{len(spans) - limit} more")
    return ", ".join(rendered)


def add_example(bucket: list[str], fname: str, limit: int = 5) -> None:
    if len(bucket) < limit:
        bucket.append(fname)


def classify_group_coverage(
    group_cov: GroupCoverage,
    title_cov: TitleCoverage,
    complete_threshold: float,
) -> tuple[str, float, str]:
    if not title_cov.episodes or not group_cov.episodes:
        return "unparsed", 0.0, "no parsed episodes"

    title_min = min(title_cov.episodes)
    title_max = max(title_cov.episodes)
    overlap = len(group_cov.episodes & title_cov.episodes)
    ratio = overlap / len(title_cov.episodes)
    group_max = max(group_cov.episodes)
    group_min = min(group_cov.episodes)

    if ratio >= complete_threshold:
        return "probably_complete", ratio, "covers most merged episodes"

    starts_near_beginning = group_min <= title_min + 1
    stops_before_end = group_max <= title_max - max(3, round(len(title_cov.episodes) * 0.15))
    has_real_run = len(group_cov.episodes) >= 3
    if starts_near_beginning and stops_before_end and has_real_run:
        return "possible_gave_up", ratio, "starts near beginning and stops well before merged end"

    if len(group_cov.episodes) >= 3:
        return "partial_or_alt_span", ratio, "has a run but not near-complete"

    return "one_off", ratio, "one or two parsed episodes"


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    title_coverages: dict[int, TitleCoverage] = {}
    group_coverages: dict[tuple[int, str], GroupCoverage] = {}
    parser_methods: Counter[str] = Counter()
    language_hints: Counter[str] = Counter()
    groups: Counter[str] = Counter()
    rows_total = 0

    for raw in open_jsonl(args.input):
        rows_total += 1
        row = row_from_json(raw)
        parser_methods[row.episode_guess.method] += 1
        language_hints[row.language_hint] += 1
        groups[row.group] += 1

        title = title_coverages.setdefault(row.anilist_id, TitleCoverage(row.anilist_id))
        title.name = title.name or row.name
        title.english_name = title.english_name or row.english_name
        title.japanese_name = title.japanese_name or row.japanese_name
        title.rows += 1
        title.parser_methods[row.episode_guess.method] += 1

        group_key = (row.anilist_id, row.group)
        group = group_coverages.setdefault(
            group_key, GroupCoverage(row.anilist_id, row.group)
        )
        group.name = group.name or row.name
        group.rows += 1
        add_example(group.examples, row.fname)

        if row.episode_guess.episode is None:
            add_example(group.unparsed_examples, row.fname)
            continue

        title.parsed_rows += 1
        title.episodes.add(row.episode_guess.episode)
        group.parsed_rows += 1
        group.episodes.add(row.episode_guess.episode)

    title_rows: list[dict] = []
    for title in title_coverages.values():
        spans = contiguous_spans(title.episodes)
        biggest_span = max(spans, key=span_len) if spans else None
        biggest_span_size = span_len(biggest_span) if biggest_span else 0
        if biggest_span_size < args.min_title_span:
            continue
        title_rows.append(
            {
                "anilist_id": title.anilist_id,
                "name": title.name,
                "english_name": title.english_name,
                "japanese_name": title.japanese_name,
                "rows": title.rows,
                "parsed_rows": title.parsed_rows,
                "unique_episodes": len(title.episodes),
                "biggest_span_size": biggest_span_size,
                "biggest_span": ""
                if biggest_span is None
                else f"{biggest_span[0]}-{biggest_span[1]}",
                "all_spans": format_spans(spans),
                "parser_methods": "; ".join(
                    f"{method}:{count}"
                    for method, count in title.parser_methods.most_common()
                ),
            }
        )
    title_rows.sort(
        key=lambda row: (row["biggest_span_size"], row["unique_episodes"], row["rows"]),
        reverse=True,
    )

    group_rows: list[dict] = []
    gave_up_rows: list[dict] = []
    complete_rows: list[dict] = []
    for group in group_coverages.values():
        title = title_coverages[group.anilist_id]
        status, ratio, reason = classify_group_coverage(
            group, title, args.complete_threshold
        )
        spans = contiguous_spans(group.episodes)
        biggest_span = max(spans, key=span_len) if spans else None
        row = {
            "anilist_id": group.anilist_id,
            "name": group.name,
            "group": group.group,
            "status": status,
            "coverage_ratio": f"{ratio:.3f}",
            "reason": reason,
            "rows": group.rows,
            "parsed_rows": group.parsed_rows,
            "unique_group_episodes": len(group.episodes),
            "unique_title_episodes": len(title.episodes),
            "group_episode_min": min(group.episodes) if group.episodes else "",
            "group_episode_max": max(group.episodes) if group.episodes else "",
            "title_episode_min": min(title.episodes) if title.episodes else "",
            "title_episode_max": max(title.episodes) if title.episodes else "",
            "biggest_group_span_size": span_len(biggest_span) if biggest_span else 0,
            "group_spans": format_spans(spans),
            "examples": " | ".join(group.examples[:3]),
            "unparsed_examples": " | ".join(group.unparsed_examples[:3]),
        }
        group_rows.append(row)
        if status == "possible_gave_up":
            gave_up_rows.append(row)
        elif status == "probably_complete":
            complete_rows.append(row)

    group_rows.sort(
        key=lambda row: (
            row["status"] != "probably_complete",
            row["status"] != "possible_gave_up",
            row["anilist_id"],
            row["group"],
        )
    )
    gave_up_rows.sort(
        key=lambda row: (
            float(row["coverage_ratio"]),
            -int(row["unique_group_episodes"]),
            row["anilist_id"],
        )
    )
    complete_rows.sort(
        key=lambda row: (
            -int(row["unique_group_episodes"]),
            row["anilist_id"],
            row["group"],
        )
    )

    write_csv(
        args.out_dir / "merged_title_spans.csv",
        title_rows,
        [
            "anilist_id",
            "name",
            "english_name",
            "japanese_name",
            "rows",
            "parsed_rows",
            "unique_episodes",
            "biggest_span_size",
            "biggest_span",
            "all_spans",
            "parser_methods",
        ],
    )
    write_csv(
        args.out_dir / "group_coverage.csv",
        group_rows,
        [
            "anilist_id",
            "name",
            "group",
            "status",
            "coverage_ratio",
            "reason",
            "rows",
            "parsed_rows",
            "unique_group_episodes",
            "unique_title_episodes",
            "group_episode_min",
            "group_episode_max",
            "title_episode_min",
            "title_episode_max",
            "biggest_group_span_size",
            "group_spans",
            "examples",
            "unparsed_examples",
        ],
    )
    write_csv(
        args.out_dir / "possible_gave_up_groups.csv",
        gave_up_rows,
        list(group_rows[0].keys()) if group_rows else [],
    )
    write_csv(
        args.out_dir / "probably_complete_groups.csv",
        complete_rows,
        list(group_rows[0].keys()) if group_rows else [],
    )

    report_path = args.out_dir / "episode_coverage_report.md"
    with report_path.open("w", encoding="utf-8") as report:
        report.write("# Episode coverage exploration\n\n")
        report.write(f"Input: `{args.input}`\n\n")
        report.write(f"Rows: {rows_total:,}\n\n")
        report.write(f"AniList titles: {len(title_coverages):,}\n\n")
        report.write("## Parser methods\n\n")
        for method, count in parser_methods.most_common():
            report.write(f"- `{method}`: {count:,}\n")
        report.write("\n## Language hints\n\n")
        for hint, count in language_hints.most_common(20):
            report.write(f"- `{hint}`: {count:,}\n")
        report.write("\n## Top leading groups\n\n")
        for group, count in groups.most_common(20):
            report.write(f"- `{group}`: {count:,}\n")
        report.write("\n## Biggest merged title spans\n\n")
        report.write("| AniList | Title | Unique episodes | Biggest span | Rows |\n")
        report.write("| ---: | --- | ---: | --- | ---: |\n")
        for row in title_rows[:30]:
            title = row["english_name"] or row["name"] or row["japanese_name"]
            report.write(
                f"| {row['anilist_id']} | {title} | {row['unique_episodes']} | "
                f"{row['biggest_span']} ({row['biggest_span_size']}) | {row['rows']} |\n"
            )
        report.write("\n## Possible gave-up groups to smoke-test\n\n")
        report.write(
            "| AniList | Title | Group | Coverage | Group span | Title span | Examples |\n"
        )
        report.write("| ---: | --- | --- | ---: | --- | --- | --- |\n")
        for row in gave_up_rows[:20]:
            report.write(
                f"| {row['anilist_id']} | {row['name']} | {row['group']} | "
                f"{row['coverage_ratio']} | {row['group_spans']} | "
                f"{row['title_episode_min']}-{row['title_episode_max']} | "
                f"{row['examples']} |\n"
            )
        report.write("\n## Probably complete groups\n\n")
        report.write("| AniList | Title | Group | Coverage | Episodes | Span |\n")
        report.write("| ---: | --- | --- | ---: | ---: | --- |\n")
        for row in complete_rows[:20]:
            report.write(
                f"| {row['anilist_id']} | {row['name']} | {row['group']} | "
                f"{row['coverage_ratio']} | {row['unique_group_episodes']} | "
                f"{row['group_spans']} |\n"
            )
        report.write("\n## Output files\n\n")
        report.write("- `merged_title_spans.csv`\n")
        report.write("- `group_coverage.csv`\n")
        report.write("- `possible_gave_up_groups.csv`\n")
        report.write("- `probably_complete_groups.csv`\n")

    print(f"Wrote {report_path}")
    print(f"Wrote {args.out_dir / 'merged_title_spans.csv'}")
    print(f"Wrote {args.out_dir / 'group_coverage.csv'}")
    print(f"Wrote {args.out_dir / 'possible_gave_up_groups.csv'}")
    print(f"Wrote {args.out_dir / 'probably_complete_groups.csv'}")


if __name__ == "__main__":
    main()
