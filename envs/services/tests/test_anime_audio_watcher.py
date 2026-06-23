from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from anime_audio_support import manifest, write_series
from ja_media_services.anime_audio.app import create_app
from ja_media_services.anime_audio.settings import AnimeAudioSettings
from ja_media_services.anime_audio.watcher import DebouncedPaths, ManifestEventHandler


class FakeObserver:
    def __init__(self) -> None:
        self.handler: ManifestEventHandler | None = None
        self.started = False
        self.stopped = False

    def schedule(
        self, event_handler: ManifestEventHandler, _: str, *, recursive: bool
    ) -> None:
        assert recursive
        self.handler = event_handler

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        assert timeout == 5


def test_debounce_coalesces_repeated_publication_events(tmp_path: Path) -> None:
    pending = DebouncedPaths(2.0)
    path = tmp_path / "anilist-1" / ".ja-media.json"

    pending.add(path, 10.0)
    pending.add(path, 11.0)

    assert pending.pop_due(12.5) == ()
    assert pending.pop_due(13.0) == (path,)
    assert pending.pop_due(99.0) == ()


def test_event_handler_accepts_only_immediate_child_manifests(tmp_path: Path) -> None:
    seen: list[Path] = []
    handler = ManifestEventHandler(tmp_path, seen.append)

    handler.dispatch(FileCreatedEvent(str(tmp_path / "series" / ".ja-media.json")))
    handler.dispatch(FileCreatedEvent(str(tmp_path / ".ja-media.json")))
    handler.dispatch(
        FileCreatedEvent(str(tmp_path / "series" / "nested" / ".ja-media.json"))
    )
    handler.dispatch(FileCreatedEvent(str(tmp_path / "series" / "episode.m4a")))

    assert seen == [tmp_path / "series" / ".ja-media.json"]


def test_move_notifies_deleted_and_created_manifest_locations(tmp_path: Path) -> None:
    seen: list[Path] = []
    handler = ManifestEventHandler(tmp_path, seen.append)
    source = tmp_path / "old" / ".ja-media.json"
    destination = tmp_path / "new" / ".ja-media.json"

    handler.dispatch(FileMovedEvent(str(source), str(destination)))

    assert seen == [source, destination]


def test_app_lifecycle_applies_debounced_observer_refresh(tmp_path: Path) -> None:
    library = tmp_path / "library"
    path = write_series(library)
    observer = FakeObserver()
    settings = AnimeAudioSettings(
        library_root=library,
        db_path=tmp_path / "index.sqlite",
        watcher_debounce_seconds=0.01,
        fallback_scan_interval_seconds=0,
    )

    with TestClient(
        create_app(settings, observer_factory=lambda: observer)
    ) as client:
        assert observer.started
        health = client.get("/healthz").json()
        assert health["watcher_running"]
        assert health["fallback_scan_running"]
        path.write_text(json.dumps(manifest(title="Observed")), encoding="utf-8")
        assert observer.handler is not None
        observer.handler.dispatch(FileModifiedEvent(str(path)))
        observer.handler.dispatch(FileModifiedEvent(str(path)))

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if client.get("/series/1").json()["title"] == "Observed":
                break
            time.sleep(0.01)
        assert client.get("/series/1").json()["title"] == "Observed"

    assert observer.stopped


def test_failed_observer_is_degraded_while_fallback_worker_stays_alive(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    write_series(library)
    settings = AnimeAudioSettings(
        library_root=library,
        db_path=tmp_path / "index.sqlite",
        fallback_scan_interval_seconds=60,
    )

    def fail_observer() -> FakeObserver:
        raise OSError("watching unavailable")

    with TestClient(
        create_app(settings, observer_factory=fail_observer)
    ) as client:
        health = client.get("/healthz").json()

    assert health["status"] == "degraded"
    assert not health["watcher_running"]
    assert health["fallback_scan_running"]
