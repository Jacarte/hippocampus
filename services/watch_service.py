from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.indexing_service import IndexingService


class WatchService:

    def __init__(
        self,
        indexing_service: "IndexingService",
        poll_interval: float = 2.0,
    ) -> None:
        self._indexing_service = indexing_service
        self._poll_interval = poll_interval
        self._watchers: dict[str, tuple[threading.Thread, threading.Event, bool]] = {}
        self._lock = threading.Lock()

    def start(self, root: str, generate_summaries: bool = False) -> None:
        """Start watching *root* for filesystem changes and resyncing periodically.

        Args:
            root: Absolute path to the directory tree to watch.
            generate_summaries: Forwarded to :meth:`IndexingService.sync` on
                every watch-triggered resync.  Defaults to ``False``.
        """
        with self._lock:
            if root in self._watchers:
                return
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._watch_loop,
                args=(root, stop_event, generate_summaries),
                daemon=True,
                name=f"watch-{root}",
            )
            self._watchers[root] = (thread, stop_event, generate_summaries)
            thread.start()

    def stop(self, root: str) -> None:
        with self._lock:
            entry = self._watchers.pop(root, None)
        if entry is None:
            return
        thread, stop_event, _generate_summaries = entry
        stop_event.set()
        thread.join(timeout=self._poll_interval + 2.0)

    def stop_all(self) -> None:
        with self._lock:
            roots = list(self._watchers.keys())
        for root in roots:
            self.stop(root)

    def is_watching(self, root: str) -> bool:
        with self._lock:
            return root in self._watchers

    def list_roots(self) -> list[str]:
        with self._lock:
            return list(self._watchers.keys())

    def _watch_loop(self, root: str, stop_event: threading.Event, generate_summaries: bool = False) -> None:
        while not stop_event.is_set():
            try:
                self._indexing_service.sync(root, generate_summaries=generate_summaries)
            except Exception as exc:
                logging.warning("Watch sync error for %s: %s", root, exc, exc_info=True)
            stop_event.wait(timeout=self._poll_interval)
