from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.services.source_policy import TRACKING_QUERY_PARAMS


def canonicalize_url(raw_url: str) -> tuple[str | None, str | None, str | None]:
    """Return canonical URL + hostname + error code for source-policy consumers."""
    if not raw_url:
        return None, None, "missing_url"

    candidate = raw_url.strip()
    if not candidate:
        return None, None, "missing_url"

    parsed = urlparse(candidate)
    if not parsed.scheme and not parsed.netloc:
        parsed = urlparse(f"https://{candidate}")

    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    if not hostname:
        return None, None, "missing_hostname"

    filtered_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered_key = key.lower()
        if lowered_key.startswith("utm_") or lowered_key in TRACKING_QUERY_PARAMS:
            continue
        filtered_query.append((key, value))

    canonical_url = urlunparse(
        (
            "https",
            hostname,
            parsed.path,
            parsed.params,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )

    return canonical_url, hostname, None
