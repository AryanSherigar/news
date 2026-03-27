import unittest
from unittest.mock import patch

from app.schemas import ChatAnswer, Citation
from app.services.ai_orchestration import generate_topic_chat_response


class ChatOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_topic_chat_response_invokes_structured_chain_payload(self) -> None:
        expected_answer = ChatAnswer(
            message="Grounded answer",
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
            confidence=0.83,
            suggested_followups=["What changed most recently?"],
        )

        with patch("app.services.ai_orchestration._invoke_with_optional_fallback", return_value=expected_answer) as mock_invoke:
            response = await generate_topic_chat_response(
                topic="US Iran conflict",
                message="What happened this week?",
                history=[{"role": "user", "content": "Give me updates"}],
                story_context={"timeline": [{"id": "e1", "title": "Event"}]},
            )

        self.assertEqual(response.message, "Grounded answer")
        self.assertEqual(response.citations[0].url, "https://reuters.com/story")

        invoke_kwargs = mock_invoke.call_args.kwargs
        self.assertEqual(invoke_kwargs["payload"]["topic"], "US Iran conflict")
        self.assertIn("Give me updates", invoke_kwargs["payload"]["history"])
        self.assertIn("Event", invoke_kwargs["payload"]["story_context"])

    async def test_generate_topic_chat_response_returns_safe_fallback_when_model_parse_fails(self) -> None:
        with patch("app.services.ai_orchestration._invoke_with_optional_fallback", side_effect=ValueError("bad llm json")):
            response = await generate_topic_chat_response(
                topic="US Iran conflict",
                message="What happened this week?",
                history=[{"role": "user", "content": "Give me updates"}],
                story_context={
                    "timeline": [
                        {
                            "id": "e1",
                            "title": "Event",
                            "citations": [
                                {
                                    "source_name": "Reuters",
                                    "url": "https://reuters.com/story",
                                    "published_at": "2026-03-27T00:00:00Z",
                                    "snippet": "Evidence snippet",
                                }
                            ],
                        }
                    ]
                },
            )

        self.assertTrue(response.message)
        self.assertEqual(response.citations[0].url, "https://reuters.com/story")
        self.assertGreaterEqual(response.confidence, 0)


if __name__ == "__main__":
    unittest.main()
