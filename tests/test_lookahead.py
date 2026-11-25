"""Host tests for Lookahead's guesses (no Pyodide needed)."""

import asyncio

from micropip_uv_resolver._lookahead import Lookahead


def wheel(name: str, version: str, **extra) -> dict:
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    return {"filename": filename, "url": f"https://files.example/{filename}", **extra}


PAGES = {
    "app": [wheel("app", "1.0"), wheel("app", "2.0"), wheel("app", "3.0b1")],
    "dep": [wheel("dep", "1.0"), wheel("dep", "1.5"), wheel("dep", "2.0")],
    "old": [wheel("old", "1.0"), wheel("old", "2.0", **{"requires-python": "<3"})],
    "gone": [wheel("gone", "1.0"), wheel("gone", "2.0", yanked=True)],
}
METADATA = {
    "app": b"Name: app\nRequires-Dist: dep<2\nRequires-Dist: old\nRequires-Dist: gone\n",
}


class Index:
    def __init__(self) -> None:
        self.pages: list[str] = []
        self.metadata: list[str] = []

    async def get_page(self, name: str) -> dict | None:
        self.pages.append(name)
        files = PAGES.get(name)
        return {"name": name, "files": files} if files else None

    async def get_metadata(self, wheel_url: str) -> bytes:
        self.metadata.append(wheel_url.rsplit("/", 1)[-1])
        name = wheel_url.rsplit("/", 1)[-1].split("-")[0]
        return METADATA.get(name, f"Name: {name}\n".encode())


def run(requirements, installed=None) -> Index:
    index = Index()

    async def main():
        lookahead = Lookahead(index.get_page, index.get_metadata, installed or {}, "3.14.0")
        lookahead.start(requirements)
        # Let the guesses run to completion
        for _ in range(50):
            await asyncio.sleep(0)

    asyncio.run(main())
    return index


def test_follows_newest_allowed_versions():
    index = run(["app"])
    assert index.pages[0] == "app"
    assert sorted(index.pages) == ["app", "dep", "gone", "old"]
    assert sorted(index.metadata) == sorted(
        [
            "app-2.0-py3-none-any.whl",  # not the pre-release
            "dep-1.5-py3-none-any.whl",  # dep<2 from app's metadata
            "old-1.0-py3-none-any.whl",  # 2.0 requires Python < 3
            "gone-1.0-py3-none-any.whl",  # 2.0 is yanked
        ]
    )


def test_top_level_specifier_and_installed_version():
    index = run(["app==1.0"], installed={"dep": "1.0"})
    assert "app-1.0-py3-none-any.whl" in index.metadata
    assert "dep-1.0-py3-none-any.whl" in index.metadata


def test_uv_requests_reuse_the_fetches():
    index = Index()

    async def main():
        lookahead = Lookahead(index.get_page, index.get_metadata, {}, "3.14.0")
        lookahead.start(["app"])
        page = await lookahead.page("app")
        metadata = await lookahead.metadata(PAGES["app"][1]["url"])
        assert page["name"] == "app"
        assert b"dep<2" in metadata
        lookahead.cancel()

    asyncio.run(main())
    assert index.pages.count("app") == 1
    assert index.metadata.count("app-2.0-py3-none-any.whl") == 1
