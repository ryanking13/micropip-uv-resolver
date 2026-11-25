"""Host tests for the cache shared across resolutions (no Pyodide needed)."""

from micropip_uv_resolver._cache import MISSING, Cache


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_pages_expire():
    clock = Clock()
    cache = Cache(page_ttl=300, clock=clock)
    cache.put_page("a", {"name": "a"})
    clock.now = 299
    assert cache.get_page("a") == {"name": "a"}
    clock.now = 301
    assert cache.get_page("a") is MISSING


def test_a_missing_project_is_cached():
    cache = Cache()
    assert cache.get_page("gone") is MISSING
    cache.put_page("gone", None)
    assert cache.get_page("gone") is None


def test_least_recently_used_entries_are_evicted():
    cache = Cache(max_pages=2, max_metadata=2)
    cache.put_page("a", 1)
    cache.put_page("b", 2)
    cache.get_page("a")
    cache.put_page("c", 3)
    assert cache.get_page("b") is MISSING
    assert cache.get_page("a") == 1

    cache.put_metadata("x", b"x")
    cache.put_metadata("y", b"y")
    cache.get_metadata("x")
    cache.put_metadata("z", b"z")
    assert cache.get_metadata("y") is None
    assert cache.get_metadata("x") == b"x"


def test_metadata_does_not_expire():
    clock = Clock()
    cache = Cache(page_ttl=1, clock=clock)
    cache.put_metadata("url", b"Name: a\n")
    clock.now = 10**9
    assert cache.get_metadata("url") == b"Name: a\n"


def test_clear():
    cache = Cache()
    cache.put_page("a", 1)
    cache.put_metadata("x", b"x")
    cache.clear()
    assert cache.get_page("a") is MISSING
    assert cache.get_metadata("x") is None
