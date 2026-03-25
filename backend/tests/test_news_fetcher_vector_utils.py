import unittest
from datetime import datetime, timezone

from app.services.news_fetcher import _parse_iso_datetime


class NewsFetcherVectorUtilsTests(unittest.TestCase):
    def test_parse_iso_datetime_accepts_z_suffix(self) -> None:
        parsed = _parse_iso_datetime("2026-03-24T12:34:56Z")
        self.assertEqual(parsed, datetime(2026, 3, 24, 12, 34, 56, tzinfo=timezone.utc))

    def test_parse_iso_datetime_accepts_offset_and_normalizes_utc(self) -> None:
        parsed = _parse_iso_datetime("2026-03-24T17:34:56+05:00")
        self.assertEqual(parsed, datetime(2026, 3, 24, 12, 34, 56, tzinfo=timezone.utc))

    def test_parse_iso_datetime_returns_none_for_invalid(self) -> None:
        self.assertIsNone(_parse_iso_datetime("not-a-date"))


if __name__ == "__main__":
    unittest.main()
