import unittest

from app.services.news_fetcher import build_retrieval_queries, validate_retrieval_filter
from app.services.source_policy import get_source_policy


class RetrievalFilterValidationTests(unittest.TestCase):
    def test_build_retrieval_queries_includes_policy_filter_for_every_strategy(self) -> None:
        queries = build_retrieval_queries("india markets", provider="gnews")
        policy = get_source_policy()

        for strategy in ("metadata", "vector", "bm25"):
            self.assertIn(strategy, queries)
            filters = queries[strategy]["filters"]
            self.assertEqual(set(filters.get("domain_in", [])), policy.allowed_domains)
            self.assertEqual(set(filters.get("source_in", [])), policy.allowed_source_ids)

    def test_validate_retrieval_filter_raises_if_filter_missing(self) -> None:
        policy = get_source_policy()
        expected_filters = policy.build_filters("gnews")
        payload = {"query": "india markets", "filters": expected_filters}
        validate_retrieval_filter(payload, provider="gnews")

    def test_validate_retrieval_filter_accepts_source_only_filter_for_guardian(self) -> None:
        policy = get_source_policy()
        validate_retrieval_filter(
            {
                "query": "india markets",
                "filters": {
                    "source_in": sorted(policy.allowed_source_ids),
                },
            },
            provider="guardian",
        )


if __name__ == "__main__":
    unittest.main()
