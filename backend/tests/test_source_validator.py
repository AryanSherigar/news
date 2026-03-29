import unittest
from unittest.mock import patch

from app.schemas import (
    Arc,
    ArcStatus,
    ChatAnswer,
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
from app.services.source_policy import ProviderRetrievalRule, SourcePolicy
from app.services.source_validator import (
    CitationViolation,
    SourcePolicyViolationError,
    canonicalize_and_validate_source_url,
    validate_chat_sources_or_raise,
    validate_profile_sources_or_raise,
    validate_story_sources_or_raise,
    violations_to_response_payload,
)


class SourceValidatorTests(unittest.TestCase):
    def test_canonicalize_and_validate_source_url_keeps_allowlisted_domain(self) -> None:
        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            canonical, domain, error = canonicalize_and_validate_source_url(
                "http://theguardian.com/story?a=1&utm_source=x&fbclid=abc"
            )

        self.assertEqual(canonical, "https://theguardian.com/story?a=1")
        self.assertEqual(domain, "theguardian.com")
        self.assertIsNone(error)

    def test_canonicalize_and_validate_source_url_accepts_schemeless_input(self) -> None:
        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            canonical, domain, error = canonicalize_and_validate_source_url(
                " www.reuters.com/story?utm_source=newsletter&id=42 "
            )

        self.assertEqual(canonical, "https://reuters.com/story?id=42")
        self.assertEqual(domain, "reuters.com")
        self.assertIsNone(error)

    def test_validate_story_sources_or_raise_rejects_non_allowlisted_domain_in_strict_mode(self) -> None:
        story = _make_story("https://example.com/disallowed")

        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            with self.assertRaises(SourcePolicyViolationError):
                validate_story_sources_or_raise(story)

    def test_validate_profile_sources_or_raise_canonicalizes_nested_profile_citation(self) -> None:
        profile = _make_profile("http://reuters.com/article?fbclid=123")

        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            validate_profile_sources_or_raise(profile)

        self.assertEqual(
            profile.timeline_contributions[0].citations[0].url,
            "https://reuters.com/article",
        )

    def test_validate_story_sources_or_raise_accepts_mixed_provider_domains_in_broad_mode(self) -> None:
        story = _make_story("https://www.theguardian.com/world/2026/mar/24/update?utm_medium=email")
        story.timeline[0].citations.append(_make_citation("https://www.gdeltproject.org/data?utm_campaign=a"))
        story.insights[0].citations = [_make_citation("https://news.google.com/articles/xyz?ref=abc")]

        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=False),
        ):
            validate_story_sources_or_raise(story)

        self.assertEqual(story.timeline[0].citations[0].url, "https://theguardian.com/world/2026/mar/24/update")
        self.assertEqual(story.timeline[0].citations[1].url, "https://gdeltproject.org/data")
        self.assertEqual(story.insights[0].citations[0].url, "https://news.google.com/articles/xyz")

    def test_validate_chat_sources_or_raise_canonicalizes_chat_citation(self) -> None:
        answer = ChatAnswer(
            message="Summary",
            citations=[_make_citation("http://reuters.com/story?utm_source=abc")],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.9,
            suggested_followups=[],
        )

        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            validate_chat_sources_or_raise(answer)

        self.assertEqual(answer.citations[0].url, "https://reuters.com/story")

    def test_validate_story_sources_or_raise_rejects_mixed_provider_domains_in_strict_mode(self) -> None:
        story = _make_story("https://www.theguardian.com/world/2026/mar/24/update")
        story.insights[0].citations = [_make_citation("https://news.google.com/articles/xyz")]

        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            with self.assertRaises(SourcePolicyViolationError) as exc_info:
                validate_story_sources_or_raise(story)

        rejected_domains = {violation.domain for violation in exc_info.exception.violations}
        self.assertSetEqual(rejected_domains, {"news.google.com"})

    def test_violations_payload_is_policy_aware_for_configured_sources(self) -> None:
        violation = CitationViolation(
            location="timeline[0].citations[0].url",
            url="https://example.org/story",
            domain="example.org",
            reason="disallowed_domain",
        )

        with patch(
            "app.services.source_validator.get_source_policy",
            return_value=_policy_fixture(strict=True, allowed_source_ids=("GUARDIAN", "GDELT", "GNEWS")),
        ):
            payload = violations_to_response_payload([violation], provider="guardian")

        self.assertEqual(payload["message"], "Model output included citations outside configured source policy.")
        self.assertEqual(payload["source_policy"]["allowed_source_ids"], ["GDELT", "GNEWS", "GUARDIAN"])
        self.assertEqual(payload["source_policy"]["provider"], "guardian")
        self.assertEqual(payload["source_policy"]["provider_filters"], {"source_in": ["GDELT", "GNEWS", "GUARDIAN"]})
        self.assertEqual(payload["violations"][0]["reason"], "disallowed_domain")


def _policy_fixture(
    strict: bool,
    allowed_domains: tuple[str, ...] = ("theguardian.com", "reuters.com"),
    allowed_source_ids: tuple[str, ...] = ("guardian", "reuters"),
) -> SourcePolicy:
    return SourcePolicy(
        allowed_domains=frozenset(allowed_domains),
        allowed_source_ids=frozenset(allowed_source_ids),
        source_aliases={domain: source_id for domain, source_id in zip(allowed_domains, allowed_source_ids)},
        strict_allowlist_validation=strict,
        provider_rules={
            "guardian": ProviderRetrievalRule(
                provider="guardian",
                include_domain_filter=False,
                include_source_filter=True,
            ),
            "gdelt": ProviderRetrievalRule(
                provider="gdelt",
                include_domain_filter=True,
                include_source_filter=False,
            ),
            "gnews": ProviderRetrievalRule(
                provider="gnews",
                include_domain_filter=True,
                include_source_filter=True,
            ),
        },
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
                citations=[_make_citation("https://theguardian.com/insight")],
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
                citations=[_make_citation("https://reuters.com/alliance")],
            )
        ],
        conflicts=[
            ProfileRelationship(
                player_id="player-3",
                name="Rival",
                description="Description",
                relationship_type=RelationshipType.CONFLICT,
                strength=0.8,
                citations=[_make_citation("https://theguardian.com/conflict")],
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
        citations=[_make_citation("https://reuters.com/profile")],
    )


if __name__ == "__main__":
    unittest.main()
