from langchain_google_genai import ChatGoogleGenerativeAI
from app.schemas import StoryData, PlayerProfile
from app.prompts import get_analyze_prompt, get_deep_dive_prompt
from app.config import get_settings


def get_gemini_model():
    """
    Initialize and return a ChatGoogleGenerativeAI model instance.
    
    This is the single point where the model provider is configured.
    To switch to OpenAI, Anthropic, etc., only this function needs to change.
    """
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model="gemini-3-flash-preview",
        google_api_key=settings.gemini_api_key,
        temperature=0.2,  # Low temperature for consistency
    )


async def analyze_story(topic: str, news_context: str) -> StoryData:
    """
    Analyze a story topic and return structured StoryData.
    
    Uses LangChain with ChatGoogleGenerativeAI and enforces StoryData schema.
    """
    model = get_gemini_model()
    prompt = get_analyze_prompt()
    
    # Create chain with structured output
    chain = prompt | model.with_structured_output(StoryData)
    
    # Invoke with context
    result = await chain.ainvoke({
        "topic": topic,
        "news_context": news_context
    })
    
    return result


async def generate_player_profile(
    player_id: str,
    player_name: str,
    player_role: str,
    player_type: str,
    topic: str,
    timeline_context: str,
    related_players_context: str
) -> PlayerProfile:
    """
    Generate a detailed player profile for the deep-dive feature.
    
    Uses LangChain with structured output to enforce PlayerProfile schema.
    """
    model = get_gemini_model()
    prompt = get_deep_dive_prompt()
    
    # Create chain with structured output
    chain = prompt | model.with_structured_output(PlayerProfile)
    
    # Invoke with context
    result = await chain.ainvoke({
        "player_name": player_name,
        "player_role": player_role,
        "player_type": player_type,
        "topic": topic,
        "timeline_context": timeline_context,
        "related_players_context": related_players_context
    })
    
    # Ensure the ID matches the player_id provided
    result.id = player_id
    
    return result
