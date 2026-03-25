from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

from app.config import get_settings


@dataclass(frozen=True)
class EmbeddingResult:
	embedding: list[float]
	input_token_count: int | None = None


class BedrockEmbeddingService:
	"""Wrapper around Bedrock Runtime text embedding calls."""

	def __init__(self) -> None:
		settings = get_settings()
		self.model_id = settings.bedrock_embedding_model_id
		self.dimensions = settings.bedrock_embedding_dimensions
		self.normalize = settings.bedrock_embedding_normalize
		self._client = boto3.client(
			"bedrock-runtime",
			region_name=settings.aws_region,
			config=Config(retries={"max_attempts": settings.opensearch_max_retries, "mode": "adaptive"}),
		)

	def embed_query(self, text: str) -> EmbeddingResult:
		payload: dict[str, Any] = {
			"inputText": text,
			"dimensions": self.dimensions,
			"normalize": self.normalize,
		}
		response = self._client.invoke_model(
			modelId=self.model_id,
			body=json.dumps(payload),
			contentType="application/json",
			accept="application/json",
		)
		body = json.loads(response["body"].read())
		embedding = body.get("embedding")
		if not isinstance(embedding, list):
			raise ValueError("Bedrock embedding response missing 'embedding' list")
		token_count = body.get("inputTextTokenCount")
		if token_count is not None and not isinstance(token_count, int):
			token_count = None
		return EmbeddingResult(embedding=embedding, input_token_count=token_count)

	async def aembed_query(self, text: str) -> EmbeddingResult:
		return await asyncio.to_thread(self.embed_query, text)
