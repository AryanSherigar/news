import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from app.services.news_fetcher import fetch_news_context


class NewsFetcherFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_news_context_falls_back_to_rss_when_vector_has_no_results(self) -> None:
        vector_service = Mock()
        vector_service.enabled = True

        vector_empty = {
            "prompt_context": "[]",
            "items": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "empty_context": True,
            "empty_context_reason": "no_vector_results",
        }
        rss_result = {
            "prompt_context": "[{\"title\":\"Live update\"}]",
            "items": [
                {
                    "title": "Live update",
                    "url": "https://theguardian.com/world/live",
                    "domain": "theguardian.com",
                    "source": "guardian",
                    "provider": "gnews",
                    "published_at": "2026-03-27T00:00:00Z",
                }
            ],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "empty_context": False,
            "empty_context_reason": None,
        }

        with patch("app.services.news_fetcher._get_vector_service", return_value=vector_service), patch(
            "app.services.news_fetcher._fetch_vector_context",
            new=AsyncMock(return_value=vector_empty),
        ), patch(
            "app.services.news_fetcher._fetch_rss_context",
            new=AsyncMock(return_value=rss_result),
        ) as mock_rss:
            result = await fetch_news_context("new topic without vectors")

        self.assertFalse(result["empty_context"])
        self.assertEqual(result["items"][0]["title"], "Live update")
        self.assertEqual(mock_rss.await_count, 1)


if __name__ == "__main__":
    unittest.main()
