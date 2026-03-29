import logging

from fastapi import APIRouter, HTTPException

from app.constants import AnalyzeRequest
from app.schemas import StoryData
from app.services.ai_orchestration import analyze_story
from app.services.news_fetcher import fetch_news_context
from app.services.source_validator import (
    SourcePolicyViolationError,
    validate_story_sources_or_raise,
    violations_to_response_payload,
)

router = APIRouter(prefix="/api", tags=["analysis"])
logger = logging.getLogger(__name__)


@router.post("/analyze", response_model=StoryData)
async def analyze(request: AnalyzeRequest) -> StoryData:
    """
    Analyze a story topic and return structured narrative data.

    Takes a topic as input, fetches news context, and returns:
    - timeline: List of significant events
    - players: Key entities involved
    - relationships: Interactions between players
    - arcs: Narrative story threads
    - insights: High-level conclusions
    - news_context: Structured source items used for grounding
    - fetched_at: Timestamp for when context was retrieved
    """
    if not request.topic.strip():
        raise HTTPException(status_code=400, detail="Topic cannot be empty")

    try:
        news_context = await fetch_news_context(
            request.topic,
            timeline_id=request.timeline_id,
            published_from=request.published_from,
            published_to=request.published_to,
            sources=request.sources,
        )

        if news_context.get("empty_context"):
            return StoryData(
                timeline=[],
                players=[],
                relationships=[],
                arcs=[],
                insights=[],
                news_context=[],
                fetched_at=news_context["fetched_at"],
            )

        story_data = await analyze_story(request.topic, news_context["prompt_context"])
        response_payload = story_data.model_copy(update={
            "news_context": news_context["items"],
            "fetched_at": news_context["fetched_at"],
        })
        validate_story_sources_or_raise(response_payload)
        return response_payload

    except SourcePolicyViolationError as e:
        raise HTTPException(status_code=422, detail=violations_to_response_payload(e.violations, provider="gnews"))
    except ValueError as e:
        if "Analyze output failed quality checks" in str(e):
            # Return a safe empty payload instead of surfacing transient model-format issues.
            return StoryData(
                timeline=[],
                players=[],
                relationships=[],
                arcs=[],
                insights=[],
                news_context=[],
                fetched_at=None,
            )
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Error analyzing story for topic=%s timeline_id=%s",
            request.topic,
            request.timeline_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze story: {str(e)}"
        )
