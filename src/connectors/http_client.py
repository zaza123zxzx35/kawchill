from __future__ import annotations

import time
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_SSL_READY = False
USER_AGENT = "TeacherExamRadar/1.0 (personal local research; Windows)"
MAX_BYTES = 5_000_000


def configure_ssl() -> None:
    global _SSL_READY
    if _SSL_READY:
        return
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass
    _SSL_READY = True


def ascii_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/%")
    query = quote(unquote(parts.query), safe="=&?/%:+")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def get_bytes(url: str, timeout: int = 25, limit: int = 8_000_000) -> tuple[int, bytes, str]:
    configure_ssl()
    request = urllib.request.Request(ascii_url(url), headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(limit + 1)
            if len(payload) > limit:
                raise ValueError("ไฟล์ใหญ่เกินกำหนด")
            return getattr(response, "status", 200), payload, response.headers.get_content_type() or ""
    except urllib.error.HTTPError as exc:
        return exc.code, b"", ""


def get_text(url: str, timeout: int = 40) -> tuple[int, str]:
    """Fetch a public URL. Raises if the connection fails twice."""
    configure_ssl()
    last_error: Exception | None = None
    for attempt in range(2):
        request = urllib.request.Request(
            ascii_url(url),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "th,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                payload = response.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise ValueError("ไฟล์ตอบกลับใหญ่เกินกำหนด")
                charset = response.headers.get_content_charset() or "utf-8"
                text = payload.decode(charset, "replace")
                if status in {429, 503} and attempt == 0:
                    time.sleep(2)
                    continue
                return status, text
        except urllib.error.HTTPError as exc:
            body = exc.read(2000).decode("utf-8", "replace") if exc.fp else ""
            if exc.code in {429, 503} and attempt == 0:
                time.sleep(2)
                last_error = exc
                continue
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
                continue
            raise
    if last_error:
        raise last_error
    raise TimeoutError(url)
