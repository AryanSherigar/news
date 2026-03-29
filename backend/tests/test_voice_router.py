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
            voice_stt_silence_timeout_ms=900,
            voice_stt_max_silence_ms=12000,
            voice_stt_partial_timeout_ms=2500,
            voice_stt_reconnect_backoff_ms=350,
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

    def test_voice_session_supports_repeated_turns_without_reconnect(self) -> None:
        first_answer = ChatAnswer(
            message="First turn response.",
            citations=[
                Citation(
                    source_name="Reuters",
                    url="https://reuters.com/first",
                    published_at="2026-03-27T00:00:00Z",
                    snippet="First snippet",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.9,
            suggested_followups=[],
        )
        second_answer = ChatAnswer(
            message="Second turn response.",
            citations=[
                Citation(
                    source_name="AP",
                    url="https://apnews.com/second",
                    published_at="2026-03-28T00:00:00Z",
                    snippet="Second snippet",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.9,
            suggested_followups=[],
        )

        with patch("app.routers.voice.generate_topic_voice_response", side_effect=[first_answer, second_answer]), patch(
            "app.routers.voice.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ), patch(
            "app.routers.voice.synthesize_pcm_audio",
            return_value=b"\x00\x00" * 200,
        ):
            with self.client.websocket_connect('/api/voice/chat') as websocket:
                websocket.send_text(
                    json.dumps(
                        {
                            "type": "session_start",
                            "topic": "US Iran conflict",
                            "timeline_slice": [{"id": "event-1"}],
                            "history": [],
                        }
                    )
                )
                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "session_ready")
                session_id = ready["session_id"]

                turn_ids: list[str] = []
                for utterance, expected_text in [
                    ("Give me the latest update.", first_answer.message),
                    ("What changed after that?", second_answer.message),
                ]:
                    websocket.send_text(json.dumps({"type": "user_utterance", "text": utterance}))
                    for _ in range(100):
                        event = websocket.receive()
                        text = event.get("text")
                        if not text:
                            continue
                        parsed = json.loads(text)
                        if parsed.get("type") == "assistant_final":
                            self.assertEqual(parsed["answer"]["message"], expected_text)
                            self.assertEqual(parsed["session_id"], session_id)
                            turn_ids.append(parsed["turn_id"])
                            break

                self.assertEqual(len(turn_ids), 2)
                self.assertNotEqual(turn_ids[0], turn_ids[1])

    def test_voice_session_barge_in_cancels_active_turn_and_acknowledges(self) -> None:
        answer = ChatAnswer(
            message="Long audio answer for interruption.",
            citations=[
                Citation(
                    source_name="Reuters",
                    url="https://reuters.com/long-audio",
                    published_at="2026-03-27T00:00:00Z",
                    snippet="Audio snippet",
                )
            ],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.85,
            suggested_followups=[],
        )

        with patch("app.routers.voice.generate_topic_voice_response", return_value=answer), patch(
            "app.routers.voice.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ), patch(
            "app.routers.voice.synthesize_pcm_audio",
            return_value=b"\x00\x00" * 1200,
        ):
            with self.client.websocket_connect('/api/voice/chat') as websocket:
                websocket.send_text(
                    json.dumps(
                        {
                            "type": "session_start",
                            "topic": "US Iran conflict",
                            "timeline_slice": [{"id": "event-1"}],
                            "history": [],
                        }
                    )
                )
                self.assertEqual(websocket.receive_json()["type"], "session_ready")

                websocket.send_text(json.dumps({"type": "user_utterance", "text": "Tell me in detail"}))
                websocket.send_text(json.dumps({"type": "barge_in", "reason": "user_interrupt"}))
                websocket.send_text(json.dumps({"type": "user_utterance", "text": "Start over quickly"}))
                saw_ack = False
                new_final = None
                canceled_turn_id = None
                for _ in range(120):
                    event = websocket.receive()
                    text = event.get("text")
                    if not text:
                        continue
                    payload = json.loads(text)
                    if payload.get("type") == "barge_in_ack":
                        saw_ack = True
                        self.assertEqual(payload.get("reason"), "user_interrupt")
                        canceled_turn_id = payload.get("turn_id")
                    if payload.get("type") == "assistant_final":
                        new_final = payload
                        break

                self.assertTrue(saw_ack)
                self.assertIsNotNone(new_final)
                if canceled_turn_id:
                    self.assertNotEqual(new_final.get("turn_id"), canceled_turn_id)

    def test_voice_binary_audio_emits_user_final_and_autoruns_turn(self) -> None:
        answer = ChatAnswer(
            message="Here is what happened.",
            citations=[Citation(source_name="AP", url="https://example.com", published_at="2026-03-27T00:00:00Z", snippet="snippet")],
            outside_topic=False,
            outside_topic_note=None,
            confidence=0.8,
            suggested_followups=[],
        )

        with patch("app.routers.voice.generate_topic_voice_response", return_value=answer), patch(
            "app.routers.voice.validate_chat_sources_or_raise",
            side_effect=lambda value: value,
        ), patch(
            "app.routers.voice.synthesize_pcm_audio",
            return_value=b"\x00\x00" * 200,
        ):
            with self.client.websocket_connect('/api/voice/chat') as websocket:
                websocket.send_text(
                    json.dumps(
                        {
                            "type": "session_start",
                            "topic": "US Iran conflict",
                            "timeline_slice": [{"id": "event-1"}],
                            "history": [],
                        }
                    )
                )

                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "session_ready")

                websocket.send_bytes((b"\xff\x7f" * 600))
                websocket.send_bytes((b"\x00\x00" * 1200))

                saw_user_final = False
                saw_assistant_final = False
                for _ in range(100):
                    event = websocket.receive()
                    text = event.get("text")
                    if not text:
                        continue
                    payload = json.loads(text)
                    if payload.get("type") == "user_final":
                        saw_user_final = True
                    if payload.get("type") == "assistant_final":
                        saw_assistant_final = True
                        break

                self.assertTrue(saw_user_final)
                self.assertTrue(saw_assistant_final)

    def test_voice_binary_audio_ingestion_emits_interim_before_final(self) -> None:
        with self.client.websocket_connect('/api/voice/chat') as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "session_start",
                        "topic": "US Iran conflict",
                        "timeline_slice": [{"id": "event-1"}],
                        "history": [],
                    }
                )
            )
            self.assertEqual(websocket.receive_json()["type"], "session_ready")

            websocket.send_bytes((b"\xff\x7f" * 700))
            websocket.send_bytes((b"\xff\x7f" * 700))
            websocket.send_bytes((b"\x00\x00" * 1200))

            saw_user_interim = False
            saw_user_final = False
            for _ in range(80):
                payload = json.loads(websocket.receive_text())
                if payload.get("type") == "user_interim":
                    saw_user_interim = True
                if payload.get("type") == "user_final":
                    saw_user_final = True
                    self.assertTrue(payload.get("end_of_utterance"))
                    self.assertIn("bytes", payload.get("text", ""))
                    break

            self.assertTrue(saw_user_interim)
            self.assertTrue(saw_user_final)


if __name__ == "__main__":
    unittest.main()
