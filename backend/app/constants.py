from typing import Optional
from pydantic import BaseModel


# API request/response models
class AnalyzeRequest(BaseModel):
    """Request model for story analysis endpoint."""
    topic: str
    mode: Optional[str] = None  # reserved for future use


class PlayerProfileRequest(BaseModel):
    """Request model for player profile deep-dive endpoint."""
    player_id: str
    player_name: str
    player_role: str
    player_type: str
    topic: str
