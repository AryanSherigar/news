"""Provider-specific ingestion adapters.

Each adapter returns raw payload records that can be mapped into the canonical
article/event JSONL schema in ``scripts/scrape_three_timelines.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


class ProviderError(RuntimeError):
    """Raised when a provider call fails."""


def _fetch_json(url: str, *, timeout: int = 25) -> dict[str, Any]:
    req = Request(url=url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:  # nosec B310
        charset = resp.headers.get_content_charset() or "utf-8"
        payload = resp.read().decode(charset, errors="replace")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Invalid JSON payload from provider: {url}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"Expected JSON object payload from provider: {url}")
    return parsed


def _to_yyyymmddhhmmss(date_str: str) -> str:
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def fetch_guardian(
    *,
    query: str,
    start_date: str,
    end_date: str,
    api_key: str,
    page_size: int = 50,
    max_pages: int = 2,
) -> list[dict[str, Any]]:
    """Fetch Guardian Content API records with full body metadata."""
    records: list[dict[str, Any]] = []
    encoded_query = quote_plus(query)

    for page in range(1, max_pages + 1):
        url = (
            "https://content.guardianapis.com/search"
            f"?q={encoded_query}"
            f"&from-date={start_date}&to-date={end_date}"
            f"&page-size={page_size}&page={page}"
            "&show-fields=headline,trailText,bodyText,byline,sectionName,wordcount"
            "&show-tags=keyword,contributor"
            f"&api-key={quote_plus(api_key)}"
        )
        payload = _fetch_json(url)
        response = payload.get("response", {})
        items = response.get("results", [])
        if not isinstance(items, list):
            break

        for item in items:
            if isinstance(item, dict):
                item["_provider"] = "guardian"
                item["_provider_query"] = query
                records.append(item)

        pages = int(response.get("pages") or page)
        if page >= pages:
            break

    return records


def fetch_gdelt(
    *,
    query: str,
    start_date: str,
    end_date: str,
    max_records: int = 75,
) -> list[dict[str, Any]]:
    """Fetch GDELT DOC API records as event/GKG proxies."""
    start_dt = _to_yyyymmddhhmmss(f"{start_date}T00:00:00Z")
    end_dt = _to_yyyymmddhhmmss(f"{end_date}T23:59:59Z")
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote_plus(query)}"
        "&mode=ArtList"
        f"&maxrecords={max_records}"
        f"&startdatetime={start_dt}&enddatetime={end_dt}"
        "&format=json"
    )
    payload = _fetch_json(url)
    articles = payload.get("articles", [])
    records: list[dict[str, Any]] = []
    if isinstance(articles, list):
        for item in articles:
            if isinstance(item, dict):
                item["_provider"] = "gdelt"
                item["_provider_query"] = query
                records.append(item)
    return records


def fetch_gnews(
    *,
    query: str,
    start_date: str,
    end_date: str,
    api_key: str,
    max_records: int = 50,
) -> list[dict[str, Any]]:
    """Fetch optional GNews enrichment records."""
    url = (
        "https://gnews.io/api/v4/search"
        f"?q={quote_plus(query)}"
        "&lang=en"
        f"&from={start_date}T00:00:00Z&to={end_date}T23:59:59Z"
        f"&max={max_records}"
        f"&apikey={quote_plus(api_key)}"
    )
    payload = _fetch_json(url)
    items = payload.get("articles", [])
    records: list[dict[str, Any]] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item["_provider"] = "gnews"
                item["_provider_query"] = query
                records.append(item)
    return records


def fetch_mediastack(
    *,
    query: str,
    start_date: str,
    end_date: str,
    api_key: str,
    max_records: int = 50,
) -> list[dict[str, Any]]:
    """Fetch optional Mediastack enrichment records."""
    url = (
        "http://api.mediastack.com/v1/news"
        f"?access_key={quote_plus(api_key)}"
        f"&keywords={quote_plus(query)}"
        f"&date={start_date},{end_date}"
        "&languages=en&sort=published_desc"
        f"&limit={max_records}"
    )
    payload = _fetch_json(url)
    items = payload.get("data", [])
    records: list[dict[str, Any]] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item["_provider"] = "mediastack"
                item["_provider_query"] = query
                records.append(item)
    return records
