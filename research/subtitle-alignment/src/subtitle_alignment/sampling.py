"""Seeded series draw with explicit Kitsunekko usability gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient

from subtitle_alignment.objects import cache_kitsunekko
from subtitle_alignment.silver import SilverPool
from subtitle_alignment.values import positive_episode_number


def qualify_series(
    root: Path,
    client: HttpKitsunekkoSubtitlesClient,
    pool: SilverPool,
    *,
    target_count: int,
    download_workers: int,
) -> tuple[
    tuple[int, ...],
    list[dict[str, object]],
    dict[int, list[dict[str, Any]]],
    list[dict[str, object]],
]:
    """Keep drawing until the requested number of usable series is reached."""

    accepted: list[int] = []
    draws: list[dict[str, object]] = []
    inventories: dict[int, list[dict[str, Any]]] = {}
    cached_candidates: list[dict[str, object]] = []
    for draw_index, (series_id, canonical_episodes) in enumerate(
        pool.series_episodes.items(), start=1
    ):
        if len(canonical_episodes) <= 1:
            draws.append(
                _draw(
                    draw_index,
                    series_id,
                    canonical_episodes,
                    decision="rejected",
                    reason="single_episode",
                )
            )
            _print_draw(draws[-1], len(accepted), target_count)
            continue

        files = [dict(item) for item in client.anilist_files(series_id).files]
        candidates = _mapped_candidates(series_id, canonical_episodes, files)
        inventory_episodes = {
            int(item["canonical_episode"]) for item in candidates
        }
        decision, missing = coverage_decision(
            canonical_episodes, inventory_episodes
        )
        if decision == "rejected":
            draws.append(
                _draw(
                    draw_index,
                    series_id,
                    canonical_episodes,
                    inventory_files=len(files),
                    mapped_episodes=len(inventory_episodes),
                    missing=missing,
                    decision=decision,
                    reason="inventory_coverage_below_75pct",
                )
            )
            _print_draw(draws[-1], len(accepted), target_count)
            continue

        cached = cache_kitsunekko(
            root, client, candidates, workers=download_workers
        )
        available_episodes = {
            int(item["canonical_episode"])
            for item in cached
            if item["fetch_status"] == "ok"
        }
        decision, missing = coverage_decision(
            canonical_episodes, available_episodes
        )
        reason = (
            "usable"
            if decision == "accepted"
            else "content_coverage_below_75pct"
        )
        draws.append(
            _draw(
                draw_index,
                series_id,
                canonical_episodes,
                inventory_files=len(files),
                mapped_episodes=len(available_episodes),
                missing=missing,
                candidate_failures=sum(
                    item["fetch_status"] != "ok" for item in cached
                ),
                decision=decision,
                reason=reason,
            )
        )
        if decision == "accepted":
            accepted.append(series_id)
            inventories[series_id] = files
            cached_candidates.extend(cached)
        _print_draw(draws[-1], len(accepted), target_count)
        if len(accepted) == target_count:
            return tuple(sorted(accepted)), draws, inventories, cached_candidates
    raise RuntimeError(
        f"only {len(accepted)} of {target_count} usable series found in Silver pool"
    )


def coverage_decision(
    canonical_episodes: tuple[int, ...], available_episodes: set[int]
) -> tuple[str, tuple[int, ...]]:
    """Accept multi-episode series with candidates for at least 75% of episodes."""

    missing = tuple(sorted(set(canonical_episodes) - available_episodes))
    if len(canonical_episodes) <= 1:
        return "rejected", missing
    missing_fraction = len(missing) / len(canonical_episodes)
    return ("accepted" if missing_fraction <= 0.25 else "rejected"), missing


def _mapped_candidates(
    series_id: int,
    canonical_episodes: tuple[int, ...],
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    episodes = set(canonical_episodes)
    candidates = []
    for item in files:
        episode = positive_episode_number(item.get("episode_local"))
        if episode is None or episode not in episodes:
            continue
        subtitle_id = str(item.get("subtitle_id") or "").strip()
        if not subtitle_id:
            raise RuntimeError(f"Kitsunekko row lacks subtitle_id: {item}")
        candidates.append(
            {"anilist_id": series_id, "canonical_episode": episode, **item}
        )
    return candidates


def _draw(
    draw_index: int,
    series_id: int,
    canonical_episodes: tuple[int, ...],
    *,
    inventory_files: int = 0,
    mapped_episodes: int = 0,
    missing: tuple[int, ...] | None = None,
    candidate_failures: int = 0,
    decision: str,
    reason: str,
) -> dict[str, object]:
    missing = canonical_episodes if missing is None else missing
    return {
        "draw_index": draw_index,
        "anilist_id": series_id,
        "canonical_episode_count": len(canonical_episodes),
        "inventory_file_count": inventory_files,
        "available_episode_count": mapped_episodes,
        "missing_episode_count": len(missing),
        "missing_fraction": len(missing) / len(canonical_episodes),
        "missing_episodes_json": json.dumps(missing),
        "candidate_fetch_failures": candidate_failures,
        "decision": decision,
        "reason": reason,
    }


def _print_draw(item: dict[str, object], accepted: int, target: int) -> None:
    print(
        f"draw={item['draw_index']} anilist={item['anilist_id']} "
        f"episodes={item['canonical_episode_count']} "
        f"missing={item['missing_episode_count']} "
        f"decision={item['decision']} reason={item['reason']} "
        f"accepted={accepted}/{target}"
    )
