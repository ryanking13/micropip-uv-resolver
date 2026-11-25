"""Host tests for reading a wheel's METADATA with range requests (no Pyodide needed)."""

import asyncio
import io
import zipfile

import pytest

from micropip._compat import FetchResponse
from micropip_uv_resolver._wheel_metadata import TAIL_SIZE, read_wheel_metadata

METADATA = b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nRequires-Dist: six\n"


def make_wheel(padding: int = 0, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A wheel whose METADATA is stored before ``padding`` bytes of other files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=method) as zf:
        zf.writestr("demo-1.0.dist-info/METADATA", METADATA)
        # Incompressible, so that the central directory ends up far from METADATA
        zf.writestr("demo/data.bin", bytes(range(256)) * (padding // 256), zipfile.ZIP_STORED)
        zf.writestr("demo-1.0.dist-info/RECORD", b"")
    return buf.getvalue()


class Server:
    """Serves ``data`` at any URL, with or without range requests."""

    def __init__(self, data: bytes, ranges: bool = True) -> None:
        self.data = data
        self.ranges = ranges
        self.requests: list[str | None] = []
        self.sent = 0

    async def fetch(self, url: str, headers: dict[str, str]) -> FetchResponse:
        range_header = headers.get("Range")
        self.requests.append(range_header)
        if range_header and self.ranges:
            start, end = (int(x) for x in range_header.removeprefix("bytes=").split("-"))
            body = self.data[start : end + 1]
            self.sent += len(body)
            return FetchResponse(body, status=206)
        self.sent += len(self.data)
        return FetchResponse(self.data)


def read(server: Server, size: int | None):
    return asyncio.run(read_wheel_metadata(server.fetch, "https://example.com/demo.whl", size))


@pytest.mark.parametrize("method", [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED])
def test_small_wheel_one_range(method):
    wheel = make_wheel(method=method)
    server = Server(wheel)
    result = read(server, len(wheel))
    assert result.metadata == METADATA
    assert result.wheel is None
    assert server.requests == [f"bytes=0-{len(wheel) - 1}"]


def test_large_wheel_reads_only_ranges():
    wheel = make_wheel(padding=4 * TAIL_SIZE)
    server = Server(wheel)
    result = read(server, len(wheel))
    assert result.metadata == METADATA
    assert result.wheel is None
    # The tail, then the local header and the data of METADATA
    assert len(server.requests) == 3
    assert server.sent < TAIL_SIZE + 1024


def test_no_size_downloads_the_wheel():
    wheel = make_wheel(padding=4 * TAIL_SIZE)
    server = Server(wheel)
    result = read(server, None)
    assert result.metadata == METADATA
    assert result.wheel == wheel
    assert server.requests == [None]


def test_server_without_ranges_downloads_the_wheel():
    wheel = make_wheel(padding=4 * TAIL_SIZE)
    server = Server(wheel, ranges=False)
    result = read(server, len(wheel))
    assert result.metadata == METADATA
    assert result.wheel == wheel


def test_no_metadata_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/__init__.py", b"")
    wheel = buf.getvalue()
    with pytest.raises(ValueError, match="METADATA"):
        read(Server(wheel), len(wheel))
