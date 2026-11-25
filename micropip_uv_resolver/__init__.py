"""
A micropip resolver backed by uv's resolver, compiled to Emscripten.

Experimental. See README.md.

Usage, in Pyodide::

    import functools

    import micropip
    from micropip_uv_resolver import UvResolver, load_uv

    uv = await load_uv("https://example.com/uv/uv-resolver.mjs")
    micropip.default_client.resolver = functools.partial(UvResolver, uv=uv)
    await micropip.install("fastapi")

How it fits together:

- uv runs as a separate Emscripten module, built from ``uv/`` in this
  repository. It is loaded with :func:`load_uv` and passed to
  :class:`UvResolver` as its ``uv`` setting.
- uv's index requests go to ``micropip://project/<name>/``. They are answered,
  through ``globalThis.__uvFetch``, with a PEP 691 page built from
  ``micropip.sources.ordered_sources()``, so index URLs and Source plugins
  work. Every other request (PEP 658 metadata) goes through the compatibility
  layer, so custom layers and credentials apply.
- The lock file (``"PYODIDE"`` in the index URLs) is not used: packages come
  only from the package indexes. If the index URLs have no package index, it
  resolves with micropip's resolver.
- Installed packages (``ResolveRequest.installed``) are passed as preferences,
  and packages whose installed version is resolved are skipped.
- Requirements uv can't handle yet (direct URLs, ``emfs:``, ``file:``) and
  runtimes without the uv module or JSPI fall back to micropip's resolver.
"""

import json
import sys
import sysconfig
from typing import Any

from micropip._compat import CompatibilityLayer
from micropip.resolution import Resolution, ResolveRequest
from micropip.resolver import Resolver
from micropip.sources import LockFileSource, ordered_sources
from micropip.wheelinfo import WheelInfo

from ._lookahead import Lookahead
from ._wheel_metadata import read_wheel_metadata

# micropip vendors packaging; use the same copy so that types (Version) match
from micropip._vendored.packaging.src.packaging.requirements import Requirement
from micropip._vendored.packaging.src.packaging.utils import canonicalize_name
from micropip._vendored.packaging.src.packaging.version import Version

__all__ = ["UvResolver", "is_supported", "load_uv"]

PROJECT_PREFIX = "micropip://project"


def is_supported(uv: Any | None) -> bool:
    """Whether ``uv`` (from :func:`load_uv`) is usable: loaded, and the runtime supports JSPI."""
    return uv is not None and bool(uv.isSupported())


async def load_uv(url: str) -> Any:
    """
    Load the uv module (``uv-resolver.mjs``) from ``url`` and return it, to pass
    to :class:`UvResolver` as ``uv``. The wasm module is downloaded and
    instantiated here, so that the first resolution doesn't wait for it.

    In Node.js, ``url`` can be a file path.
    """
    from js import Function

    # A dynamic import() through a JS function, since Python can't call import()
    importer = Function("url", "return import(url)")
    uv = await importer(url)
    if uv.isSupported():
        # Compile and instantiate the wasm now, not in the first resolution
        await uv.init()
    return uv


def _abi_version() -> str:
    """The PyEmscripten platform version of the runtime, e.g. ``2026_0`` (Pyodide 314)."""
    return sysconfig.get_config_var("PYEMSCRIPTEN_PLATFORM_VERSION")


def _python_platform() -> str:
    """uv's name for the Pyodide platform, e.g. ``wasm32-pyodide2026``."""
    return f"wasm32-pyodide{_abi_version().split('_')[0]}"


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _is_url_requirement(requirement: str) -> bool:
    return " @ " in requirement or requirement.endswith(".whl") or ":" in requirement


def _to_js(obj: Any) -> Any:
    from js import Object
    from pyodide.ffi import to_js

    return to_js(obj, dict_converter=Object.fromEntries)


