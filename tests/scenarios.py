# ruff: noqa: F704  (top-level await: run with pyodide.runPythonAsync)
# Scenarios comparing UvResolver with micropip's default resolver. Run with `node tests/run.mjs`.
# UV_MODULE (path to uv-resolver.mjs) is set by run.mjs.
#
# UvResolver only uses package indexes (not the lock file), so the comparisons
# resolve from PyPI only (index_urls=["PYPI"]) with both resolvers.
import functools
import time

import micropip
from micropip_uv_resolver import UvResolver, is_supported, load_uv

uv_module = await load_uv(UV_MODULE)  # noqa: F821
assert is_supported(uv_module), "the uv module didn't load, or JSPI is unavailable"

PYPI = {"index_urls": ["PYPI"]}


def UvClient():
    return micropip.Client(resolver=functools.partial(UvResolver, uv=uv_module))


DefaultClient = micropip.Client


def summary(resolution):
    wheels = sorted(f"{w.name}=={w.version}" for w in resolution.wheels)
    locked = sorted(f"{p.name}=={p.version} (lock file)" for p in resolution.lockfile_packages)
    return wheels + locked


def names(resolution):
    return {w.name for w in resolution.wheels} | {p.name for p in resolution.lockfile_packages}


async def resolve_both(title, requirements, **kwargs):
    print(f"\n=== {title}: {requirements} {kwargs or ''}")
    results = {}
    for label, client in (("uv", UvClient()), ("micropip", DefaultClient())):
        start = time.perf_counter()
        resolution = await client.resolve(requirements, **kwargs)
        print(f"[{label}] {time.perf_counter() - start:.2f}s: {', '.join(summary(resolution))}")
        results[label] = resolution
    return results["uv"], results["micropip"]


# 1. Pure Python packages, and a pyemscripten wheel on PyPI (charset-normalizer)
uv, mp = await resolve_both("PyPI", ["requests", "httpx"], **PYPI)
assert names(uv) == names(mp), (names(uv), names(mp))
assert not uv.lockfile_packages
assert any("pyemscripten" in w.filename for w in uv.wheels if w.name == "charset-normalizer")
assert uv.requested_by["urllib3"] == ["requests"]

# 2. A binary dependency with a pyemscripten wheel on PyPI (pydantic-core)
uv, mp = await resolve_both("binary wheel on PyPI", ["pydantic"], **PYPI)
assert names(uv) == names(mp), (names(uv), names(mp))
assert any("pyemscripten" in w.filename for w in uv.wheels if w.name == "pydantic-core")

# 3. fastapi 0.110.0 pins starlette<0.37. uv backtracks to a consistent set;
#    micropip's resolver keeps the newest starlette with a warning.
uv, mp = await resolve_both("backtracking", ["fastapi==0.110.0", "starlette>=0.36"], **PYPI)
uv_starlette = [w.version for w in uv.wheels if w.name == "starlette"]
assert len(uv_starlette) == 1 and str(uv_starlette[0]).startswith("0.36"), uv_starlette
assert len([w for w in mp.wheels if w.name == "starlette"]) == 1

# 4. No usable wheel on the index (markupsafe only has native wheels and an sdist)
print("\n=== no usable wheel: ['markupsafe']")
try:
    await UvClient().resolve(["markupsafe"], **PYPI)
except ValueError as e:
    assert "no usable wheels" in str(e), str(e)
    print("  [uv]", str(e).splitlines()[0][:120])
else:
    raise AssertionError("expected 'no usable wheels'")

# 5. The lock file is not used: the default index URLs resolve from PyPI only,
#    and index URLs without a package index fall back to micropip's resolver.
print("\n=== the lock file is not used")
resolution = await UvClient().resolve(["httpx"])
assert names(resolution) and not resolution.lockfile_packages
print("  default index URLs ->", ", ".join(summary(resolution)))
resolution = await UvClient().resolve(["numpy"], index_urls=["PYODIDE"])
assert [p.name for p in resolution.lockfile_packages] == ["numpy"]
print("  index_urls=['PYODIDE'] -> micropip's resolver:", ", ".join(summary(resolution)))

# 6. Install what uv resolved, and use it
print("\n=== install with UvResolver")
client = UvClient()
resolution = await client.resolve(["requests"], **PYPI)
results = [r async for r in client.install_resolution(resolution)]
for r in results:
    print(f"  {r.name}=={r.version} from {r.source}: {'ok' if r.ok else r.error}")
assert all(r.ok for r in results)
import requests  # noqa: E402

print("  imported requests", requests.__version__)

# 7. Installed packages (ResolveRequest.installed)
print("\n=== installed packages")
resolution = await client.resolve(["requests"], **PYPI)
assert summary(resolution) == [], summary(resolution)
print("  requests again -> nothing to install")
try:
    await client.resolve(["urllib3<2"], **PYPI)
except ValueError as e:
    assert "already installed" in str(e)
    print("  urllib3<2 ->", str(e)[:120])
else:
    raise AssertionError("expected 'already installed'")
resolution = await client.resolve(["urllib3<2"], reinstall=True, **PYPI)
assert [w.version.major for w in resolution.wheels if w.name == "urllib3"] == [1]
print("  urllib3<2, reinstall=True ->", summary(resolution))

# 8. Fallback to micropip's resolver when the uv module isn't available
print("\n=== fallback")
assert not is_supported(None)
resolution = await micropip.Client(resolver=UvResolver).resolve(["httpx"], **PYPI)
assert "httpx" in names(resolution)
print("  without the uv module -> micropip's resolver:", ", ".join(summary(resolution)))

print("\nAll scenarios passed")
