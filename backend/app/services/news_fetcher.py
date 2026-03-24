import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp

from app.schemas import NewsItem


logger = logging.getLogger(__name__)

ALLOWED_SOURCE_DOMAINS = {
    "economictimes.indiatimes.com",
    "timesofindia.indiatimes.com",
}

SOURCE_ALIASES = {
    "economictimes.indiatimes.com": "ET",
    "timesofindia.indiatimes.com": "TOI",
}

TRACKING_QUERY_PARAMS = {
    "gclid",
    "dclid",
    "fbclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "spm",
    "yclid",
}

NO_NEWS_CONTEXT = [
    NewsItem(
        title="No live news retrieved",
        link="",
        source="System",
        published_at="",
    )
]


async def fetch_news_context(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Fetch structured news context for a given topic using the rss2json proxy.

    Returns a dict with serialized prompt context, structured items for API consumers,
    and the timestamp when the fetch completed.
    """
    fetched_at = datetime.now(timezone.utc)

    try:
        encoded_query = query.replace(" ", "+")
        url = f"https://www.rss2json.com/api.json?rss_url=https://news.google.com/rss/search?q={encoded_query}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return _fallback_context(fetched_at)

                data = await response.json()
                items = data.get("items", [])[:max_results]
                if not items:
                    return _fallback_context(fetched_at)

                news_items = []
                for item in items:
                    news_item = _to_news_item(item)
                    if news_item is not None:
                        news_items.append(news_item)

                if not news_items:
                    logger.warning("All fetched items were rejected by URL validation", extra={"query": query})
                    return _fallback_context(fetched_at)

                return {
                    "prompt_context": json.dumps([item.model_dump() for item in news_items], ensure_ascii=False, indent=2),
                    "items": [item.model_dump() for item in news_items],
                    "fetched_at": fetched_at.isoformat(),
                }

    except Exception as e:
        print(f"Error fetching news: {e}")
        return _fallback_context(fetched_at)


def _to_news_item(item: dict[str, Any]) -> NewsItem | None:
    original_link = item.get("link", "")
    normalized_link, hostname, reject_reason = _normalize_and_validate_url(original_link)
    if reject_reason:
        logger.info(
            "Rejected news item URL",
            extra={
                "reason": reject_reason,
                "domain": hostname or "",
                "url": original_link,
            },
        )
        return None

    source_info = item.get("source") or {}
    source_name = source_info.get("title") if isinstance(source_info, dict) else ""
    canonical_source_name = SOURCE_ALIASES.get(hostname or "", source_name or "Unknown source")

    return NewsItem(
        title=item.get("title", ""),
        link=normalized_link or "",
        source=canonical_source_name,
        published_at=item.get("pubDate", ""),
    )


def _normalize_and_validate_url(raw_url: str) -> tuple[str | None, str | None, str | None]:
    if not raw_url:
        return None, None, "missing_url"

    parsed = urlparse(raw_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None, None, "missing_hostname"

    if hostname not in ALLOWED_SOURCE_DOMAINS:
        return None, hostname, "disallowed_domain"

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered_key = key.lower()
        if lowered_key.startswith("utm_") or lowered_key in TRACKING_QUERY_PARAMS:
            continue
        filtered_query.append((key, value))

    normalized_url = urlunparse(
        (
            "https",
            hostname,
            parsed.path,
            parsed.params,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )

    return normalized_url, hostname, None


def _fallback_context(fetched_at: datetime) -> dict[str, Any]:
    return {
        "prompt_context": json.dumps([item.model_dump() for item in NO_NEWS_CONTEXT], ensure_ascii=False, indent=2),
        "items": [item.model_dump() for item in NO_NEWS_CONTEXT],
        "fetched_at": fetched_at.isoformat(),
    }
