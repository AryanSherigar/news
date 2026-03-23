from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum


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


class StoryData(BaseModel):
    """Complete story analysis response."""
    timeline: list[StoryEvent]
    players: list[Player]
    relationships: list[Relationship]
    arcs: list[Arc]
    insights: list[Insight]


# --- Deep-Dive Profile Schema ---
class PlayerProfile(BaseModel):
    """Structured deep-dive profile for a player."""
    id: str = Field(..., description="Player ID")
    name: str
    summary: str = Field(..., description="Executive summary of the player's role")
    role_in_story: str = Field(..., description="Detailed explanation of their position in the narrative")
    motivations: list[str] = Field(..., description="Inferred goals and motives")
    alliances: list[dict] = Field(..., description="List of allies with brief descriptions")
    conflicts: list[dict] = Field(..., description="List of adversaries with brief descriptions")
    timeline_contributions: list[dict] = Field(..., description="Key events they influenced or participated in")
    risk_score: float = Field(..., ge=0, le=1, description="Strategic risk assessment from 0 to 1")
    outlook: str = Field(..., description="Prediction of future trajectory")
    citations: list[str] = Field(default_factory=list, description="Supporting evidence or source references")
