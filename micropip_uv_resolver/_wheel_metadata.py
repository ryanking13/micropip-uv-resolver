"""
Read a wheel's METADATA without PEP 658 metadata on the index.

uv only resolves with wheel metadata. When the index doesn't serve it separately
(PEP 658), UvResolver reads it from the wheel: with HTTP range requests for the
end of the zip (its central directory) and the METADATA entry, so only a few
kilobytes are downloaded. If the size of the wheel isn't known or the server
doesn't answer range requests (status 206), the whole wheel is downloaded, and
kept so that it isn't downloaded again when it is installed.
"""

import io
import re
import struct
import zipfile
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from micropip._compat import FetchResponse

Fetch = Callable[[str, dict[str, str]], Awaitable[FetchResponse]]

# The end of the zip that is fetched first: the end of central directory record
# and, for most wheels, the whole central directory.
TAIL_SIZE = 64 * 1024

_METADATA = re.compile(r"[^/]+\.dist-info/METADATA")

_EOCD = struct.Struct("<4s4H2LH")  # end of central directory record
_CENTRAL = struct.Struct("<4s6H3L5H2L")  # central directory file header
_LOCAL = struct.Struct("<4s5H3L2H")  # local file header


@dataclass
class WheelMetadata:
    metadata: bytes
    """The contents of the wheel's ``.dist-info/METADATA``."""

    wheel: bytes | None = None
    """The whole wheel, if it had to be downloaded."""


class _Unsupported(Exception):
    """The zip can't be read with range requests; download the whole wheel."""


async def read_wheel_metadata(fetch: Fetch, url: str, size: int | None) -> WheelMetadata:
    """Read the METADATA of the wheel at ``url`` (``size`` bytes, if known)."""
    if size:
        try:
            return await _read_with_ranges(fetch, url, size)
        except Exception:  # noqa: BLE001
            # _Unsupported, or the server rejected the range (e.g. 416): fall
            # back to the whole wheel, which also reports real errors.
            pass
    response = await fetch(url, {})
    return WheelMetadata(_metadata_from_zip(response.content), wheel=response.content)


async def _fetch_range(fetch: Fetch, url: str, start: int, end: int) -> bytes:
    """Bytes ``start`` to ``end`` (inclusive) of ``url``."""
    # A closed range ("bytes=N-M") is a CORS-safelisted header; a suffix range
    # ("bytes=-N") would need a preflight request in browsers.
    response = await fetch(url, {"Range": f"bytes={start}-{end}"})
    if response.status != 206 or len(response.content) != end - start + 1:
        raise _Unsupported(f"no range response for {url}")
    return response.content


async def _read_with_ranges(fetch: Fetch, url: str, size: int) -> WheelMetadata:
    tail_start = max(0, size - TAIL_SIZE)
    tail = await _fetch_range(fetch, url, tail_start, size - 1)

    eocd_at = tail.rfind(b"PK\x05\x06")
    if eocd_at < 0 or len(tail) - eocd_at < _EOCD.size:
        raise _Unsupported("no end of central directory record")
    _, _, _, _, entries, cd_size, cd_offset, _ = _EOCD.unpack_from(tail, eocd_at)
    if entries == 0xFFFF or cd_offset == 0xFFFFFFFF:
        raise _Unsupported("zip64")

    if cd_offset < tail_start:
        central = await _fetch_range(fetch, url, cd_offset, cd_offset + cd_size - 1)
    else:
        central = tail[cd_offset - tail_start : cd_offset - tail_start + cd_size]

    entry = _find_metadata_entry(central)
    if entry is None:
        raise ValueError(f"{url} has no .dist-info/METADATA")
    method, compressed_size, name_length, local_offset = entry

    # The local header's extra field can differ from the central one: read the
    # header first, then the data.
    header_end = local_offset + _LOCAL.size + name_length
    if local_offset >= tail_start:
        local = tail[local_offset - tail_start :]
    else:
        local = await _fetch_range(fetch, url, local_offset, header_end - 1)
    (*_, local_name_length, local_extra_length) = _LOCAL.unpack_from(local)
    data_start = local_offset + _LOCAL.size + local_name_length + local_extra_length
    data_end = data_start + compressed_size
    if data_start >= tail_start:
        data = tail[data_start - tail_start : data_end - tail_start]
    else:
        data = await _fetch_range(fetch, url, data_start, data_end - 1)

    return WheelMetadata(_decompress(method, data))


def _find_metadata_entry(central: bytes) -> tuple[int, int, int, int] | None:
    """The METADATA entry of a central directory: (method, compressed size, name length, offset)."""
    pos = 0
    while pos + _CENTRAL.size <= len(central):
        fields = _CENTRAL.unpack_from(central, pos)
        if fields[0] != b"PK\x01\x02":
            raise _Unsupported("bad central directory")
        method, compressed_size = fields[4], fields[8]
        name_length, extra_length, comment_length = fields[10], fields[11], fields[12]
        local_offset = fields[16]
        start = pos + _CENTRAL.size
        name = central[start : start + name_length].decode("utf-8", "replace")
        if _METADATA.fullmatch(name):
            if compressed_size == 0xFFFFFFFF or local_offset == 0xFFFFFFFF:
                raise _Unsupported("zip64")
            return method, compressed_size, name_length, local_offset
        pos = start + name_length + extra_length + comment_length
    return None


def _decompress(method: int, data: bytes) -> bytes:
    if method == zipfile.ZIP_STORED:
        return data
    if method == zipfile.ZIP_DEFLATED:
        return zlib.decompress(data, -15)
    raise _Unsupported(f"compression method {method}")


def _metadata_from_zip(wheel: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(wheel)) as zf:
        for name in zf.namelist():
            if _METADATA.fullmatch(name):
                return zf.read(name)
    raise ValueError("the wheel has no .dist-info/METADATA")
