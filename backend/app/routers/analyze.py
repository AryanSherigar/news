from fastapi import APIRouter, HTTPException

from app.constants import AnalyzeRequest
from app.schemas import StoryData
from app.services.ai_orchestration import analyze_story
from app.services.news_fetcher import fetch_news_context

router = APIRouter(prefix="/api", tags=["analysis"])


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
        news_context = await fetch_news_context(request.topic)
        story_data = await analyze_story(request.topic, news_context["prompt_context"])
        return story_data.model_copy(update={
            "news_context": news_context["items"],
            "fetched_at": news_context["fetched_at"],
        })

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error analyzing story: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze story: {str(e)}"
        )
