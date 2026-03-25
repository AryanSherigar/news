from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.config import get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorSearchRequest:
    vector: list[float]
    top_k: int
    timeline_id: str | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    domains: list[str] | None = None
    sources: list[str] | None = None


class OpenSearchVectorSearchService:
    """Pinecone vector search with metadata filters.

    Kept under the existing class name to avoid changing callers.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.pinecone_api_key.strip()
        self.host = self._normalize_host(settings.pinecone_index_host)
        self.default_top_k = settings.pinecone_top_k
        self.namespace = settings.pinecone_namespace.strip() or "all_timelines"
        self.use_timeline_namespace = settings.pinecone_use_timeline_namespace
        self.timeout_seconds = settings.pinecone_timeout_seconds
        self.max_retries = settings.pinecone_max_retries

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.host)

    def _normalize_host(self, raw_host: str) -> str:
        candidate = raw_host.strip()
        if not candidate:
            return ""
        if "://" in candidate:
            parsed = urlparse(candidate)
            candidate = parsed.netloc or parsed.path
        return candidate.strip().strip("/")

    def _build_filter_clauses(self, request: VectorSearchRequest) -> dict[str, Any] | None:
        clauses: dict[str, Any] = {}
        if request.timeline_id:
            clauses["timeline_id"] = {"$eq": request.timeline_id}
        if request.domains:
            clauses["domain"] = {"$in": request.domains}
        if request.sources:
            clauses["source"] = {"$in": request.sources}
        if request.published_from or request.published_to:
            range_query: dict[str, str] = {}
            if request.published_from:
                range_query["$gte"] = request.published_from.isoformat().replace("+00:00", "Z")
            if request.published_to:
                range_query["$lte"] = request.published_to.isoformat().replace("+00:00", "Z")
            clauses["published_at"] = range_query
        return clauses or None

    def _request_json_with_retry(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {}
        url = f"https://{self.host}{path}"
        body = json.dumps(payload).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            req = Request(
                url=url,
                headers={
                    "Api-Key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                data=body,
                method="POST",
            )
            try:
                with urlopen(req, timeout=self.timeout_seconds) as response:  # nosec B310
                    parsed = json.loads(response.read().decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise RuntimeError("Invalid Pinecone response payload")
                    return parsed
            except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.max_retries:
                    break
        raise RuntimeError(f"Pinecone request failed for {url}: {last_exc}")

    def _namespace_for_request(self, request: VectorSearchRequest) -> str:
        if self.use_timeline_namespace and request.timeline_id:
            return request.timeline_id
        return self.namespace

    def search(self, request: VectorSearchRequest) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        filter_clauses = self._build_filter_clauses(request)
        top_k = request.top_k or self.default_top_k
        query_payload: dict[str, Any] = {
            "vector": request.vector,
            "topK": top_k,
            "includeMetadata": True,
            "namespace": self._namespace_for_request(request),
        }
        if filter_clauses:
            query_payload["filter"] = filter_clauses

        response = self._request_json_with_retry("/query", query_payload)
        matches = response.get("matches", [])
        if not isinstance(matches, list):
            return []

        hits: list[dict[str, Any]] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            metadata = match.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            hits.append(
                {
                    "_id": match.get("id"),
                    "_score": match.get("score"),
                    "_source": metadata,
                }
            )
        return hits

    async def asearch(self, request: VectorSearchRequest) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.search, request)
