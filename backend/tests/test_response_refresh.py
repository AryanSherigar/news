import unittest
from unittest.mock import AsyncMock, patch

from app.schemas import ChatAnswer, Citation
from app.services.response_refresh import refresh_answer_with_fresh_news_if_needed


class ResponseRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_uses_fresh_news_when_answer_is_low_confidence(self) -> None:
        initial = ChatAnswer(
            message="I don't have enough source-backed information",
            citations=[
                Citation(
                    source_name="Reuters",
                    url="https://reuters.com/story",
                    published_at="2026-03-27T00:00:00Z",
                    snippet="Evidence snippet",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.2,
            suggested_followups=[],
        )
        refreshed = ChatAnswer(
            message="Fresh evidence confirms new talks were announced.",
            citations=[
                Citation(
                    source_name="Guardian",
                    url="https://theguardian.com/world/story",
                    published_at="2026-03-27T08:00:00Z",
                    snippet="Talks resumed this morning",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.73,
            suggested_followups=[],
        )

        generate_mock = AsyncMock(return_value=refreshed)

        with patch(
            "app.services.response_refresh.fetch_news_context",
            new=AsyncMock(return_value={"empty_context": True, "items": []}),
        ), patch(
            "app.services.response_refresh.fetch_live_news_context",
            new=AsyncMock(
                return_value={
                    "empty_context": False,
                    "items": [
                        {
                            "title": "Talks resumed",
                            "url": "https://theguardian.com/world/story",
                            "domain": "theguardian.com",
                            "source": "guardian",
                            "published_at": "2026-03-27T08:00:00Z",
                        }
                    ],
                }
            ),
        ):
            answer = await refresh_answer_with_fresh_news_if_needed(
                topic="US Iran conflict",
                message="Any updates?",
                history=[{"role": "user", "content": "What changed?"}],
                story_context={"topic": "US Iran conflict", "timeline": [{"id": "event-1"}]},
                answer=initial,
                generate_response=generate_mock,
            )

        self.assertEqual(answer.message, refreshed.message)
        self.assertEqual(generate_mock.await_count, 1)

    async def test_refresh_skips_news_when_context_is_rich_and_confident(self) -> None:
        initial = ChatAnswer(
            message="Sanctions expanded after a parliamentary vote.",
            citations=[
                Citation(
                    source_name="Reuters",
                    url="https://reuters.com/story",
                    published_at="2026-03-27T00:00:00Z",
                    snippet="Vote expanded sanctions",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.82,
            suggested_followups=[],
        )

        generate_mock = AsyncMock()

        answer = await refresh_answer_with_fresh_news_if_needed(
            topic="US Iran conflict",
            message="Any updates?",
            history=[{"role": "user", "content": "What changed?"}],
            story_context={
                "topic": "US Iran conflict",
                "timeline": [
                    {
                        "id": "event-1",
                        "citations": [
                            {
                                "source_name": "Reuters",
                                "url": "https://reuters.com/story",
                                "published_at": "2026-03-27T00:00:00Z",
                                "snippet": "Vote expanded sanctions",
                            }
                        ],
                    },
                    {
                        "id": "event-2",
                        "citations": [
                            {
                                "source_name": "AP",
                                "url": "https://apnews.com/story",
                                "published_at": "2026-03-28T00:00:00Z",
                                "snippet": "Regional response followed",
                            }
                        ],
                    },
                ],
            },
            answer=initial,
            generate_response=generate_mock,
        )

        self.assertEqual(answer.message, initial.message)
        self.assertEqual(generate_mock.await_count, 0)


if __name__ == "__main__":
    unittest.main()
