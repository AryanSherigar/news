import unittest

from app.schemas import (
    Arc,
    ArcStatus,
    Citation,
    ImpactLevel,
    Insight,
    InsightType,
    Player,
    PlayerProfile,
    PlayerType,
    ProfileRelationship,
    Relationship,
    RelationshipType,
    SentimentType,
    StoryData,
    StoryEvent,
    TimelineContribution,
)
from app.services.source_validator import (
    SourcePolicyViolationError,
    canonicalize_and_validate_source_url,
    validate_profile_sources_or_raise,
    validate_story_sources_or_raise,
)


class SourceValidatorTests(unittest.TestCase):
    def test_canonicalize_and_validate_source_url_keeps_allowlisted_domain(self) -> None:
        canonical, domain, error = canonicalize_and_validate_source_url(
            "http://economictimes.indiatimes.com/story?a=1&utm_source=x"
        )

        self.assertEqual(canonical, "https://economictimes.indiatimes.com/story?a=1")
        self.assertEqual(domain, "economictimes.indiatimes.com")
        self.assertIsNone(error)

    def test_validate_story_sources_or_raise_raises_for_disallowed_domain(self) -> None:
        story = _make_story("https://example.com/disallowed")

        with self.assertRaises(SourcePolicyViolationError):
            validate_story_sources_or_raise(story)

    def test_validate_profile_sources_or_raise_canonicalizes_nested_profile_citation(self) -> None:
        profile = _make_profile("http://timesofindia.indiatimes.com/article?fbclid=123")

        validate_profile_sources_or_raise(profile)

        self.assertEqual(
            profile.timeline_contributions[0].citations[0].url,
            "https://timesofindia.indiatimes.com/article",
        )


def _make_citation(url: str) -> Citation:
    return Citation(
        source_name="Test Source",
        url=url,
        published_at="2026-03-24T00:00:00Z",
        snippet="Supporting snippet",
    )


def _make_story(url: str) -> StoryData:
    return StoryData(
        timeline=[
            StoryEvent(
                id="event-1",
                title="Event",
                description="Description",
                date="2026-03-24",
                impact=ImpactLevel.MEDIUM,
                sentiment=SentimentType.NEUTRAL,
                playersInvolved=["player-1"],
                arcId="arc-1",
                citations=[_make_citation(url)],
            )
        ],
        players=[
            Player(
                id="player-1",
                name="Player",
                type=PlayerType.PERSON,
                role="Role",
                sentimentScore=0.2,
            )
        ],
        relationships=[
            Relationship(
                source="player-1",
                target="player-1",
                type=RelationshipType.NEUTRAL,
                strength=0.5,
                description="Neutral",
            )
        ],
        arcs=[
            Arc(
                id="arc-1",
                title="Arc",
                summary="Summary",
                involvedPlayers=["player-1"],
                startEventId="event-1",
                endEventId=None,
                status=ArcStatus.ONGOING,
            )
        ],
        insights=[
            Insight(
                id="insight-1",
                type=InsightType.SUMMARY,
                content="Content",
                state_of_play="State",
                why_now="Why",
                watchlist=["Watch"],
                citations=[_make_citation("https://economictimes.indiatimes.com/insight")],
            )
        ],
    )


def _make_profile(url: str) -> PlayerProfile:
    return PlayerProfile(
        id="player-1",
        name="Player",
        summary="Summary",
        role_in_story="Role",
        motivations=["Motive"],
        alliances=[
            ProfileRelationship(
                player_id="player-2",
                name="Ally",
                description="Description",
                relationship_type=RelationshipType.ALLIANCE,
                strength=0.7,
                citations=[_make_citation("https://timesofindia.indiatimes.com/alliance")],
            )
        ],
        conflicts=[
            ProfileRelationship(
                player_id="player-3",
                name="Rival",
                description="Description",
                relationship_type=RelationshipType.CONFLICT,
                strength=0.8,
                citations=[_make_citation("https://economictimes.indiatimes.com/conflict")],
            )
        ],
        timeline_contributions=[
            TimelineContribution(
                event_id="event-1",
                event="Event",
                date="2026-03-24",
                impact="High",
                citations=[_make_citation(url)],
            )
        ],
        risk_score=0.4,
        outlook="Outlook",
        citations=[_make_citation("https://timesofindia.indiatimes.com/profile")],
    )


if __name__ == "__main__":
    unittest.main()
