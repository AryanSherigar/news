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
    source_policy_fallback_text: str = "I don’t have enough allowlisted-source information"

    aws_region: str = "us-east-1"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    bedrock_embedding_dimensions: int = 1024
    bedrock_embedding_normalize: bool = True

    opensearch_host: str = ""
    opensearch_index_name: str = "timeline_chunks"
    opensearch_top_k: int = 12
    opensearch_timeout_seconds: int = 15
    opensearch_max_retries: int = 3

    pinecone_api_key: str = ""
    pinecone_index_host: str = ""
    pinecone_top_k: int = 12
    pinecone_namespace: str = "all_timelines"
    pinecone_use_timeline_namespace: bool = False
    pinecone_timeout_seconds: int = 15
    pinecone_max_retries: int = 3

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
