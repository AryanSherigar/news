from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.schemas import Citation, ChatAnswer, PlayerProfile, StoryData
from app.services.source_policy import TRACKING_QUERY_PARAMS, get_source_policy


@dataclass
class CitationViolation:
    """Structured citation policy violation metadata."""

    location: str
    url: str
    reason: str
    domain: str | None = None


class SourcePolicyViolationError(ValueError):
    """Raised when model output citations violate source policy."""

    def __init__(self, violations: list[CitationViolation]):
        self.violations = violations
        super().__init__("Citation source policy violation")


def canonicalize_and_validate_source_url(raw_url: str) -> tuple[str | None, str | None, str | None]:
    """Canonicalize citation URL and verify it belongs to the configured source policy."""
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

    policy = get_source_policy()
    if policy.strict_allowlist_validation and policy.allowed_domains and hostname not in policy.allowed_domains:
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
    """Validate timeline/insight citations against configured source policy."""
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
    """Validate all profile citation fields against configured source policy."""
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


def validate_chat_sources_or_raise(answer: ChatAnswer) -> ChatAnswer:
    """Validate chat response citations against configured source policy."""
    violations: list[CitationViolation] = []
    _validate_citations_in_place(answer.citations, "chat.citations", violations)

    if violations:
        raise SourcePolicyViolationError(violations)

    return answer


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


def _source_policy_metadata(provider: str | None = None) -> dict[str, Any]:
    policy = get_source_policy()
    selected_provider = (provider or "").strip().lower() or None
    provider_for_filters = selected_provider or "gnews"
    provider_filters = policy.build_filters(provider_for_filters)

    return {
        "provider": selected_provider,
        "configured_providers": sorted(policy.provider_rules),
        "allowed_domains": sorted(policy.allowed_domains),
        "allowed_source_ids": sorted(policy.allowed_source_ids),
        "provider_filters": provider_filters,
    }


def violations_to_response_payload(
    violations: list[CitationViolation],
    provider: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable payload for API error responses."""
    return {
        "message": "Model output included citations outside configured source policy.",
        "source_policy": _source_policy_metadata(provider),
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
