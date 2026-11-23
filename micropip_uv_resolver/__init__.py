"""
A micropip resolver backed by uv's resolver, compiled to Emscripten.

Experimental. See README.md.

Usage, in Pyodide::

    import micropip
    from micropip_uv_resolver import UvResolver, load_uv

    await load_uv("https://example.com/uv/uv-resolver.mjs")  # or set globalThis.uvResolver yourself
    micropip.default_client.resolver_class = UvResolver
    await micropip.install("fastapi")

How it fits together:

- uv runs as a separate Emscripten module (``globalThis.uvResolver``), built
  from the uv-emscripten PoC with ``uv/micropip.patch``.
- uv's index requests go to ``micropip://project/<name>/``. They are answered,
  through ``globalThis.__uvFetch``, with a PEP 691 page built from
  ``micropip.sources.ordered_sources()``, so index URLs, Source plugins and the
  lock file all work. Every other request (PEP 658 metadata) goes through the
  compatibility layer, so custom layers and credentials apply.
- Lock file packages are shown to uv as a virtual wheel tagged with the most
  specific Pyodide tag, whose METADATA is built from ``LockedPackage.requires``.
  micropip's "lock file first, then fall through" rule is expressed as uv
  preferences for the lock file versions.
- Installed packages (``ResolveRequest.installed``) are passed as preferences,
  and packages whose installed version is resolved are skipped.
- Requirements uv can't handle yet (direct URLs, ``emfs:``, ``file:``) and
  runtimes without the uv module or JSPI fall back to micropip's resolver.
"""

import json
import sys
import sysconfig
from typing import Any

from micropip.package import PackageMetadata
from micropip.resolution import Resolution
from micropip.resolver import Resolver
from micropip.sources import LockFileSource, ordered_sources
from micropip.wheelinfo import WheelInfo

# micropip vendors packaging; use the same copy so that types (Version) match
from micropip._vendored.packaging.src.packaging.requirements import Requirement
from micropip._vendored.packaging.src.packaging.utils import canonicalize_name
from micropip._vendored.packaging.src.packaging.version import Version

__all__ = ["UvResolver", "is_supported", "load_uv"]

PROJECT_PREFIX = "micropip://project"
LOCK_PREFIX = "micropip://lockfile/"


def _uv_module() -> Any | None:
    try:
        from js import globalThis
    except ImportError:  # not in Pyodide
        return None
    return getattr(globalThis, "uvResolver", None)


def is_supported() -> bool:
    """Whether the uv module is loaded and the runtime supports JSPI."""
    module = _uv_module()
    return module is not None and bool(module.isSupported())


async def load_uv(url: str) -> None:
    """
    Load the uv module (``uv-resolver.mjs``) from ``url`` and make it available
    to :class:`UvResolver` as ``globalThis.uvResolver``.

    In Node.js, ``url`` can be a file path.
    """
    from js import Function, globalThis

    # A dynamic import() through a JS function, since Python can't call import()
    importer = Function("url", "return import(url)")
    globalThis.uvResolver = await importer(url)


def _abi_version() -> str:
    return sysconfig.get_config_var("PYEMSCRIPTEN_PLATFORM_VERSION") or sysconfig.get_config_var(
        "PYODIDE_ABI_VERSION"
    )


def _python_platform() -> str:
    """uv's name for the Pyodide platform, e.g. ``wasm32-pyodide2025``."""
    return f"wasm32-pyodide{_abi_version().split('_')[0]}"


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _best_tag() -> str:
    """The most specific wheel tag of the runtime, e.g. ``cp313-cp313-pyemscripten_2025_0_wasm32``."""
    cp = f"cp{sys.version_info.major}{sys.version_info.minor}"
    return f"{cp}-{cp}-pyemscripten_{_abi_version()}_wasm32"


def _is_url_requirement(requirement: str) -> bool:
    return " @ " in requirement or requirement.endswith(".whl") or ":" in requirement


def _to_js(obj: Any) -> Any:
    from js import Object
    from pyodide.ffi import to_js

    return to_js(obj, dict_converter=Object.fromEntries)


