"""
Fetch what uv is likely to ask for next, concurrently.

uv asks for project pages and wheel metadata one at a time, and almost all of
a resolution's time is spent waiting for them. While uv waits, this guesses
the next requests and starts them: for each project page, the metadata of the
wheel uv will most likely pick (the installed version, or the newest
compatible version allowed by the requirements seen so far); for each wheel's
metadata, the project pages of its dependencies. uv's own requests then find
the result fetched, or in flight.

A wrong guess only costs a fetch; uv still decides everything.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from micropip._utils import is_package_compatible, parse_version
from micropip.metadata import Metadata

from micropip._vendored.packaging.src.packaging.requirements import Requirement
from micropip._vendored.packaging.src.packaging.specifiers import SpecifierSet
from micropip._vendored.packaging.src.packaging.utils import (
    canonicalize_name,
    parse_wheel_filename,
)
from micropip._vendored.packaging.src.packaging.version import Version

# Fetches started only because of a guess. Guesses follow the newest versions,
# which can pull in packages uv never asks for, so stop at some point.
MAX_GUESSES = 200


class Lookahead:
    def __init__(
        self,
        get_page: Callable[[str], Awaitable[dict | None]],
        get_metadata: Callable[[str], Awaitable[bytes]],
        installed: Mapping[str, str],
        python_version: str,
    ) -> None:
        self._get_page = get_page
        self._get_metadata = get_metadata
        self._installed = installed
        self._python_version = Version(python_version)
        self._pages: dict[str, asyncio.Future] = {}
        self._metadata: dict[str, asyncio.Future] = {}
        self._specifiers: dict[str, SpecifierSet] = {}
        self._extras: dict[str, set[str]] = {}
        self._guesses = 0

    def start(self, requirements: list[str]) -> None:
        """Start fetching for the top-level requirements."""
        for requirement in requirements:
            self._want(Requirement(requirement))

    async def page(self, name: str) -> dict | None:
        """The project page of ``name``, as returned by ``get_page``."""
        return await self._page_task(canonicalize_name(name))

    async def metadata(self, wheel_url: str) -> bytes:
        """The metadata of the wheel at ``wheel_url``, as returned by ``get_metadata``."""
        return await self._metadata_task(wheel_url)

    def cancel(self) -> None:
        """Cancel the fetches that are still running."""
        for task in [*self._pages.values(), *self._metadata.values()]:
            task.cancel()

    # -- tasks -----------------------------------------------------------------

    def _page_task(self, name: str, guess: bool = False) -> asyncio.Future:
        task = self._pages.get(name)
        if task is None:
            if guess and not self._guess():
                return None  # type: ignore[return-value]
            task = self._pages[name] = _spawn(self._load_page(name))
        return task

    def _metadata_task(self, wheel_url: str, guess: bool = False) -> asyncio.Future:
        task = self._metadata.get(wheel_url)
        if task is None:
            if guess and not self._guess():
                return None  # type: ignore[return-value]
            task = self._metadata[wheel_url] = _spawn(self._load_metadata(wheel_url))
        return task

    def _guess(self) -> bool:
        if self._guesses >= MAX_GUESSES:
            return False
        self._guesses += 1
        return True

    async def _load_page(self, name: str) -> dict | None:
        page = await self._get_page(name)
        if page is not None:
            wheel_url = self._likely_wheel(name, page)
            if wheel_url is not None:
                self._metadata_task(wheel_url, guess=True)
        return page

    async def _load_metadata(self, wheel_url: str) -> bytes:
        data = await self._get_metadata(wheel_url)
        name = canonicalize_name(parse_wheel_filename(wheel_url.rsplit("/", 1)[-1])[0])
        try:
            requirements = Metadata(data).requires(self._extras.get(name, set()))
        except Exception:  # noqa: BLE001
            requirements = []
        for requirement in requirements:
            self._want(requirement, guess=True)
        return data

    # -- guesses ---------------------------------------------------------------

    def _want(self, requirement: Requirement, guess: bool = False) -> None:
        name = canonicalize_name(requirement.name)
        known = self._specifiers.get(name)
        self._specifiers[name] = (
            known & requirement.specifier if known is not None else requirement.specifier
        )
        self._extras.setdefault(name, set()).update(requirement.extras)
        self._page_task(name, guess=guess)

    def _likely_wheel(self, name: str, page: dict) -> str | None:
        """The URL of the wheel uv will most likely pick from ``page``."""
        candidates: list[tuple[Version, int, str]] = []
        for file in page["files"]:
            if file.get("yanked"):
                continue
            compatible, tag_index = is_package_compatible(file["filename"])
            if not compatible or tag_index is None:
                continue
            requires_python = file.get("requires-python")
            if requires_python and not _allows(requires_python, self._python_version):
                continue
            version = parse_version(file["filename"])
            # The most specific tag first, as uv picks the best wheel of a version
            candidates.append((version, -tag_index, file["url"]))
        if not candidates:
            return None

        installed = self._installed.get(name)
        if installed is not None:
            for version, _, url in sorted(candidates, reverse=True):
                if str(version) == installed:
                    return url

        specifier = self._specifiers.get(name, SpecifierSet())
        # Like uv, no pre-releases unless only pre-releases are allowed. (An
        # empty SpecifierSet would allow them.)
        allowed = [c for c in candidates if not c[0].is_prerelease and specifier.contains(c[0])]
        if not allowed:
            allowed = [c for c in candidates if specifier.contains(c[0], prereleases=True)]
        if not allowed:
            return None
        return max(allowed)[2]


def _allows(requires_python: str, python_version: Version) -> bool:
    try:
        return SpecifierSet(requires_python).contains(python_version, prereleases=True)
    except Exception:  # noqa: BLE001
        return True


def _spawn(coro: Any) -> asyncio.Future:
    task = asyncio.ensure_future(coro)
    # A guess nobody awaits may fail; don't report it as never retrieved.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return task
