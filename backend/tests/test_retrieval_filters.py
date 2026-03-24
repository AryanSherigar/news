import unittest

from app.services.news_fetcher import (
    ALLOWED_SOURCE_CODES,
    ALLOWED_SOURCE_DOMAINS,
    build_retrieval_queries,
    validate_retrieval_filter,
)


class RetrievalFilterValidationTests(unittest.TestCase):
    def test_build_retrieval_queries_includes_mandatory_filter_for_every_strategy(self) -> None:
        queries = build_retrieval_queries("india markets")

        for strategy in ("metadata", "vector", "bm25"):
            self.assertIn(strategy, queries)
            filters = queries[strategy]["filters"]
            self.assertEqual(set(filters["domain_in"]), ALLOWED_SOURCE_DOMAINS)
            self.assertEqual(set(filters["source_in"]), ALLOWED_SOURCE_CODES)

    def test_validate_retrieval_filter_raises_if_filter_missing(self) -> None:
        with self.assertRaises(ValueError):
            validate_retrieval_filter({"query": "india markets", "filters": {}})

    def test_validate_retrieval_filter_accepts_source_only_filter(self) -> None:
        validate_retrieval_filter(
            {
                "query": "india markets",
                "filters": {
                    "source_in": sorted(ALLOWED_SOURCE_CODES),
                },
            }
        )


if __name__ == "__main__":
    unittest.main()
