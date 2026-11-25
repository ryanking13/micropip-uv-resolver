"""
Project pages and wheel metadata, kept across resolutions.

Each resolution creates a new UvResolver, so without this every
``micropip.install`` fetches everything again. Wheel metadata doesn't change
for a given URL, so it is kept until evicted. Project pages change when new
versions are released, so they are kept for ``page_ttl`` seconds.
"""

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

_MISSING = object()


class Cache:
    """
    Project pages and wheel metadata for :class:`~micropip_uv_resolver.UvResolver`,
    shared across resolutions. Pass one with ``UvResolver(..., cache=...)``, or
    ``cache=None`` to fetch everything for each resolution.

    Entries are keyed by URL (metadata) and by package index and project name
    (pages), not by compatibility layer or fetch options: use separate caches
    for indexes that serve different content to different clients.
    """

    def __init__(
        self,
        page_ttl: float = 300.0,
        max_pages: int = 256,
        max_metadata: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.page_ttl = page_ttl
        self.max_pages = max_pages
        self.max_metadata = max_metadata
        self._clock = clock
        self._pages: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._metadata: OrderedDict[str, bytes] = OrderedDict()

    def get_page(self, key: Hashable) -> Any:
        """The page stored for ``key``, or ``_MISSING`` if there is none or it expired."""
        entry = self._pages.get(key)
        if entry is None:
            return _MISSING
        stored_at, page = entry
        if self._clock() - stored_at > self.page_ttl:
            del self._pages[key]
            return _MISSING
        self._pages.move_to_end(key)
        return page

    def put_page(self, key: Hashable, page: Any) -> None:
        self._pages[key] = (self._clock(), page)
        self._pages.move_to_end(key)
        while len(self._pages) > self.max_pages:
            self._pages.popitem(last=False)

    def get_metadata(self, url: str) -> bytes | None:
        data = self._metadata.get(url)
        if data is not None:
            self._metadata.move_to_end(url)
        return data

    def put_metadata(self, url: str, data: bytes) -> None:
        self._metadata[url] = data
        self._metadata.move_to_end(url)
        while len(self._metadata) > self.max_metadata:
            self._metadata.popitem(last=False)

    def clear(self) -> None:
        """Forget everything."""
        self._pages.clear()
        self._metadata.clear()


MISSING = _MISSING
