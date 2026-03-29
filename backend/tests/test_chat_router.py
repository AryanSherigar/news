import unittest
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ChatAnswer, Citation


class ChatRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_chat_rejects_empty_message(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "topic": "Budget 2026",
                "message": "   ",
                "timeline_slice": [{
                    "id": "event-1",
                    "title": "Event",
                    "description": "Description",
                    "date": "2026-03-27",
                    "impact": "high",
                    "sentiment": "neutral",
                    "playersInvolved": ["player-1"],
                    "arcId": "arc-1",
                    "citations": [
                        {
                            "source_name": "Reuters",
                            "url": "https://reuters.com/story",
                            "published_at": "2026-03-27T00:00:00Z",
                            "snippet": "Snippet",
                        }
                    ],
                }],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Message cannot be empty")

    def test_chat_returns_structured_response(self) -> None:
        mock_response = {
            "message": "The latest escalation happened after sanctions expanded.",
            "citations": [
                {
                    "source_name": "Reuters",
                    "url": "https://reuters.com/story",
                    "published_at": "2026-03-27T00:00:00Z",
                    "snippet": "Evidence snippet",
                }
            ],
            "outside_topic": False,
            "outside_topic_note": None,
            "confidence": 0.76,
            "suggested_followups": ["Which actor is most exposed?"],
        }

        payload = {
            "topic": "US Iran conflict",
            "message": "What changed this week?",
            "history": [{"role": "user", "content": "Summarize the latest developments"}],
            "timeline_slice": [{
                "id": "event-1",
                "title": "Event",
                "description": "Description",
                "date": "2026-03-27",
                "impact": "high",
                "sentiment": "neutral",
                "playersInvolved": ["player-1"],
                "arcId": "arc-1",
                "citations": [
                    {
                        "source_name": "Reuters",
                        "url": "https://reuters.com/story",
                        "published_at": "2026-03-27T00:00:00Z",
                        "snippet": "Snippet",
                    }
                ],
            }],
        }

        with patch("app.routers.chat.generate_topic_chat_response", return_value=mock_response), patch(
            "app.routers.chat.fetch_news_context",
            return_value={"empty_context": True, "items": []},
        ), patch(
            "app.routers.chat.fetch_live_news_context",
            return_value={"empty_context": True, "items": []},
        ), patch(
            "app.routers.chat.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ):
            response = self.client.post("/api/chat", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["message"], mock_response["message"])
        self.assertEqual(body["citations"][0]["url"], "https://reuters.com/story")

    def test_chat_stream_returns_delta_and_final_events(self) -> None:
        payload = {
            "topic": "US Iran conflict",
            "message": "What changed this week?",
            "history": [{"role": "user", "content": "Summarize the latest developments"}],
            "timeline_slice": [{
                "id": "event-1",
                "title": "Event",
                "description": "Description",
                "date": "2026-03-27",
                "impact": "high",
                "sentiment": "neutral",
                "playersInvolved": ["player-1"],
                "arcId": "arc-1",
                "citations": [
                    {
                        "source_name": "Reuters",
                        "url": "https://reuters.com/story",
                        "published_at": "2026-03-27T00:00:00Z",
                        "snippet": "Snippet",
                    }
                ],
            }],
        }

        chat_answer = ChatAnswer(
            message="The latest escalation happened after sanctions expanded.",
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
            confidence=0.76,
            suggested_followups=["Which actor is most exposed?"],
        )

        with patch("app.routers.chat.generate_topic_chat_response", return_value=chat_answer), patch(
            "app.routers.chat.fetch_news_context",
            return_value={"empty_context": True, "items": []},
        ), patch(
            "app.routers.chat.fetch_live_news_context",
            return_value={"empty_context": True, "items": []},
        ), patch(
            "app.routers.chat.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ):
            with self.client.stream("POST", "/api/chat/stream", json=payload) as response:
                self.assertEqual(response.status_code, 200)
                events = [
                    json.loads(line)
                    for line in response.iter_lines()
                    if line
                ]

        self.assertGreaterEqual(len(events), 2)

        event_types = [event["type"] for event in events]
        self.assertEqual(event_types[-1], "final")
        self.assertTrue(all(event_type == "delta" for event_type in event_types[:-1]))

        rebuilt_message = "".join(event["delta"] for event in events[:-1])
        self.assertEqual(rebuilt_message, chat_answer.message)
        self.assertEqual(events[-1]["answer"]["message"], chat_answer.message)

    def test_chat_retries_with_fresh_news_when_first_answer_is_fallback(self) -> None:
        payload = {
            "topic": "US Iran conflict",
            "message": "Any latest updates?",
            "history": [{"role": "user", "content": "What changed?"}],
            "timeline_slice": [{
                "id": "event-1",
                "title": "Event",
                "description": "Description",
                "date": "2026-03-27",
                "impact": "high",
                "sentiment": "neutral",
                "playersInvolved": ["player-1"],
                "arcId": "arc-1",
                "citations": [
                    {
                        "source_name": "Reuters",
                        "url": "https://reuters.com/story",
                        "published_at": "2026-03-27T00:00:00Z",
                        "snippet": "Snippet",
                    }
                ],
            }],
        }

        first_answer = ChatAnswer(
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
        second_answer = ChatAnswer(
            message="Fresh retrieval shows a new diplomatic round announced this morning.",
            citations=[
                Citation(
                    source_name="guardian",
                    url="https://theguardian.com/world/story",
                    published_at="2026-03-27T08:00:00Z",
                    snippet="Diplomatic round announced in latest briefing",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.72,
            suggested_followups=["Who initiated the talks?"],
        )

        with patch(
            "app.routers.chat.generate_topic_chat_response",
            side_effect=[first_answer, second_answer],
        ) as mock_chat, patch(
            "app.routers.chat.fetch_news_context",
            return_value={
                "empty_context": True,
                "items": [],
            },
        ), patch(
            "app.routers.chat.fetch_live_news_context",
            return_value={
                "empty_context": False,
                "items": [
                    {
                        "title": "Diplomatic round announced",
                        "url": "https://theguardian.com/world/story",
                        "domain": "theguardian.com",
                        "source": "guardian",
                        "published_at": "2026-03-27T08:00:00Z",
                    }
                ],
            },
        ), patch(
            "app.routers.chat.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ):
            response = self.client.post("/api/chat", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], second_answer.message)
        self.assertEqual(mock_chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
