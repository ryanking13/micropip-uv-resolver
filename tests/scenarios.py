# ruff: noqa: F704  (top-level await: run with pyodide.runPythonAsync)
# Scenarios comparing UvResolver with micropip's default resolver. Run with `node tests/run.mjs`.
# UV_MODULE (path to uv-resolver.mjs) is set by run.mjs.
import time

import micropip
from micropip.resolver import Resolver
from micropip_uv_resolver import UvResolver, is_supported, load_uv

await load_uv(UV_MODULE)  # noqa: F821
assert is_supported(), "the uv module didn't load, or JSPI is unavailable"


class UvClient(micropip.Client):
    resolver_class = UvResolver


class DefaultClient(micropip.Client):
    resolver_class = Resolver


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


# 1. Pure Python packages from PyPI; their dependencies partly come from the lock file
uv, mp = await resolve_both("PyPI", ["flask", "httpx"])
assert names(uv) == names(mp), (names(uv), names(mp))
assert uv.requested_by["werkzeug"] == ["flask"]

# 2. Lock file packages (the lock file comes first with the default index)
uv, mp = await resolve_both("lock file first", ["regex", "attrs"])
assert {p.name for p in uv.lockfile_packages} >= {"regex", "attrs"}
assert not uv.wheels

# 3. Only the lock file
uv, mp = await resolve_both("index_urls=['PYODIDE']", ["numpy"], index_urls=["PYODIDE"])
assert summary(uv) == summary(mp)
assert [p.name for p in uv.lockfile_packages] == ["numpy"] and not uv.wheels

# 4. fastapi 0.110.0 pins starlette<0.37, the lock file has a newer starlette.
#    uv backtracks to a consistent set; micropip's resolver keeps the lock file
#    version with a warning.
uv, mp = await resolve_both("backtracking", ["fastapi==0.110.0", "starlette>=0.36"])
uv_starlette = [w.version for w in uv.wheels if w.name == "starlette"] + [
    p.version for p in uv.lockfile_packages if p.name == "starlette"
]
assert len(uv_starlette) == 1 and str(uv_starlette[0]).startswith("0.36"), uv_starlette
mp_starlette = [w for w in mp.wheels if w.name == "starlette"] + [
    p for p in mp.lockfile_packages if p.name == "starlette"
]
assert len(mp_starlette) == 1, mp_starlette

# 5. Install what uv resolved, and use it
print("\n=== install with UvResolver")
client = UvClient()
resolution = await client.resolve(["requests", "regex"])
results = [r async for r in client.install_resolution(resolution)]
for r in results:
    print(f"  {r.name}=={r.version} from {r.source}: {'ok' if r.ok else r.error}")
assert all(r.ok for r in results)
import requests  # noqa: E402
import regex  # noqa: E402,F401

print("  imported requests", requests.__version__)

# 6. Installed packages (ResolveRequest.installed)
print("\n=== installed packages")
resolution = await client.resolve(["requests"])
assert summary(resolution) == [], summary(resolution)
print("  requests again -> nothing to install")
try:
    await client.resolve(["urllib3<2"])
except ValueError as e:
    assert "already installed" in str(e)
    print("  urllib3<2 ->", str(e)[:120])
else:
    raise AssertionError("expected 'already installed'")
resolution = await client.resolve(["urllib3<2"], reinstall=True)
assert [w.version.major for w in resolution.wheels if w.name == "urllib3"] == [1]
print("  urllib3<2, reinstall=True ->", summary(resolution))

# 7. Fallback to micropip's resolver when the uv module isn't available
print("\n=== fallback")
from js import globalThis  # noqa: E402

uv_module = globalThis.uvResolver
del globalThis.uvResolver
try:
    assert not is_supported()
    resolution = await UvClient().resolve(["flask"])
    assert "flask" in names(resolution)
    print("  without the uv module -> micropip's resolver:", ", ".join(summary(resolution)))
finally:
    globalThis.uvResolver = uv_module

print("\nAll scenarios passed")