class UvResolver(Resolver):
    """
    A :class:`micropip.resolver.Resolver` that resolves with uv.

    Set it as ``resolver_class`` of a :class:`micropip.Client` (or of
    ``micropip.default_client``) after loading the uv module (:func:`load_uv`).
    """

    async def resolve(self) -> Resolution:
        requirements = self.request.requirements
        if not is_supported() or any(_is_url_requirement(r) for r in requirements):
            return await super().resolve()

        from js import globalThis
        from pyodide.ffi import create_proxy

        handler = create_proxy(self._uv_fetch)
        # setattr: `globalThis.__uvFetch` would be name-mangled inside the class
        setattr(globalThis, "__uvFetch", handler)
        try:
            output = await globalThis.uvResolver.resolve(
                _to_js(
                    {
                        "requirements": list(requirements),
                        "pythonVersion": _python_version(),
                        "pythonPlatform": _python_platform(),
                        "indexUrl": PROJECT_PREFIX,
                        "preferences": self._installed_preferences() + self._lockfile_preferences(),
                    }
                )
            )
        finally:
            delattr(globalThis, "__uvFetch")
            handler.destroy()

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
        lockfile_packages: list[PackageMetadata] = []
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
            url = pkg.get("url") or ""
            if url.startswith(LOCK_PREFIX):
                lockfile_packages.append(PackageMetadata(name, version, "pyodide"))
            else:
                wheels.append(
                    WheelInfo.from_package_index(
                        name=name,
                        filename=pkg["filename"],
                        url=url,
                        version=Version(version),
                        sha256=pkg.get("sha256"),
                        size=None,
                    )
                )

        return Resolution(
            wheels=wheels,
            lockfile_packages=lockfile_packages,
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

    def _lock_first(self) -> bool:
        """Whether the lock file comes before any index in micropip's source order."""
        items = ordered_sources(self.request.index_urls)
        return bool(items) and isinstance(items[0], LockFileSource)

    def _lockfile_preferences(self) -> list[dict]:
        """Prefer lock file versions if the lock file comes first, like micropip does."""
        lockfile = self.compat_layer.lockfile
        if lockfile is None or not self._lock_first():
            return []
        return [{"name": locked.name, "version": locked.version} for locked in lockfile]

    # -- uv's requests -----------------------------------------------------------

    async def _uv_fetch(self, url: str, accept: str) -> Any:
        # Never raise: an exception thrown through uv's JSPI call leaves the
        # wasm instance unusable.
        try:
            if url.startswith(PROJECT_PREFIX + "/"):
                name = url[len(PROJECT_PREFIX) + 1 :].strip("/")
                page = await self._project_page(name)
                if page is None:
                    return _to_js({"status": 404, "body": b""})
                return _to_js({"status": 200, "body": json.dumps(page).encode()})

            if url.startswith(LOCK_PREFIX) and url.endswith(".metadata"):
                return _to_js({"status": 200, "body": self._locked_metadata(url)})

            response = await self.fetch(url, {"accept": accept})
            return _to_js({"status": response.status, "body": response.content})
        except Exception:
            return _to_js({"status": 404, "body": b""})

    def _locked_file(self, locked: Any) -> dict:
        # The most specific tag, so that uv picks this virtual wheel over an index
        # wheel of the same version.
        filename = f"{locked.name.replace('-', '_')}-{locked.version}-{_best_tag()}.whl"
        return {
            "filename": filename,
            "url": LOCK_PREFIX + filename,
            "hashes": {},
            "core-metadata": True,
        }

    async def _project_page(self, name: str) -> dict | None:
        """
        The project as PEP 691 JSON, for uv.

        The files come from the first source group (in micropip's order) that has
        the project. If the lock file has the project too, its version is added as
        a virtual wheel: preferred if the lock file comes first (see
        _lockfile_preferences), or only used if no index has the project otherwise.
        """
        lockfile = self.compat_layer.lockfile
        locked = lockfile.get(name) if lockfile is not None else None

        page = None
        for item in ordered_sources(self.request.index_urls):
            if isinstance(item, LockFileSource):
                continue
            for source in item:
                page = await source.get_project(name, self.fetch)
                if page is not None:
                    break
            if page is not None:
                break

        files: list[dict] = []
        versions: list[str] = []
        if page is not None:
            versions = list(page.versions)
            files = [
                {
                    "filename": f.filename,
                    "url": f.url,
                    "hashes": f.hashes,
                    "core-metadata": f.core_metadata or False,
                    "yanked": f.yanked,
                    "requires-python": f.requires_python,
                    "size": f.size,
                }
                for f in page.files
            ]
        if locked is not None and (self._lock_first() or page is None):
            files.append(self._locked_file(locked))
            versions.append(locked.version)

        if not files:
            return None
        return {"meta": {"api-version": "1.1"}, "name": name, "versions": versions, "files": files}

    def _locked_metadata(self, url: str) -> bytes:
        filename = url[len(LOCK_PREFIX) : -len(".metadata")]
        dist_name, version = filename.split("-")[:2]
        locked = self.compat_layer.lockfile.get(canonicalize_name(dist_name))
        lines = ["Metadata-Version: 2.1", f"Name: {locked.name}", f"Version: {version}"]
        lines += [f"Requires-Dist: {req}" for req in locked.requires]
        return ("\n".join(lines) + "\n").encode()
