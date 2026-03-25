import unittest
from datetime import datetime, timezone

from app.services.vector_search import OpenSearchVectorSearchService, VectorSearchRequest


class VectorSearchFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        # Avoid AWS/client initialization for pure filter tests.
        self.service = OpenSearchVectorSearchService.__new__(OpenSearchVectorSearchService)

    def test_build_filter_clauses_with_timeline_source_and_date_range(self) -> None:
        req = VectorSearchRequest(
            vector=[0.1, 0.2],
            top_k=5,
            timeline_id="budget_2026",
            published_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_to=datetime(2026, 3, 31, tzinfo=timezone.utc),
            domains=["economictimes.indiatimes.com", "timesofindia.indiatimes.com"],
            sources=["ET", "TOI"],
        )

        clauses = self.service._build_filter_clauses(req)

        self.assertEqual(
            clauses,
            {
                "timeline_id": {"$eq": "budget_2026"},
                "domain": {"$in": ["economictimes.indiatimes.com", "timesofindia.indiatimes.com"]},
                "source": {"$in": ["ET", "TOI"]},
                "published_at": {
                    "$gte": "2026-01-01T00:00:00Z",
                    "$lte": "2026-03-31T00:00:00Z",
                },
            },
        )

    def test_build_filter_clauses_empty_when_no_filters(self) -> None:
        req = VectorSearchRequest(vector=[0.1, 0.2], top_k=5)
        clauses = self.service._build_filter_clauses(req)
        self.assertIsNone(clauses)


if __name__ == "__main__":
    unittest.main()
