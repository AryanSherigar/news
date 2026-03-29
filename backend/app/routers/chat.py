import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.constants import ChatRequest
from app.schemas import ChatAnswer
from app.services.ai_orchestration import generate_topic_chat_response
from app.services.response_refresh import refresh_answer_with_fresh_news_if_needed
from app.services.source_validator import (
    SourcePolicyViolationError,
    validate_chat_sources_or_raise,
    violations_to_response_payload,
)

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)


def _validate_chat_request(request: ChatRequest) -> None:
    if not request.topic.strip():
        raise HTTPException(status_code=400, detail="Topic cannot be empty")

    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not request.timeline_slice:
        raise HTTPException(status_code=400, detail="Timeline context is required for chat")


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

    return await refresh_answer_with_fresh_news_if_needed(
        topic=request.topic,
        message=request.message,
        history=history,
        story_context=base_story_context,
        answer=answer,
        generate_response=generate_topic_chat_response,
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
        logger.exception(
            "Error generating topic chat response for topic=%s message_length=%d history_count=%d",
            request.topic,
            len(request.message),
            len(request.history),
        )
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
            logger.exception(
                "Error generating topic chat stream response for topic=%s message_length=%d history_count=%d",
                request.topic,
                len(request.message),
                len(request.history),
            )
            yield json.dumps(
                {
                    "type": "error",
                    "status": 500,
                    "detail": f"Failed to generate chat response: {str(e)}",
                }
            ) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")