class UvResolver(Resolver):
    """
    A :class:`micropip.resolver.Resolver` that resolves with uv.

    Pass the uv module (from :func:`load_uv`) as ``uv``, through
    ``functools.partial`` since micropip creates a resolver for each request::

        client = micropip.Client(resolver=functools.partial(UvResolver, uv=uv))

    Without ``uv`` (or without JSPI), it resolves with micropip's resolver.
    """

    def __init__(
        self,
        compat_layer: CompatibilityLayer,
        request: ResolveRequest | None = None,
        *,
        uv: Any | None = None,
    ) -> None:
        super().__init__(compat_layer, request)
        self.uv = uv
        # Wheels whose index doesn't serve their metadata (PEP 658): URL -> size
        self._without_metadata: dict[str, int | None] = {}
        # Wheels downloaded to read their metadata, reused when installing
        self._downloaded: dict[str, bytes] = {}

    async def resolve(self) -> Resolution:
        requirements = self.request.requirements
        if (
            not is_supported(self.uv)
            or any(_is_url_requirement(r) for r in requirements)
            or not self._index_groups()
        ):
            return await super().resolve()

        from js import globalThis
        from pyodide.ffi import create_proxy

        installed = {} if self.request.reinstall else self._installed()
        self._lookahead = Lookahead(
            self._project_page,
            self._fetch_metadata,
            installed,
            ".".join(map(str, sys.version_info[:3])),
        )
        self._lookahead.start(list(requirements))

        handler = create_proxy(self._uv_fetch)
        # setattr: `globalThis.__uvFetch` would be name-mangled inside the class
        setattr(globalThis, "__uvFetch", handler)
        try:
            output = await self.uv.resolve(
                _to_js(
                    {
                        "requirements": list(requirements),
                        "pythonVersion": _python_version(),
                        "pythonPlatform": _python_platform(),
                        "indexUrl": PROJECT_PREFIX,
                        "preferences": self._installed_preferences(),
                    }
                )
            )
        finally:
            delattr(globalThis, "__uvFetch")
            handler.destroy()
            self._lookahead.cancel()

        result = output.to_py()
        if not result["ok"]:
            raise ValueError(result["error"])
        return self._to_resolution(result)

    # -- uv's result -> micropip ---------------------------------------------

    def _installed(self) -> Any:
        if self.request.installed is not None:
            return self.request.installed
        from micropip.package_manager import InstalledPackages

        return InstalledPackages()

    def _to_resolution(self, result: dict) -> Resolution:
        wheels: list[WheelInfo] = []
        included: set[str] = set()
        installed = self._installed()

        for pkg in result["packages"]:
            name, version = pkg["name"], pkg["version"]
            installed_version = installed.get(canonicalize_name(name))
            if installed_version == version:
                continue  # Already installed
            if installed_version is not None and not self.request.reinstall:
                raise ValueError(
                    f"{name}=={installed_version} is already installed, but the "
                    f"resolution needs {name}=={version}. Use reinstall=True to replace it."
                )

            included.add(canonicalize_name(name))
            wheel = WheelInfo.from_package_index(
                name=name,
                filename=pkg["filename"],
                url=pkg["url"],
                version=Version(version),
                sha256=pkg.get("sha256"),
                size=None,
            )
            if (data := self._downloaded.get(wheel.url)) is not None:
                wheel.set_data(data)
            wheels.append(wheel)

        return Resolution(
            wheels=wheels,
            reinstall=self.request.reinstall,
            fetch_options=self.request.fetch_options,
            requested_by=self._requested_by(result, included),
        )

    def _requested_by(self, result: dict, included: set[str]) -> dict[str, list[str]]:
        deps = {
            canonicalize_name(p["name"]): [canonicalize_name(d) for d in p["dependencies"]]
            for p in result["packages"]
        }
        requested_by: dict[str, list[str]] = {}
        for requirement in self.request.requirements:
            root = canonicalize_name(Requirement(requirement).name)
            stack, seen = [root], set()
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                if name in included:
                    requested_by.setdefault(name, []).append(requirement)
                stack.extend(deps.get(name, []))
        return requested_by

    # -- preferences -----------------------------------------------------------

    def _installed_preferences(self) -> list[dict]:
        """Prefer keeping installed versions (uv doesn't see the environment)."""
        if self.request.reinstall:
            return []
        return [{"name": n, "version": v} for n, v in self._installed().items()]

    # -- uv's requests -----------------------------------------------------------

    async def _uv_fetch(self, url: str, accept: str) -> Any:
        # Never raise: report failures as a failed fetch, like fetch.js does
        # for JS exceptions, so that uv gets a normal result.
        try:
            if url.startswith(PROJECT_PREFIX + "/"):
                name = url[len(PROJECT_PREFIX) + 1 :].strip("/")
                page = await self._lookahead.page(name)
                if page is None:
                    return _to_js({"status": 404, "body": b""})
                return _to_js({"status": 200, "body": json.dumps(page).encode()})

            if url.endswith(".metadata"):
                metadata = await self._lookahead.metadata(url.removesuffix(".metadata"))
                return _to_js({"status": 200, "body": metadata})

            response = await self.fetch(url, {"accept": accept})
            return _to_js({"status": response.status, "body": response.content})
        except Exception:
            return _to_js({"status": 404, "body": b""})

    def _serves_metadata(self, file: Any) -> bool:
        """Whether UvResolver serves the metadata of ``file``, for an index that doesn't (PEP 658)."""
        if not file.filename.endswith(".whl"):
            return False
        self._without_metadata[file.url] = file.size
        return True

    async def _fetch_metadata(self, wheel_url: str) -> bytes:
        """The metadata of the wheel at ``wheel_url``: from the index (PEP 658), or read from the wheel."""
        if wheel_url in self._without_metadata:
            return await self._wheel_metadata(wheel_url)
        response = await self.fetch(wheel_url + ".metadata", {})
        return response.content

    async def _wheel_metadata(self, url: str) -> bytes:
        """The METADATA of the wheel at ``url``, read with range requests when possible."""
        result = await read_wheel_metadata(self.fetch, url, self._without_metadata[url])
        if result.wheel is not None:
            self._downloaded[url] = result.wheel
        return result.metadata

    def _index_groups(self) -> list:
        """The source groups of the request, in micropip's order, without the lock file."""
        return [
            item
            for item in ordered_sources(self.request.index_urls)
            if not isinstance(item, LockFileSource)
        ]

    async def _project_page(self, name: str) -> dict | None:
        """
        The project as PEP 691 JSON, for uv: the files of the first source group
        (in micropip's order) that has the project. The lock file is skipped.
        """
        for group in self._index_groups():
            for source in group:
                page = await source.get_project(name, self.fetch)
                if page is not None:
                    break
            else:
                continue
            break
        else:
            return None

        files = [
            {
                "filename": f.filename,
                "url": f.url,
                "hashes": f.hashes,
                # uv only uses wheels with metadata. For wheels whose index
                # doesn't serve it, UvResolver reads it from the wheel.
                "core-metadata": f.core_metadata or self._serves_metadata(f),
                "yanked": f.yanked,
                "requires-python": f.requires_python,
                "size": f.size,
            }
            for f in page.files
        ]
        return {
            "meta": {"api-version": "1.1"},
            "name": name,
            "versions": list(page.versions),
            "files": files,
        }
