import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse

import aiohttp

from app.schemas import NewsItem
from app.services.bedrock_embeddings import BedrockEmbeddingService
from app.services.source_policy import get_source_policy
from app.services.url_canonicalization import canonicalize_url
from app.services.vector_search import OpenSearchVectorSearchService, VectorSearchRequest


logger = logging.getLogger(__name__)

NO_NEWS_CONTEXT: list[NewsItem] = []

_embedding_service: BedrockEmbeddingService | None = None
_vector_service: OpenSearchVectorSearchService | None = None


def _get_embedding_service() -> BedrockEmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = BedrockEmbeddingService()
    return _embedding_service


def _get_vector_service() -> OpenSearchVectorSearchService:
    global _vector_service
    if _vector_service is None:
        _vector_service = OpenSearchVectorSearchService()
    return _vector_service


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def fetch_news_context(
    query: str,
    max_results: int = 5,
    timeline_id: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """
    Fetch structured news context for a given topic using the rss2json proxy.

    Returns a dict with serialized prompt context, structured items for API consumers,
    and the timestamp when the fetch completed.
    """
    fetched_at = datetime.now(timezone.utc)

    try:
        provider = "gnews"
        retrieval_queries = build_retrieval_queries(query, provider=provider)

        vector_service = _get_vector_service()
        if vector_service.enabled:
            vector_result = await _fetch_vector_context(
                query=query,
                retrieval_queries=retrieval_queries,
                max_results=max_results,
                fetched_at=fetched_at,
                timeline_id=timeline_id,
                published_from=published_from,
                published_to=published_to,
                sources=sources,
            )
            if not vector_result.get("empty_context"):
                return vector_result

            reason = vector_result.get("empty_context_reason")
            if reason in {"no_vector_results", "vector_fetch_exception"}:
                logger.info(
                    "Vector retrieval returned no usable context. Falling back to RSS news retrieval.",
                    extra={"query": query, "reason": reason},
                )
                return await _fetch_rss_context(
                    query=query,
                    max_results=max_results,
                    fetched_at=fetched_at,
                    retrieval_queries=retrieval_queries,
                    provider=provider,
                )

            return vector_result

        logger.info("Vector backend not configured. Falling back to RSS news retrieval.")
        return await _fetch_rss_context(
            query=query,
            max_results=max_results,
            fetched_at=fetched_at,
            retrieval_queries=retrieval_queries,
            provider=provider,
        )

    except Exception:
        logger.exception(
            "Error fetching news context for query=%s timeline_id=%s",
            query,
            timeline_id,
        )
        return _empty_context(fetched_at, reason="fetch_exception")


async def _fetch_vector_context(
    *,
    query: str,
    retrieval_queries: dict[str, dict[str, Any]],
    max_results: int,
    fetched_at: datetime,
    timeline_id: str | None,
    published_from: str | None,
    published_to: str | None,
    sources: list[str] | None,
) -> dict[str, Any]:
    try:
        embedding_service = _get_embedding_service()
        vector_service = _get_vector_service()

        query_text = retrieval_queries["vector"]["query"]
        filters = retrieval_queries["vector"].get("filters", {})
        policy_domains = sorted(set(filters.get("domain_in", [])))
        policy_sources = sorted(set(filters.get("source_in", [])))
        effective_sources = sorted({s.strip() for s in (sources or []) if s and s.strip()})
        if not effective_sources:
            effective_sources = policy_sources

        embedded = await embedding_service.aembed_query(query_text)
        search_request = VectorSearchRequest(
            vector=embedded.embedding,
            top_k=max_results,
            timeline_id=(timeline_id or "").strip() or None,
            published_from=_parse_iso_datetime(published_from),
            published_to=_parse_iso_datetime(published_to),
            domains=policy_domains or None,
            sources=effective_sources or None,
        )
        hits = await vector_service.asearch(search_request)
        if not hits:
            return _empty_context(fetched_at, reason="no_vector_results")

        items: list[NewsItem] = []
        for hit in hits[:max_results]:
            source = hit.get("_source", {}) if isinstance(hit.get("_source"), dict) else {}
            item = _to_news_item_from_vector(source)
            if item is not None:
                items.append(item)

        if not items:
            return _empty_context(fetched_at, reason="no_documents_after_mandatory_filter")

        return {
            "prompt_context": json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
            "items": [item.model_dump() for item in items],
            "fetched_at": fetched_at.isoformat(),
            "empty_context": False,
            "empty_context_reason": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Vector retrieval failed. Falling back to empty context", extra={"error": str(exc)})
        return _empty_context(fetched_at, reason="vector_fetch_exception")


async def fetch_live_news_context(
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Fetch live internet news context through RSS retrieval regardless of vector availability."""
    fetched_at = datetime.now(timezone.utc)
    provider = "gnews"

    retrieval_queries = build_retrieval_queries(query, provider=provider)
    return await _fetch_rss_context(
        query=query,
        max_results=max_results,
        fetched_at=fetched_at,
        retrieval_queries=retrieval_queries,
        provider=provider,
    )


async def _fetch_rss_context(
    *,
    query: str,
    max_results: int,
    fetched_at: datetime,
    retrieval_queries: dict[str, dict[str, Any]],
    provider: str,
) -> dict[str, Any]:
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


def _to_news_item_from_vector(source_doc: dict[str, Any]) -> NewsItem | None:
    url = str(source_doc.get("url") or "").strip()
    normalized_link, hostname, reject_reason = _normalize_and_validate_url(url)
    if reject_reason:
        logger.info("Rejected vector result URL", extra={"reason": reject_reason, "url": url, "domain": hostname})
        return None

    parsed = urlparse(normalized_link or "")
    domain = (parsed.hostname or "").lower() or hostname
    if not domain:
        return None

    source_value = str(source_doc.get("source") or "").strip()
    if not source_value:
        source_value = get_source_policy().source_aliases.get(domain, domain)

    published_at = str(source_doc.get("published_at") or "")
    if not published_at:
        published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return NewsItem(
        title=str(source_doc.get("title") or ""),
        url=normalized_link or "",
        domain=domain,
        source=source_value,
        provider="pinecone",
        published_at=published_at,
    )


def _normalize_and_validate_url(raw_url: str) -> tuple[str | None, str | None, str | None]:
    normalized_url, hostname, error = canonicalize_url(raw_url)
    if error:
        return None, hostname, error

    policy = get_source_policy()
    if policy.strict_allowlist_validation and policy.allowed_domains and hostname not in policy.allowed_domains:
        return None, hostname, "disallowed_domain"

    return normalized_url, hostname, None


def _empty_context(fetched_at: datetime, reason: str) -> dict[str, Any]:
    return {
        "prompt_context": json.dumps([item.model_dump() for item in NO_NEWS_CONTEXT], ensure_ascii=False, indent=2),
        "items": [item.model_dump() for item in NO_NEWS_CONTEXT],
        "fetched_at": fetched_at.isoformat(),
        "empty_context": True,
        "empty_context_reason": reason,
    }
