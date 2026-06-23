"""Filesystem event coalescing and periodic repair for the anime-audio index."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ja_media_services.anime_audio.db import set_metadata
from ja_media_services.anime_audio.index import incremental_scan, refresh_manifest

logger = logging.getLogger(__name__)


class ObserverLike(Protocol):
    def schedule(
        self, event_handler: FileSystemEventHandler, path: str, *, recursive: bool
    ) -> object: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class DebouncedPaths:
    """Pure coalescing state shared by the worker and focused tests."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self._paths: dict[Path, float] = {}

    def add(self, path: Path, now: float) -> None:
        self._paths[path] = now + self.delay_seconds

    def pop_due(self, now: float) -> tuple[Path, ...]:
        due = tuple(sorted(path for path, deadline in self._paths.items() if deadline <= now))
        for path in due:
            del self._paths[path]
        return due

    def next_deadline(self) -> float | None:
        return min(self._paths.values(), default=None)


class ManifestEventHandler(FileSystemEventHandler):
    """Translate watchdog events into immediate-child manifest refreshes."""

    def __init__(self, root: Path, notify: Callable[[Path], None]) -> None:
        self.root = root.resolve()
        self.notify = notify

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._notify_if_manifest(Path(event.src_path))
        destination = getattr(event, "dest_path", None)
        if destination:
            self._notify_if_manifest(Path(destination))

    def _notify_if_manifest(self, path: Path) -> None:
        try:
            relative = path.resolve().relative_to(self.root)
        except ValueError:
            return
        if len(relative.parts) == 2 and relative.name == ".ja-media.json":
            self.notify(self.root / relative)


class IndexWatcher:
    """Own the observer and one worker for debounced refreshes and repair scans."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: threading.RLock,
        library_root: Path,
        *,
        watcher_enabled: bool,
        debounce_seconds: float,
        fallback_interval_seconds: float,
        observer_factory: Callable[[], ObserverLike] = Observer,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connection = connection
        self.lock = lock
        self.root = library_root.resolve()
        self.watcher_enabled = watcher_enabled
        self.fallback_interval = fallback_interval_seconds
        self.observer_factory = observer_factory
        self.clock = clock
        self.pending = DebouncedPaths(debounce_seconds)
        self.condition = threading.Condition()
        self.stopping = False
        self.observer: ObserverLike | None = None
        self.worker: threading.Thread | None = None

    def start(self) -> None:
        with self.lock, self.connection:
            set_metadata(
                self.connection, "watcher_enabled", "1" if self.watcher_enabled else "0"
            )
            set_metadata(self.connection, "watcher_running", "0")
            set_metadata(self.connection, "fallback_scan_running", "0")
        if self.watcher_enabled:
            candidate: ObserverLike | None = None
            try:
                candidate = self.observer_factory()
                candidate.schedule(
                    ManifestEventHandler(self.root, self.notify),
                    str(self.root),
                    recursive=True,
                )
                candidate.start()
                self.observer = candidate
            except Exception:
                logger.exception("Anime-audio filesystem observer could not start")
                if candidate is not None:
                    try:
                        candidate.stop()
                    except Exception:
                        pass
        if self.watcher_enabled or self.fallback_interval > 0:
            self.worker = threading.Thread(
                target=self._run, name="anime-audio-index-watcher", daemon=True
            )
            self.worker.start()
        with self.lock, self.connection:
            set_metadata(
                self.connection,
                "watcher_running",
                "1" if self.observer is not None else "0",
            )
            set_metadata(
                self.connection,
                "fallback_scan_running",
                "1" if self.worker is not None else "0",
            )

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
        if self.worker:
            self.worker.join(timeout=5)
        with self.lock, self.connection:
            set_metadata(self.connection, "watcher_running", "0")
            set_metadata(self.connection, "fallback_scan_running", "0")

    def notify(self, path: Path) -> None:
        with self.condition:
            self.pending.add(path, self.clock())
            self.condition.notify_all()

    def _run(self) -> None:
        next_scan = (
            self.clock() + self.fallback_interval
            if self.fallback_interval > 0
            else None
        )
        while True:
            with self.condition:
                now = self.clock()
                if self.stopping:
                    return
                paths = self.pending.pop_due(now)
                scan_due = next_scan is not None and next_scan <= now
                if not paths and not scan_due:
                    deadlines = [
                        item
                        for item in (self.pending.next_deadline(), next_scan)
                        if item is not None
                    ]
                    timeout = max(0.0, min(deadlines) - now) if deadlines else None
                    self.condition.wait(timeout)
                    continue
            self._refresh(paths)
            if scan_due:
                self._scan()
                next_scan = self.clock() + self.fallback_interval

    def _refresh(self, paths: tuple[Path, ...]) -> None:
        for path in paths:
            try:
                with self.lock:
                    refresh_manifest(self.connection, self.root, path)
            except Exception:
                logger.exception("Unexpected anime-audio manifest refresh failure")

    def _scan(self) -> None:
        try:
            with self.lock:
                incremental_scan(self.connection, self.root)
        except Exception:
            logger.exception("Anime-audio fallback scan failed")
