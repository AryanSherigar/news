from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.schemas import Citation, PlayerProfile, StoryData
from app.services.news_fetcher import ALLOWED_SOURCE_DOMAINS, TRACKING_QUERY_PARAMS


@dataclass
class CitationViolation:
    """Structured citation policy violation metadata."""

    location: str
    url: str
    reason: str
    domain: str | None = None


class SourcePolicyViolationError(ValueError):
    """Raised when model output citations violate ET/TOI source policy."""

    def __init__(self, violations: list[CitationViolation]):
        self.violations = violations
        super().__init__("Citation source policy violation")


def canonicalize_and_validate_source_url(raw_url: str) -> tuple[str | None, str | None, str | None]:
    """Canonicalize citation URL and verify it belongs to the ET/TOI allowlist."""
    if not raw_url:
        return None, None, "missing_url"

    candidate = raw_url.strip()
    parsed = urlparse(candidate)
    if not parsed.scheme and not parsed.netloc:
        parsed = urlparse(f"https://{candidate}")

    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    if not hostname:
        return None, None, "missing_hostname"

    if hostname not in ALLOWED_SOURCE_DOMAINS:
        return None, hostname, "disallowed_domain"

    filtered_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered_key = key.lower()
        if lowered_key.startswith("utm_") or lowered_key in TRACKING_QUERY_PARAMS:
            continue
        filtered_query.append((key, value))

    canonical_url = urlunparse(
        (
            "https",
            hostname,
            parsed.path,
            parsed.params,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )

    return canonical_url, hostname, None


def validate_story_sources_or_raise(story: StoryData) -> StoryData:
    """Validate timeline/insight citations against ET/TOI source policy."""
    violations: list[CitationViolation] = []

    for timeline_index, event in enumerate(story.timeline):
        _validate_citations_in_place(
            event.citations,
            f"timeline[{timeline_index}].citations",
            violations,
        )

    for insight_index, insight in enumerate(story.insights):
        _validate_citations_in_place(
            insight.citations,
            f"insights[{insight_index}].citations",
            violations,
        )

    if violations:
        raise SourcePolicyViolationError(violations)

    return story


def validate_profile_sources_or_raise(profile: PlayerProfile) -> PlayerProfile:
    """Validate all profile citation fields against ET/TOI source policy."""
    violations: list[CitationViolation] = []

    _validate_citations_in_place(profile.citations, "profile.citations", violations)

    for idx, alliance in enumerate(profile.alliances):
        _validate_citations_in_place(
            alliance.citations,
            f"profile.alliances[{idx}].citations",
            violations,
        )

    for idx, conflict in enumerate(profile.conflicts):
        _validate_citations_in_place(
            conflict.citations,
            f"profile.conflicts[{idx}].citations",
            violations,
        )

    for idx, contribution in enumerate(profile.timeline_contributions):
        _validate_citations_in_place(
            contribution.citations,
            f"profile.timeline_contributions[{idx}].citations",
            violations,
        )

    if violations:
        raise SourcePolicyViolationError(violations)

    return profile


def _validate_citations_in_place(
    citations: list[Citation],
    location: str,
    violations: list[CitationViolation],
) -> None:
    for citation_index, citation in enumerate(citations):
        canonical_url, domain, error = canonicalize_and_validate_source_url(citation.url)
        if error:
            violations.append(
                CitationViolation(
                    location=f"{location}[{citation_index}].url",
                    url=citation.url,
                    domain=domain,
                    reason=error,
                )
            )
            continue

        citation.url = canonical_url or citation.url


def violations_to_response_payload(violations: list[CitationViolation]) -> dict[str, Any]:
    """Return a JSON-serializable payload for API error responses."""
    return {
        "message": "Model output included citations outside ET/TOI allowlist.",
        "violations": [
            {
                "location": violation.location,
                "url": violation.url,
                "domain": violation.domain,
                "reason": violation.reason,
            }
            for violation in violations
        ],
    }
