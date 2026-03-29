import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services.news_fetcher import _normalize_and_validate_url, _parse_iso_datetime
from app.services.source_policy import ProviderRetrievalRule, SourcePolicy


class NewsFetcherVectorUtilsTests(unittest.TestCase):
    def test_parse_iso_datetime_accepts_z_suffix(self) -> None:
        parsed = _parse_iso_datetime("2026-03-24T12:34:56Z")
        self.assertEqual(parsed, datetime(2026, 3, 24, 12, 34, 56, tzinfo=timezone.utc))

    def test_parse_iso_datetime_accepts_offset_and_normalizes_utc(self) -> None:
        parsed = _parse_iso_datetime("2026-03-24T17:34:56+05:00")
        self.assertEqual(parsed, datetime(2026, 3, 24, 12, 34, 56, tzinfo=timezone.utc))

    def test_parse_iso_datetime_returns_none_for_invalid(self) -> None:
        self.assertIsNone(_parse_iso_datetime("not-a-date"))

    def test_normalize_and_validate_url_accepts_schemeless_allowlisted_input(self) -> None:
        with patch(
            "app.services.news_fetcher.get_source_policy",
            return_value=_policy_fixture(strict=True),
        ):
            normalized, hostname, error = _normalize_and_validate_url(
                " www.theguardian.com/world?utm_campaign=daily&a=1 "
            )

        self.assertEqual(normalized, "https://theguardian.com/world?a=1")
        self.assertEqual(hostname, "theguardian.com")
        self.assertIsNone(error)


def _policy_fixture(strict: bool) -> SourcePolicy:
    return SourcePolicy(
        allowed_domains=frozenset({"theguardian.com", "reuters.com"}),
        allowed_source_ids=frozenset({"guardian", "reuters"}),
        source_aliases={"theguardian.com": "guardian", "reuters.com": "reuters"},
        strict_allowlist_validation=strict,
        provider_rules={
            "gnews": ProviderRetrievalRule(
                provider="gnews",
                include_domain_filter=True,
                include_source_filter=True,
            )
        },
    )


if __name__ == "__main__":
    unittest.main()
