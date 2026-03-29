from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.schemas import ChatAnswer
from app.services.news_fetcher import fetch_live_news_context, fetch_news_context
from app.services.source_policy import build_prompt_policy_context

GenerateResponseFn = Callable[..., Awaitable[ChatAnswer]]


def _timeline_has_thin_context(timeline_context: list[dict[str, Any]]) -> bool:
    settings = get_settings()
    citation_count = 0

    for event in timeline_context:
        citations = event.get("citations") if isinstance(event, dict) else []
        if isinstance(citations, list):
            citation_count += len(citations)

    return (
        len(timeline_context) < settings.source_policy_refresh_min_timeline_events
        or citation_count < settings.source_policy_refresh_min_citations
    )


def _to_fresh_news_evidence(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []

    for item in items:
        source_name = str(item.get("source") or item.get("domain") or "news_source").strip()
        url = str(item.get("url") or "").strip()
        published_at = str(item.get("published_at") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = title or "Freshly retrieved news item relevant to the topic."

        if source_name and url and published_at and snippet:
            evidence.append(
                {
                    "source_name": source_name,
                    "url": url,
                    "published_at": published_at,
                    "snippet": snippet,
                }
            )

    return evidence


def _should_refresh_news_context(answer: ChatAnswer) -> bool:
    settings = get_settings()
    policy_context = build_prompt_policy_context()
    normalized_fallback = policy_context.fallback_text.strip().lower()
    normalized_message = answer.message.strip().lower()

    if not normalized_message:
        return True
    if normalized_fallback and normalized_message == normalized_fallback:
        return True
    if answer.confidence < settings.source_policy_refresh_confidence_threshold:
        return True
    return False


async def refresh_answer_with_fresh_news_if_needed(
    *,
    topic: str,
    message: str,
    history: list[dict[str, str]],
    story_context: dict[str, Any],
    answer: ChatAnswer,
    generate_response: GenerateResponseFn,
) -> ChatAnswer:
    timeline_context = story_context.get("timeline")
    if not isinstance(timeline_context, list):
        timeline_context = []

    should_try_fresh_news = _timeline_has_thin_context(timeline_context) or _should_refresh_news_context(answer)
    if not should_try_fresh_news:
        return answer

    settings = get_settings()
    query = f"{topic} {message}".strip()

    try:
        news_payload = await fetch_news_context(query=query, max_results=settings.source_policy_refresh_max_results)
        if news_payload.get("empty_context"):
            news_payload = await fetch_live_news_context(query=query, max_results=settings.source_policy_refresh_max_results)
            if news_payload.get("empty_context"):
                return answer

        fresh_items = news_payload.get("items", [])
        if not isinstance(fresh_items, list) or not fresh_items:
            return answer

        fresh_evidence = _to_fresh_news_evidence([item for item in fresh_items if isinstance(item, dict)])
        if not fresh_evidence:
            return answer

        enriched_story_context = {
            **story_context,
            "fresh_news_evidence": fresh_evidence,
        }

        return await generate_response(
            topic=topic,
            message=message,
            history=history,
            story_context=enriched_story_context,
        )
    except Exception:
        return answer
