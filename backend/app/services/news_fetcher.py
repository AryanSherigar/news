import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import aiohttp

from app.schemas import NewsItem
from app.services.source_policy import TRACKING_QUERY_PARAMS, get_source_policy


logger = logging.getLogger(__name__)

NO_NEWS_CONTEXT: list[NewsItem] = []


async def fetch_news_context(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Fetch structured news context for a given topic using the rss2json proxy.

    Returns a dict with serialized prompt context, structured items for API consumers,
    and the timestamp when the fetch completed.
    """
    fetched_at = datetime.now(timezone.utc)

    try:
        provider = "gnews"
        retrieval_queries = build_retrieval_queries(query, provider=provider)
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
                    news_item = _to_news_item(item, provider=provider)
                    if news_item is not None:
                        news_items.append(news_item)

                if not news_items:
                    logger.warning("No documents remained after source-policy filtering", extra={"query": query})
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


def build_retrieval_queries(user_query: str, provider: str = "gnews") -> dict[str, dict[str, Any]]:
    """Build retrieval query payloads from source-policy settings."""
    trimmed_query = user_query.strip()
    if not trimmed_query:
        raise ValueError("Query cannot be empty")

    policy = get_source_policy()
    filters = policy.build_filters(provider)

    query_suffix = policy.get_query_suffix(provider)
    scoped_query = trimmed_query if not query_suffix else f"{trimmed_query} {query_suffix}"

    retrieval_queries = {
        "metadata": {
            "query": scoped_query,
            "filters": filters,
        },
        "vector": {
            "query": scoped_query,
            "filters": filters,
        },
        "bm25": {
            "query": scoped_query,
            "filters": filters,
        },
    }

    for payload in retrieval_queries.values():
        validate_retrieval_filter(payload, provider=provider)

    return retrieval_queries


def validate_retrieval_filter(retrieval_payload: dict[str, Any], provider: str = "gnews") -> None:
    """Validate retrieval payload against configured source policy."""
    policy = get_source_policy()
    if not policy.strict_allowlist_validation:
        return

    filters = retrieval_payload.get("filters", {})
    domains = set(filters.get("domain_in", []))
    sources = set(filters.get("source_in", []))

    expected_filters = policy.build_filters(provider)
    expected_domains = set(expected_filters.get("domain_in", []))
    expected_sources = set(expected_filters.get("source_in", []))

    has_required_domain_filter = (not expected_domains) or domains == expected_domains
    has_required_source_filter = (not expected_sources) or sources == expected_sources

    if not (has_required_domain_filter and has_required_source_filter):
        raise ValueError("Mandatory retrieval filter missing for configured source policy.")


def _to_news_item(item: dict[str, Any], provider: str) -> NewsItem | None:
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

    policy = get_source_policy()
    mapped_source = policy.source_aliases.get(canonical_domain)
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
        source=mapped_source,
        provider=provider,
        published_at=item.get("pubDate", ""),
    )


def _normalize_and_validate_url(raw_url: str) -> tuple[str | None, str | None, str | None]:
    if not raw_url:
        return None, None, "missing_url"

    parsed = urlparse(raw_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None, None, "missing_hostname"

    policy = get_source_policy()
    if policy.strict_allowlist_validation and policy.allowed_domains and hostname not in policy.allowed_domains:
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
