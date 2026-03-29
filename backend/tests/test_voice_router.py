import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ChatAnswer, Citation


class VoiceRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_voice_session_rejects_uninitialized_utterance(self) -> None:
        with self.client.websocket_connect('/api/voice/chat') as websocket:
            websocket.send_text(json.dumps({"type": "user_utterance", "text": "hello"}))
            response = websocket.receive_json()

        self.assertEqual(response["type"], "error")
        self.assertEqual(response["status"], 400)
        self.assertIn("not initialized", response["detail"])

    def test_voice_session_rejects_when_duplex_disabled(self) -> None:
        disabled_settings = SimpleNamespace(
            voice_duplex_enabled=False,
            voice_sample_rate_hz=16000,
            voice_output_chunk_bytes=3200,
            voice_tts_voice_id="Joanna",
        )
        with patch("app.routers.voice.get_settings", return_value=disabled_settings):
            with self.client.websocket_connect('/api/voice/chat') as websocket:
                response = websocket.receive_json()

        self.assertEqual(response["type"], "error")
        self.assertEqual(response["status"], 503)
        self.assertIn("disabled", response["detail"].lower())

    def test_voice_session_streams_delta_and_final(self) -> None:
        answer = ChatAnswer(
            message="Sanctions expanded this week.",
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
            confidence=0.8,
            suggested_followups=["Who is affected most?"],
        )

        with patch("app.routers.voice.generate_topic_voice_response", return_value=answer), patch(
            "app.routers.voice.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ), patch(
            "app.routers.voice.synthesize_pcm_audio",
            return_value=b"\x00\x00" * 400,
        ):
            with self.client.websocket_connect('/api/voice/chat') as websocket:
                websocket.send_text(
                    json.dumps(
                        {
                            "type": "session_start",
                            "topic": "US Iran conflict",
                            "timeline_slice": [
                                {
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
                                }
                            ],
                            "history": [],
                        }
                    )
                )

                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "session_ready")

                websocket.send_text(json.dumps({"type": "user_utterance", "text": "What changed this week?"}))

                first_delta_seen = False
                final = None
                for _ in range(80):
                    event = websocket.receive()
                    if event.get("bytes") is not None:
                        continue

                    text = event.get("text")
                    if not text:
                        continue

                    parsed = json.loads(text)
                    if parsed.get("type") == "assistant_delta":
                        first_delta_seen = True
                    if parsed.get("type") == "assistant_final":
                        final = parsed
                        break

                self.assertTrue(first_delta_seen)
                self.assertIsNotNone(final)
                assert final is not None
                self.assertEqual(final["answer"]["message"], answer.message)
                self.assertEqual(final["answer"]["citations"][0]["url"], "https://reuters.com/story")
                self.assertIn("metrics", final)
                self.assertIn("final_latency_ms", final["metrics"])


if __name__ == "__main__":
    unittest.main()
