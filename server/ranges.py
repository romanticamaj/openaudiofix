"""File responses with HTTP Range support (needed for seeking in <audio>).

Starlette's FileResponse streams 206 single/multipart ranges and honours If-Range. In front of it the Range
header is normalised per RFC 9110 §14: a malformed header or a unit other than bytes is ignored (200, full
body); suffix ranges longer than the file mean the whole file; unsatisfiable ranges are dropped; when none
is left the answer is 416 with `Content-Range: bytes */<size>`. Downloads get an RFC 6266 / RFC 5987
Content-Disposition, so names with spaces or non-ASCII characters survive.
"""
import mimetypes
import os
import re
from urllib.parse import quote

from fastapi.responses import FileResponse, Response

TYPES = {".webm": "audio/webm", ".wav": "audio/wav", ".csv": "text/csv; charset=utf-8", ".json": "application/json"}
_SPEC = re.compile(r"^\s*(\d*)\s*-\s*(\d*)\s*$")


def content_disposition(name):
    """attachment; filename="<ASCII fallback>"; filename*=UTF-8''<percent-encoded UTF-8>."""
    fallback = re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def normalize_range(value, size):
    """-> None (ignore the header), "" (nothing satisfiable: 416) or a normalised "bytes=a-b[,c-d]" value."""
    units, sep, spec = value.partition("=")
    if not sep or units.strip().lower() != "bytes":
        return None
    out = []
    for part in spec.split(","):
        m = _SPEC.match(part)
        if not m or m.groups() == ("", ""):
            return None
        first, last = m.groups()
        if first:
            a = int(first)
            if last and int(last) < a:
                return None
            if a < size:
                out.append(f"{a}-{min(int(last), size - 1) if last else size - 1}")
        elif int(last) > 0 and size > 0:
            out.append(f"{max(0, size - int(last))}-{size - 1}")
    return "bytes=" + ",".join(out) if out else ""


class RangeFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        raw = [(k, v) for k, v in scope["headers"] if k != b"range"]
        values = [v for k, v in scope["headers"] if k == b"range"]
        if values:
            size = os.stat(self.path).st_size
            norm = normalize_range(values[0].decode("latin-1"), size)
            has_if_range = any(k == b"if-range" for k, _ in raw)
            if norm == "" and not has_if_range:
                await Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})(scope, receive, send)
                return
            if norm:
                raw.append((b"range", norm.encode("latin-1")))
            scope = dict(scope, headers=raw)
        await super().__call__(scope, receive, send)


def file_response(request, path, media_type=None, download_name=None):
    media_type = media_type or TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] \
        or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes"}
    if download_name:
        headers["Content-Disposition"] = content_disposition(download_name)
    return RangeFileResponse(path, media_type=media_type, headers=headers)
