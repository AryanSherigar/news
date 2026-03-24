import json

from fastapi import APIRouter, HTTPException

from app.constants import PlayerProfileRequest
from app.schemas import PlayerProfile
from app.services.ai_orchestration import generate_player_profile
from app.services.source_validator import (
    SourcePolicyViolationError,
    validate_profile_sources_or_raise,
    violations_to_response_payload,
)

router = APIRouter(prefix="/api", tags=["profiles"])


@router.post("/player-profile", response_model=PlayerProfile)
async def get_player_profile(request: PlayerProfileRequest) -> PlayerProfile:
    """
    Generate a detailed profile for a specific player in the story context.

    Returns structured profile fields backed by the current story analysis.
    """
    if not request.player_name.strip():
        raise HTTPException(status_code=400, detail="Player name cannot be empty")

    if not request.timeline_slice:
        raise HTTPException(status_code=400, detail="Timeline context is required")

    try:
        timeline_context = json.dumps(
            [event.model_dump(mode="json") for event in request.timeline_slice],
            indent=2,
        )
        related_players_context = json.dumps(
            [player.model_dump(mode="json") for player in request.player_neighborhood],
            indent=2,
        )
        relationship_context = json.dumps(
            [relationship.model_dump(mode="json") for relationship in request.relationships],
            indent=2,
        )

        profile = await generate_player_profile(
            player_id=request.player_id,
            player_name=request.player_name,
            player_role=request.player_role,
            player_type=request.player_type,
            topic=request.topic,
            timeline_context=timeline_context,
            related_players_context=related_players_context,
            relationship_context=relationship_context,
        )

        validate_profile_sources_or_raise(profile)
        return profile

    except SourcePolicyViolationError as e:
        raise HTTPException(status_code=422, detail=violations_to_response_payload(e.violations, provider="gnews"))
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generating player profile: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate player profile: {str(e)}"
        )
