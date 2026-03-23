from fastapi import APIRouter, HTTPException
from app.constants import PlayerProfileRequest
from app.schemas import PlayerProfile
from app.services.ai_orchestration import generate_player_profile

router = APIRouter(prefix="/api", tags=["profiles"])


@router.post("/player-profile", response_model=PlayerProfile)
async def get_player_profile(request: PlayerProfileRequest) -> PlayerProfile:
    """
    Generate a detailed profile for a specific player in the story context.
    
    Returns structured markdown-friendly fields:
    - summary: Executive summary
    - role_in_story: Detailed narrative position
    - motivations: Inferred goals
    - alliances: Key allies
    - conflicts: Adversaries
    - timeline_contributions: Key events influenced
    - risk_score: Strategic risk assessment (0-1)
    - outlook: Predicted trajectory
    - citations: Supporting evidence
    """
    if not request.player_name.strip():
        raise HTTPException(status_code=400, detail="Player name cannot be empty")
    
    try:
        # For now, use placeholder context strings
        # In a full implementation, these would come from the frontend or be cached
        timeline_context = f"Player: {request.player_name} ({request.player_type}) - Role: {request.player_role}"
        related_players_context = f"In the context of: {request.topic}"
        
        profile = await generate_player_profile(
            player_id=request.player_id,
            player_name=request.player_name,
            player_role=request.player_role,
            player_type=request.player_type,
            topic=request.topic,
            timeline_context=timeline_context,
            related_players_context=related_players_context
        )
        
        return profile
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generating player profile: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate player profile: {str(e)}"
        )
