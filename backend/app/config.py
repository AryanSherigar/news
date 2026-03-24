from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    gemini_api_key: str

    source_policy_strict_allowlist_validation: bool = True
    source_policy_allowed_domains: list[str] = Field(
        default_factory=lambda: [
            "economictimes.indiatimes.com",
            "timesofindia.indiatimes.com",
        ]
    )
    source_policy_allowed_source_ids: list[str] = Field(default_factory=lambda: ["ET", "TOI"])
    source_policy_source_aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "economictimes.indiatimes.com": "ET",
            "timesofindia.indiatimes.com": "TOI",
        }
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
