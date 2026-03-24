from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# --- Enums ---
class ImpactLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SentimentType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class PlayerType(str, Enum):
    PERSON = "person"
    COMPANY = "company"
    ORGANIZATION = "organization"
    COUNTRY = "country"
    OTHER = "other"


class RelationshipType(str, Enum):
    ALLIANCE = "alliance"
    CONFLICT = "conflict"
    NEUTRAL = "neutral"


class ArcStatus(str, Enum):
    ONGOING = "ongoing"
    RESOLVED = "resolved"


class InsightType(str, Enum):
    WHO_IS_WINNING = "who_is_winning"
    TURNING_POINT = "turning_point"
    KEY_PLAYER = "key_player"
    SUMMARY = "summary"


class Citation(BaseModel):
    """Structured source reference for generated claims."""

    source_name: str = Field(..., description="Publisher or publication name")
    url: str = Field(..., description="Canonical article URL")
    published_at: str = Field(..., description="Original publication timestamp in ISO format if available")
    snippet: str = Field(..., description="Short supporting excerpt or paraphrase grounded in the source")


class NewsItem(BaseModel):
    """Structured live-news context item passed into the analysis prompt."""

    title: str
    url: str
    domain: str | None = None
    source: str = Field(..., min_length=1, description="Publisher/source name or normalized code")
    provider: str = Field(..., min_length=1, description="Ingestion provider/feed name (e.g., gnews, gdelt)")
    published_at: str

    @field_validator("source", "provider", mode="before")
    @classmethod
    def _normalize_source_fields(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized


# --- Story Arc Schemas ---
class StoryEvent(BaseModel):
    """A significant moment that changes the state of the story."""

    id: str
    title: str
    description: str
    date: str = Field(..., description="ISO format if possible, else relative")
    impact: ImpactLevel
    sentiment: SentimentType
    playersInvolved: list[str]
    arcId: str
    citations: list[Citation] = Field(
        default_factory=list,
        min_length=1,
        description="At least one supporting citation for the event"
    )


class Player(BaseModel):
    """An important entity in the story."""

    id: str
    name: str
    type: PlayerType
    role: str = Field(..., description="Short description of their role in the story")
    sentimentScore: float = Field(..., ge=-1, le=1, description="Score from -1 to 1")


class Relationship(BaseModel):
    """Dynamic relationship between two players."""

    source: str = Field(..., description="Player ID")
    target: str = Field(..., description="Player ID")
    type: RelationshipType
    strength: float = Field(..., ge=0, le=1, description="Strength from 0 to 1")
    description: str


class Arc(BaseModel):
    """A meaningful narrative thread composed of multiple events."""

    id: str
    title: str = Field(..., description="Concise name (e.g., 'Legal Battle')")
    summary: str = Field(..., description="2–3 sentence explanation of the arc")
    involvedPlayers: list[str]
    startEventId: str
    endEventId: str | None = None
    status: ArcStatus


class Insight(BaseModel):
    """High-level understanding derived from the story."""

    id: str
    type: InsightType
    content: str
    state_of_play: str | None = Field(
        default=None,
        description="Plain-language status update of what is currently happening (especially for summary insights)"
    )
    why_now: str | None = Field(
        default=None,
        description="Plain-language explanation of why the development matters now (especially for summary insights)"
    )
    watchlist: list[str] = Field(
        default_factory=list,
        description="Specific signals, decisions, or milestones to monitor next (especially for summary insights)"
    )
    citations: list[Citation] = Field(
        default_factory=list,
        min_length=1,
        description="At least one supporting citation for the insight"
    )


class StoryData(BaseModel):
    """Complete story analysis response."""

    timeline: list[StoryEvent]
    players: list[Player]
    relationships: list[Relationship]
    arcs: list[Arc]
    insights: list[Insight]
    news_context: list[NewsItem] = Field(default_factory=list, description="Structured news items used as context")
    fetched_at: datetime | None = Field(default=None, description="Timestamp when the news context was fetched")


class ProfileRelationship(BaseModel):
    """Structured relationship summary for a profile section."""

    player_id: str | None = Field(default=None, description="Referenced player ID when available")
    name: str
    description: str
    relationship_type: RelationshipType | None = Field(default=None, description="Alliance/conflict/neutral when known")
    strength: float | None = Field(default=None, ge=0, le=1, description="Relationship strength when known")
    citations: list[Citation] = Field(default_factory=list, description="Supporting citations for this relationship")


class TimelineContribution(BaseModel):
    """Structured event contribution summary for a profile section."""

    event_id: str | None = Field(default=None, description="Referenced event ID when available")
    event: str
    date: str | None = Field(default=None, description="Event date when available")
    impact: str
    citations: list[Citation] = Field(default_factory=list, description="Supporting citations for this contribution")


# --- Deep-Dive Profile Schema ---
class PlayerProfile(BaseModel):
    """Structured deep-dive profile for a player."""

    id: str = Field(..., description="Player ID")
    name: str
    summary: str = Field(..., description="Executive summary of the player's role")
    role_in_story: str = Field(..., description="Detailed explanation of their position in the narrative")
    motivations: list[str] = Field(..., description="Inferred goals and motives")
    alliances: list[ProfileRelationship] = Field(..., description="List of allies with brief descriptions")
    conflicts: list[ProfileRelationship] = Field(..., description="List of adversaries with brief descriptions")
    timeline_contributions: list[TimelineContribution] = Field(..., description="Key events they influenced or participated in")
    risk_score: float = Field(..., ge=0, le=1, description="Strategic risk assessment from 0 to 1")
    outlook: str = Field(..., description="Prediction of future trajectory")
    citations: list[Citation] = Field(default_factory=list, description="Supporting evidence or source references")
