import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import aiohttp

from app.schemas import NewsItem, NewsSource


logger = logging.getLogger(__name__)

ALLOWED_SOURCE_DOMAINS = {
    "economictimes.indiatimes.com",
    "timesofindia.indiatimes.com",
}

ALLOWED_SOURCE_CODES = {"ET", "TOI"}

SOURCE_ALIASES = {
    "economictimes.indiatimes.com": "ET",
    "timesofindia.indiatimes.com": "TOI",
}

MANDATORY_SOURCE_FILTER_CLAUSE = "(site:economictimes.indiatimes.com OR site:timesofindia.indiatimes.com)"

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

NO_NEWS_CONTEXT: list[NewsItem] = []


async def fetch_news_context(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Fetch structured news context for a given topic using the rss2json proxy.

    Returns a dict with serialized prompt context, structured items for API consumers,
    and the timestamp when the fetch completed.
    """
    fetched_at = datetime.now(timezone.utc)

    try:
        retrieval_queries = build_retrieval_queries(query)
        encoded_query = quote_plus(retrieval_queries["bm25"]["query"])
        url = f"https://www.rss2json.com/api.json?rss_url=https://news.google.com/rss/search?q={encoded_query}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return _empty_context(fetched_at, reason="upstream_error")

                data = await response.json()
                items = data.get("items", [])[:max_results]
                if not items:
                    return _empty_context(fetched_at, reason="no_results")

                news_items = []
                for item in items:
                    news_item = _to_news_item(item)
                    if news_item is not None:
                        news_items.append(news_item)

                if not news_items:
                    logger.warning("No documents remained after mandatory source filtering", extra={"query": query})
                    return _empty_context(fetched_at, reason="no_documents_after_mandatory_filter")

                return {
                    "prompt_context": json.dumps([item.model_dump() for item in news_items], ensure_ascii=False, indent=2),
                    "items": [item.model_dump() for item in news_items],
                    "fetched_at": fetched_at.isoformat(),
                    "empty_context": False,
                    "empty_context_reason": None,
                }

    except Exception as e:
        print(f"Error fetching news: {e}")
        return _empty_context(fetched_at, reason="fetch_exception")


def build_retrieval_queries(user_query: str) -> dict[str, dict[str, Any]]:
    """Build retrieval query payloads with mandatory source filtering for each strategy."""
    trimmed_query = user_query.strip()
    if not trimmed_query:
        raise ValueError("Query cannot be empty")

    retrieval_queries = {
        "metadata": {
            "query": trimmed_query,
            "filters": {
                "domain_in": sorted(ALLOWED_SOURCE_DOMAINS),
                "source_in": sorted(ALLOWED_SOURCE_CODES),
            },
        },
        "vector": {
            "query": f"{trimmed_query} {MANDATORY_SOURCE_FILTER_CLAUSE}",
            "filters": {
                "domain_in": sorted(ALLOWED_SOURCE_DOMAINS),
                "source_in": sorted(ALLOWED_SOURCE_CODES),
            },
        },
        "bm25": {
            "query": f"{trimmed_query} {MANDATORY_SOURCE_FILTER_CLAUSE}",
            "filters": {
                "domain_in": sorted(ALLOWED_SOURCE_DOMAINS),
                "source_in": sorted(ALLOWED_SOURCE_CODES),
            },
        },
    }

    for payload in retrieval_queries.values():
        validate_retrieval_filter(payload)

    return retrieval_queries


def validate_retrieval_filter(retrieval_payload: dict[str, Any]) -> None:
    """Validate that every retrieval payload contains the mandatory source filter."""
    filters = retrieval_payload.get("filters", {})
    domains = set(filters.get("domain_in", []))
    sources = set(filters.get("source_in", []))

    has_domain_filter = domains == ALLOWED_SOURCE_DOMAINS
    has_source_filter = sources == ALLOWED_SOURCE_CODES
    if not (has_domain_filter or has_source_filter):
        raise ValueError(
            "Mandatory retrieval filter missing: require domain IN "
            "('economictimes.indiatimes.com', 'timesofindia.indiatimes.com') "
            "or source IN ('ET', 'TOI')."
        )


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

    parsed_normalized = urlparse(normalized_link or "")
    canonical_domain = (parsed_normalized.hostname or "").lower()
    if not canonical_domain:
        logger.info("Rejected news item URL", extra={"reason": "missing_canonical_domain", "url": original_link})
        return None

    mapped_source = SOURCE_ALIASES.get(canonical_domain)
    if not mapped_source:
        logger.info(
            "Rejected news item URL",
            extra={"reason": "unknown_source_mapping", "domain": canonical_domain, "url": normalized_link},
        )
        return None

    return NewsItem(
        title=item.get("title", ""),
        url=normalized_link or "",
        domain=canonical_domain,
        source=NewsSource(mapped_source),
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


def _empty_context(fetched_at: datetime, reason: str) -> dict[str, Any]:
    return {
        "prompt_context": json.dumps([item.model_dump() for item in NO_NEWS_CONTEXT], ensure_ascii=False, indent=2),
        "items": [item.model_dump() for item in NO_NEWS_CONTEXT],
        "fetched_at": fetched_at.isoformat(),
        "empty_context": True,
        "empty_context_reason": reason,
    }
