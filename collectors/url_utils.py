"""
normalize_url — canonicalizes a URL before dedup/storage (ТЗ #4 И2.1).

Feeds attach tracking params (utm_*, fbclid) that make the same article show
up under different URLs across sources (e.g. Habr) or polls, defeating the
url-based dedup from И1. Strips those, the fragment, and a trailing slash.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid"}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)

    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _TRACKING_PARAMS and not key.startswith(_TRACKING_PARAM_PREFIXES)
    ]

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(kept_query), ""))
