from fastapi import APIRouter, HTTPException
from app.constants import AnalyzeRequest
from app.schemas import StoryData
from app.services.news_fetcher import fetch_news_context
from app.services.ai_orchestration import analyze_story
import json

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
    """
    if not request.topic.strip():
        raise HTTPException(status_code=400, detail="Topic cannot be empty")
    
    try:
        # Fetch news context for the topic
        news_context = await fetch_news_context(request.topic)
        
        # Analyze the story using LangChain + Gemini
        story_data = await analyze_story(request.topic, news_context)
        
        return story_data
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error analyzing story: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze story: {str(e)}"
        )
