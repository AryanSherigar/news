#!/usr/bin/env python3
"""Load timeline chunks into Pinecone with Bedrock Titan embeddings.

Features:
1) Reads chunk JSONL.
2) Generates embeddings with Bedrock Titan Text Embeddings V2.
3) Upserts deterministic vector IDs into Pinecone.
4) Supports single-namespace or per-timeline namespace storage.
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
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config

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
    batch_size: int
    max_retries: int
    retry_base_seconds: float
    aws_region: str
    bedrock_model_id: str
    bedrock_dimensions: int
    bedrock_normalize: bool
    pinecone_api_key: str
    pinecone_index_host: str
    pinecone_storage_mode: str
    pinecone_namespace: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed and upsert timeline chunks into Pinecone")
    parser.add_argument("--input-jsonl", default=DEFAULT_INPUT_JSONL)
    parser.add_argument("--checkpoint-path", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--manifest-path", default=DEFAULT_MANIFEST)
    parser.add_argument("--dead-letter-path", default=DEFAULT_DLQ)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--aws-region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--bedrock-model-id", default=os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"))
    parser.add_argument("--bedrock-dimensions", type=int, default=int(os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024")))
    parser.add_argument("--bedrock-normalize", action="store_true", default=True)
    parser.add_argument("--no-bedrock-normalize", dest="bedrock_normalize", action="store_false")
    parser.add_argument(
        "--pinecone-storage-mode",
        choices=["single_collection", "timeline_namespace"],
        default="single_collection",
        help="single_collection uses --pinecone-namespace; timeline_namespace uses each row timeline_id.",
    )
    parser.add_argument("--pinecone-namespace", default=os.getenv("PINECONE_NAMESPACE", "all_timelines"))
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s | %(levelname)s | %(message)s")


def normalize_pinecone_index_host(raw_host: str) -> str:
    candidate = raw_host.strip()
    if not candidate:
        return ""
    if "://" in candidate:
        parsed = urlparse(candidate)
        candidate = parsed.netloc or parsed.path
    return candidate.strip().strip("/")


def build_config(args: argparse.Namespace) -> AppConfig:
    pinecone_api_key = os.getenv("PINECONE_API_KEY", "").strip()
    pinecone_index_host = normalize_pinecone_index_host(os.getenv("PINECONE_INDEX_HOST", ""))
    if not pinecone_api_key:
        raise ValueError("Missing PINECONE_API_KEY environment variable")
    if not pinecone_index_host:
        raise ValueError("Missing PINECONE_INDEX_HOST environment variable")

    return AppConfig(
        input_jsonl=Path(args.input_jsonl),
        checkpoint_path=Path(args.checkpoint_path),
        manifest_path=Path(args.manifest_path),
        dead_letter_path=Path(args.dead_letter_path),
        batch_size=args.batch_size,
        max_retries=args.max_retries,
        retry_base_seconds=args.retry_base_seconds,
        aws_region=args.aws_region,
        bedrock_model_id=args.bedrock_model_id,
        bedrock_dimensions=args.bedrock_dimensions,
        bedrock_normalize=args.bedrock_normalize,
        pinecone_api_key=pinecone_api_key,
        pinecone_index_host=pinecone_index_host,
        pinecone_storage_mode=args.pinecone_storage_mode,
        pinecone_namespace=args.pinecone_namespace,
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


def choose_namespace(config: AppConfig, timeline_id: str) -> str:
    if config.pinecone_storage_mode == "timeline_namespace":
        return timeline_id or config.pinecone_namespace
    return config.pinecone_namespace


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


def request_json_with_retry(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    max_retries: int,
    retry_base_seconds: float,
) -> dict[str, Any]:
    encoded = json.dumps(body).encode("utf-8")
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        req = Request(url=url, headers=headers, data=encoded, method="POST")
        try:
            with urlopen(req, timeout=90) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("Unexpected response payload")
                return payload
        except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            sleep_seconds = retry_base_seconds * (2 ** (attempt - 1))
            logging.warning(
                "request failed attempt=%s/%s url=%s retry_in=%.2fs error=%s",
                attempt,
                max_retries,
                url,
                sleep_seconds,
                exc,
            )
            time.sleep(sleep_seconds)

    raise RuntimeError(f"Request failed after {max_retries} attempts for {url}: {last_exc}")


def upsert_vectors(config: AppConfig, namespace: str, vectors: list[dict[str, Any]]) -> None:
    request_json_with_retry(
        url=f"https://{config.pinecone_index_host}/vectors/upsert",
        headers={
            "Api-Key": config.pinecone_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body={"vectors": vectors, "namespace": namespace},
        max_retries=config.max_retries,
        retry_base_seconds=config.retry_base_seconds,
    )


def make_vector_payload(row: dict[str, Any], embedding: list[float]) -> dict[str, Any]:
    parsed = urlparse(str(row.get("url") or ""))
    domain = (parsed.hostname or "").lower()
    return {
        "id": deterministic_vector_id(row),
        "values": embedding,
        "metadata": {
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
        },
    }


def flush_pending(config: AppConfig, pending: dict[str, list[dict[str, Any]]]) -> int:
    total = 0
    for namespace, vectors in pending.items():
        if not vectors:
            continue
        upsert_vectors(config, namespace, vectors)
        total += len(vectors)
    return total


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

    attempted = int(checkpoint.get("attempted", 0))
    succeeded = int(checkpoint.get("succeeded", 0))
    failed = int(checkpoint.get("failed", 0))
    started_at = datetime.now(timezone.utc)
    t0 = time.time()

    pending: dict[str, list[dict[str, Any]]] = {}

    for idx in range(start_index, len(rows)):
        row = rows[idx]
        attempted += 1
        try:
            embedding = embed_text(bedrock_client, config, str(row.get("chunk_text", "")))
            namespace = choose_namespace(config, str(row.get("timeline_id", "")))
            pending.setdefault(namespace, []).append(make_vector_payload(row, embedding))

            ready = sum(len(v) for v in pending.values())
            if ready >= config.batch_size:
                flushed = flush_pending(config, pending)
                succeeded += flushed
                pending = {}
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
                    "dataset_hash": dataset_hash,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logging.info("progress idx=%s/%s attempted=%s succeeded=%s failed=%s", idx + 1, len(rows), attempted, succeeded, failed)

    if pending:
        flushed = flush_pending(config, pending)
        succeeded += flushed

    ended_at = datetime.now(timezone.utc)
    duration_sec = round(time.time() - t0, 3)
    manifest = {
        "input_jsonl": str(config.input_jsonl),
        "rows_total": len(rows),
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "aws_region": config.aws_region,
        "bedrock_model_id": config.bedrock_model_id,
        "bedrock_dimensions": config.bedrock_dimensions,
        "bedrock_normalize": config.bedrock_normalize,
        "pinecone_index_host": config.pinecone_index_host,
        "pinecone_storage_mode": config.pinecone_storage_mode,
        "pinecone_namespace": config.pinecone_namespace,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_sec": duration_sec,
        "dataset_sha256": dataset_hash,
        "checkpoint_path": str(config.checkpoint_path),
        "dead_letter_path": str(config.dead_letter_path),
    }
    config.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with config.manifest_path.open("w", encoding="utf-8") as outfile:
        json.dump(manifest, outfile, ensure_ascii=False, indent=2)

    write_checkpoint(
        config.checkpoint_path,
        {
            "last_success_index": len(rows) - 1,
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "dataset_hash": dataset_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    logging.info("done attempted=%s succeeded=%s failed=%s duration_sec=%s", attempted, succeeded, failed, duration_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
