"""Request-origin checks.

Browsers attach an `Origin` header to WebSocket handshakes and to
cross-site POST/PATCH/DELETE requests. If a page on some other website
tries to talk to Jarvis on localhost, its Origin won't match the Host
Jarvis is served from, and the request is refused.

Requests with no Origin (curl, tests, the Swagger UI's same-page calls in
some browsers) are allowed — they don't come from a third-party web page.
"""

from urllib.parse import urlparse

from app.config import get_settings


def origin_allowed(origin: str | None, host: str | None) -> bool:
    if not origin:
        return True
    if origin in get_settings().allowed_origins:
        return True
    parsed = urlparse(origin)
    return bool(host) and parsed.netloc.lower() == host.lower()
