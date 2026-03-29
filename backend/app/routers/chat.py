import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.constants import ChatRequest
from app.schemas import ChatAnswer
from app.services.news_fetcher import fetch_live_news_context, fetch_news_context
from app.services.ai_orchestration import generate_topic_chat_response
from app.services.source_policy import build_prompt_policy_context
from app.services.source_validator import (
    SourcePolicyViolationError,
    validate_chat_sources_or_raise,
    violations_to_response_payload,
)

router = APIRouter(prefix="/api", tags=["chat"])


def _validate_chat_request(request: ChatRequest) -> None:
    if not request.topic.strip():
        raise HTTPException(status_code=400, detail="Topic cannot be empty")

    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not request.timeline_slice:
        raise HTTPException(status_code=400, detail="Timeline context is required for chat")


def _timeline_has_thin_context(timeline_context: list[dict[str, Any]]) -> bool:
    citation_count = 0
    for event in timeline_context:
        citations = event.get("citations") if isinstance(event, dict) else []
        if isinstance(citations, list):
            citation_count += len(citations)

    return len(timeline_context) < 2 or citation_count < 2


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


def _should_refresh_news_context(answer: ChatAnswer, fallback_text: str) -> bool:
    normalized_fallback = fallback_text.strip().lower()
    normalized_message = answer.message.strip().lower()

    if not normalized_message:
        return True
    if normalized_fallback and normalized_message == normalized_fallback:
        return True
    if answer.confidence < 0.45:
        return True
    return False


async def _generate_answer_with_optional_fresh_news(request: ChatRequest) -> ChatAnswer:
    timeline_context = [event.model_dump(mode="json") for event in request.timeline_slice]
    history = [message.model_dump(mode="json") for message in request.history]

    base_story_context: dict[str, Any] = {
        "topic": request.topic,
        "timeline": timeline_context,
    }

    answer = await generate_topic_chat_response(
        topic=request.topic,
        message=request.message,
        history=history,
        story_context=base_story_context,
    )

    policy_context = build_prompt_policy_context()
    should_try_fresh_news = _timeline_has_thin_context(timeline_context) or _should_refresh_news_context(
        answer,
        policy_context.fallback_text,
    )
    if not should_try_fresh_news:
        return answer

    news_payload = await fetch_news_context(
        query=f"{request.topic} {request.message}".strip(),
        max_results=6,
    )
    if news_payload.get("empty_context"):
        news_payload = await fetch_live_news_context(
            query=f"{request.topic} {request.message}".strip(),
            max_results=6,
        )
        if news_payload.get("empty_context"):
            return answer

    fresh_items = news_payload.get("items", [])
    if not isinstance(fresh_items, list) or not fresh_items:
        return answer

    fresh_evidence = _to_fresh_news_evidence([item for item in fresh_items if isinstance(item, dict)])
    if not fresh_evidence:
        return answer

    enriched_story_context = {
        **base_story_context,
        "fresh_news_evidence": fresh_evidence,
    }
    return await generate_topic_chat_response(
        topic=request.topic,
        message=request.message,
        history=history,
        story_context=enriched_story_context,
    )


@router.post("/chat", response_model=ChatAnswer)
async def chat(request: ChatRequest) -> ChatAnswer:
    """Answer user questions about the currently analyzed topic."""
    _validate_chat_request(request)

    try:
        answer = await _generate_answer_with_optional_fresh_news(request)

        validate_chat_sources_or_raise(answer)
        return answer
    except SourcePolicyViolationError as e:
        raise HTTPException(status_code=422, detail=violations_to_response_payload(e.violations, provider="gnews"))
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(e),
                "code": "chat_quality_check_failed",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generating topic chat response: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate chat response: {str(e)}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream a topic-chat answer as NDJSON deltas and a final structured payload."""
    _validate_chat_request(request)

    async def stream_generator():
        try:
            answer = await _generate_answer_with_optional_fresh_news(request)
            validate_chat_sources_or_raise(answer)

            words = answer.message.split()
            if not words:
                words = [answer.message]

            settings = get_settings()
            delay_ms = max(0, settings.chat_stream_simulated_delay_ms)

            for index, word in enumerate(words):
                suffix = " " if index < len(words) - 1 else ""
                payload = {"type": "delta", "delta": f"{word}{suffix}"}
                yield json.dumps(payload) + "\n"
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)

            yield json.dumps({"type": "final", "answer": answer.model_dump(mode="json")}) + "\n"
        except SourcePolicyViolationError as e:
            yield json.dumps(
                {
                    "type": "error",
                    "status": 422,
                    "detail": violations_to_response_payload(e.violations, provider="gnews"),
                }
            ) + "\n"
        except ValueError as e:
            yield json.dumps(
                {
                    "type": "error",
                    "status": 422,
                    "detail": {
                        "message": str(e),
                        "code": "chat_quality_check_failed",
                    },
                }
            ) + "\n"
        except Exception as e:
            print(f"Error generating topic chat stream response: {e}")
            yield json.dumps(
                {
                    "type": "error",
                    "status": 500,
                    "detail": f"Failed to generate chat response: {str(e)}",
                }
            ) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")
