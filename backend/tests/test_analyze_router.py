import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


class AnalyzeRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_analyze_returns_empty_payload_when_quality_check_fails(self) -> None:
        with patch(
            "app.routers.analyze.fetch_news_context",
            new=AsyncMock(
                return_value={
                    "prompt_context": "[]",
                    "items": [],
                    "fetched_at": "2026-03-27T00:00:00Z",
                    "empty_context": False,
                }
            ),
        ), patch(
            "app.routers.analyze.analyze_story",
            new=AsyncMock(side_effect=ValueError("Analyze output failed quality checks: empty timeline/insights")),
        ):
            response = self.client.post("/api/analyze", json={"topic": "US Iran conflict"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["timeline"], [])
        self.assertEqual(body["players"], [])
        self.assertEqual(body["relationships"], [])
        self.assertEqual(body["arcs"], [])
        self.assertEqual(body["insights"], [])


if __name__ == "__main__":
    unittest.main()
