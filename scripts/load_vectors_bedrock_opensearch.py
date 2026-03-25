#!/usr/bin/env python3
"""Load timeline chunks into OpenSearch Serverless with Bedrock Titan embeddings.

Features:
1) Reads chunk JSONL.
2) Generates embeddings with Bedrock Titan Text Embeddings V2.
3) Indexes deterministic vector IDs into one OpenSearch index.
4) Preserves metadata for query-time filters (timeline_id, published_at, source).
5) Writes checkpoint/manifest/dead-letter outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

DEFAULT_INPUT_JSONL = "data/processed/chunks_all_timelines.jsonl"
DEFAULT_CHECKPOINT = "data/processed/embed_checkpoint.json"
DEFAULT_MANIFEST = "data/processed/ingestion_manifest.json"
DEFAULT_DLQ = "data/processed/embed_failures.jsonl"


@dataclass
class AppConfig:
    input_jsonl: Path
    checkpoint_path: Path
    manifest_path: Path
    dead_letter_path: Path
    index_name: str
    batch_size: int
    max_retries: int
    retry_base_seconds: float
    aws_region: str
    bedrock_model_id: str
    bedrock_dimensions: int
    bedrock_normalize: bool
    opensearch_host: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed and index timeline chunks into OpenSearch Serverless")
    parser.add_argument("--input-jsonl", default=DEFAULT_INPUT_JSONL)
    parser.add_argument("--checkpoint-path", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--manifest-path", default=DEFAULT_MANIFEST)
    parser.add_argument("--dead-letter-path", default=DEFAULT_DLQ)
    parser.add_argument("--index-name", default=os.getenv("OPENSEARCH_INDEX_NAME", "timeline_chunks"))
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--aws-region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--bedrock-model-id", default=os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"))
    parser.add_argument("--bedrock-dimensions", type=int, default=int(os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024")))
    parser.add_argument("--bedrock-normalize", action="store_true", default=True)
    parser.add_argument("--no-bedrock-normalize", dest="bedrock_normalize", action="store_false")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s | %(levelname)s | %(message)s")


def build_config(args: argparse.Namespace) -> AppConfig:
    opensearch_host = os.getenv("OPENSEARCH_HOST", "").strip()
    if not opensearch_host:
        raise ValueError("Missing OPENSEARCH_HOST environment variable")
    return AppConfig(
        input_jsonl=Path(args.input_jsonl),
        checkpoint_path=Path(args.checkpoint_path),
        manifest_path=Path(args.manifest_path),
        dead_letter_path=Path(args.dead_letter_path),
        index_name=args.index_name,
        batch_size=args.batch_size,
        max_retries=args.max_retries,
        retry_base_seconds=args.retry_base_seconds,
        aws_region=args.aws_region,
        bedrock_model_id=args.bedrock_model_id,
        bedrock_dimensions=args.bedrock_dimensions,
        bedrock_normalize=args.bedrock_normalize,
        opensearch_host=opensearch_host,
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as infile:
        for line_no, line in enumerate(infile, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON at {path}:{line_no}: {exc}") from exc
    return rows


def compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_vector_id(row: dict[str, Any]) -> str:
    timeline_id = str(row.get("timeline_id", "unknown"))
    url = str(row.get("url", ""))
    chunk_index = str(row.get("chunk_index", "0"))
    text = str(row.get("chunk_text", ""))
    stable_key = f"{timeline_id}|{url}|{chunk_index}|{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    suffix = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:32]
    return f"{timeline_id}:{chunk_index}:{suffix}"


def read_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as infile:
        return json.load(infile)


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as outfile:
        json.dump(payload, outfile, ensure_ascii=False, indent=2)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as outfile:
        for row in rows:
            outfile.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_opensearch_client(config: AppConfig) -> OpenSearch:
    session = boto3.Session(region_name=config.aws_region)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials not found")
    frozen = credentials.get_frozen_credentials()
    auth = AWSV4SignerAuth(frozen, config.aws_region, "aoss")
    return OpenSearch(
        hosts=[{"host": config.opensearch_host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
        max_retries=config.max_retries,
        retry_on_timeout=True,
    )


def ensure_index(client: OpenSearch, config: AppConfig) -> None:
    if client.indices.exists(index=config.index_name):
        return
    mapping = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "embedding": {"type": "knn_vector", "dimension": config.bedrock_dimensions},
                "timeline_id": {"type": "keyword"},
                "domain": {"type": "keyword"},
                "source": {"type": "keyword"},
                "title": {"type": "text"},
                "url": {"type": "keyword"},
                "published_at": {"type": "date"},
                "chunk_index": {"type": "integer"},
                "date_bucket": {"type": "keyword"},
                "chunk_token_count": {"type": "integer"},
                "chunk_text": {"type": "text"},
            }
        },
    }
    client.indices.create(index=config.index_name, body=mapping)


def embed_text(client: Any, config: AppConfig, text: str) -> list[float]:
    body = {
        "inputText": text,
        "dimensions": config.bedrock_dimensions,
        "normalize": config.bedrock_normalize,
    }
    response = client.invoke_model(
        modelId=config.bedrock_model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        raise RuntimeError("Embedding API returned invalid payload")
    return embedding


def index_document(client: OpenSearch, config: AppConfig, row: dict[str, Any], embedding: list[float]) -> None:
    doc_id = deterministic_vector_id(row)
    parsed = urlparse(str(row.get("url") or ""))
    domain = (parsed.hostname or "").lower()
    document = {
        "embedding": embedding,
        "timeline_id": row.get("timeline_id"),
        "domain": domain,
        "source": row.get("source"),
        "title": row.get("title"),
        "url": row.get("url"),
        "published_at": row.get("published_at"),
        "chunk_index": row.get("chunk_index"),
        "date_bucket": row.get("date_bucket"),
        "chunk_token_count": row.get("chunk_token_count"),
        "chunk_text": row.get("chunk_text"),
    }
    client.index(index=config.index_name, id=doc_id, body=document, refresh=False)


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    config = build_config(args)

    if not config.input_jsonl.exists():
        raise FileNotFoundError(f"Input JSONL not found: {config.input_jsonl}")

    rows = load_jsonl(config.input_jsonl)
    dataset_hash = compute_file_sha256(config.input_jsonl)
    checkpoint = read_checkpoint(config.checkpoint_path)
    start_index = int(checkpoint.get("last_success_index", -1)) + 1

    bedrock_client = boto3.client(
        "bedrock-runtime",
        region_name=config.aws_region,
        config=Config(retries={"max_attempts": config.max_retries, "mode": "adaptive"}),
    )
    opensearch_client = build_opensearch_client(config)
    ensure_index(opensearch_client, config)

    attempted = int(checkpoint.get("attempted", 0))
    succeeded = int(checkpoint.get("succeeded", 0))
    failed = int(checkpoint.get("failed", 0))
    started_at = datetime.now(timezone.utc)
    t0 = time.time()

    for idx in range(start_index, len(rows)):
        row = rows[idx]
        attempted += 1
        try:
            embedding = embed_text(bedrock_client, config, str(row.get("chunk_text", "")))
            index_document(opensearch_client, config, row, embedding)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            append_jsonl(
                config.dead_letter_path,
                [{"error": str(exc), "row_index": idx, "record": row}],
            )

        if idx % config.batch_size == 0:
            write_checkpoint(
                config.checkpoint_path,
                {
                    "last_success_index": idx,
                    "attempted": attempted,
                    "succeeded": succeeded,
                    "failed": failed,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    opensearch_client.indices.refresh(index=config.index_name)
    manifest = {
        "input_jsonl": str(config.input_jsonl),
        "dataset_sha256": dataset_hash,
        "vector_store": "opensearch_serverless",
        "index_name": config.index_name,
        "embedding": {
            "model": config.bedrock_model_id,
            "dimensions": config.bedrock_dimensions,
            "normalize": config.bedrock_normalize,
        },
        "totals": {"attempted": attempted, "succeeded": succeeded, "failed": failed},
        "runtime": {
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "seconds": round(time.time() - t0, 3),
        },
    }
    config.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with config.manifest_path.open("w", encoding="utf-8") as outfile:
        json.dump(manifest, outfile, ensure_ascii=False, indent=2)

    logging.info("done attempted=%s succeeded=%s failed=%s", attempted, succeeded, failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
