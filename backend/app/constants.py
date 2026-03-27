from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas import Relationship, StoryEvent


class RelatedPlayerContext(BaseModel):
    """Structured related-player context for profile generation."""

    player_id: str
    name: str
    role: str
    relationship_to_selected: str


# API request/response models
class AnalyzeRequest(BaseModel):
    """Request model for story analysis endpoint."""

    topic: str
    mode: Optional[str] = None  # reserved for future use
    timeline_id: Optional[str] = None
    published_from: Optional[str] = None
    published_to: Optional[str] = None
    sources: Optional[list[str]] = None


class PlayerProfileRequest(BaseModel):
    """Request model for player profile deep-dive endpoint."""

    player_id: str
    player_name: str
    player_role: str
    player_type: str
    topic: str
    timeline_slice: list[StoryEvent]
    relationships: list[Relationship]
    player_neighborhood: list[RelatedPlayerContext]


class ChatMessageInput(BaseModel):
    """Chat history message passed by the client for stateless chat turns."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """Request model for topic-aware chat endpoint."""

    topic: str
    message: str
    history: list[ChatMessageInput] = Field(default_factory=list)
    timeline_slice: list[StoryEvent] = Field(default_factory=list)
