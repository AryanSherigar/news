from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings

TRACKING_QUERY_PARAMS = {
    "gclid",
    "dclid",
    "fbclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "spm",
    "yclid",
}


@dataclass(frozen=True)
class ProviderRetrievalRule:
    """Provider-specific retrieval behavior."""

    provider: str
    query_suffix: str | None = None
    include_domain_filter: bool = True
    include_source_filter: bool = True


@dataclass(frozen=True)
class SourcePolicy:
    """Runtime source policy used by news retrieval and citation validation."""

    allowed_domains: frozenset[str]
    allowed_source_ids: frozenset[str]
    source_aliases: dict[str, str]
    strict_allowlist_validation: bool
    provider_rules: dict[str, ProviderRetrievalRule]

    def get_provider_rule(self, provider: str) -> ProviderRetrievalRule:
        return self.provider_rules.get(provider.lower(), self.provider_rules["gnews"])

    def build_filters(self, provider: str) -> dict[str, list[str]]:
        rule = self.get_provider_rule(provider)
        filters: dict[str, list[str]] = {}

        if rule.include_domain_filter and self.allowed_domains:
            filters["domain_in"] = sorted(self.allowed_domains)

        if rule.include_source_filter and self.allowed_source_ids:
            filters["source_in"] = sorted(self.allowed_source_ids)

        return filters

    def get_query_suffix(self, provider: str) -> str:
        rule = self.get_provider_rule(provider)
        return (rule.query_suffix or "").strip()


@dataclass(frozen=True)
class PromptPolicyContext:
    """Prompt-safe source policy strings injected into runtime templates."""

    allowed_source_policy_label: str
    fallback_text: str
    unsupported_source_behavior: str


def build_prompt_policy_context(policy: SourcePolicy | None = None) -> PromptPolicyContext:
    """Build deterministic prompt context values from runtime source policy settings."""
    settings = get_settings()
    resolved_policy = policy or get_source_policy()
    allowed_source_ids = sorted(resolved_policy.allowed_source_ids)
    allowed_source_policy_label = "/".join(allowed_source_ids) if allowed_source_ids else "configured source allowlist"

    if resolved_policy.strict_allowlist_validation:
        unsupported_source_behavior = (
            "ignore unsupported sources completely and respond only with the fallback text"
        )
    else:
        unsupported_source_behavior = (
            "ignore unsupported sources and continue only with supported evidence; if none remains, use the fallback text"
        )

    return PromptPolicyContext(
        allowed_source_policy_label=allowed_source_policy_label,
        fallback_text=settings.source_policy_fallback_text,
        unsupported_source_behavior=unsupported_source_behavior,
    )


def _default_provider_rules() -> dict[str, ProviderRetrievalRule]:
    return {
        "guardian": ProviderRetrievalRule(provider="guardian", include_domain_filter=False, include_source_filter=True),
        "gdelt": ProviderRetrievalRule(provider="gdelt", include_domain_filter=True, include_source_filter=False),
        "gnews": ProviderRetrievalRule(provider="gnews", include_domain_filter=True, include_source_filter=True),
        "mediastack": ProviderRetrievalRule(provider="mediastack", include_domain_filter=True, include_source_filter=True),
    }


@lru_cache()
def get_source_policy() -> SourcePolicy:
    settings = get_settings()
    return SourcePolicy(
        allowed_domains=frozenset(settings.source_policy_allowed_domains),
        allowed_source_ids=frozenset(settings.source_policy_allowed_source_ids),
        source_aliases={key.lower(): value for key, value in settings.source_policy_source_aliases.items()},
        strict_allowlist_validation=settings.source_policy_strict_allowlist_validation,
        provider_rules=_default_provider_rules(),
    )
