#!/usr/bin/env python3
"""Load timeline chunks into Pinecone with OpenAI embeddings.

Features:
1) Reads chunk JSONL.
2) Batch embeds with retry + checkpointing.
3) Upserts deterministic vector IDs.
4) Supports:
   - Option A (recommended): one namespace + metadata filter by timeline_id.
   - Option B: per-timeline namespace.
5) Writes an ingestion manifest.
6) Writes dead-letter failures to JSONL.
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

DEFAULT_INPUT_JSONL = "data/processed/chunks_all_timelines.jsonl"
DEFAULT_CHECKPOINT = "data/processed/embed_checkpoint.json"
DEFAULT_MANIFEST = "data/processed/ingestion_manifest.json"
DEFAULT_DLQ = "data/processed/embed_failures.jsonl"


@dataclass
class Config:
    input_jsonl: Path
    checkpoint_path: Path
    manifest_path: Path
    dead_letter_path: Path
    batch_size: int
    max_retries: int
    retry_base_seconds: float
    embedding_model: str
    embedding_model_version: str
    embedding_api_url: str
    openai_api_key: str
    pinecone_api_key: str
    pinecone_index_host: str
    pinecone_storage_mode: str
    pinecone_namespace: str


@dataclass
class RetryOutcome:
    payload: dict[str, Any]
    attempts: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed and upsert timeline chunks into Pinecone")
    parser.add_argument("--input-jsonl", default=DEFAULT_INPUT_JSONL)
    parser.add_argument("--checkpoint-path", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--manifest-path", default=DEFAULT_MANIFEST)
    parser.add_argument("--dead-letter-path", default=DEFAULT_DLQ)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--embedding-model", default="text-embedding-3-large")
    parser.add_argument("--embedding-model-version", default="unspecified")
    parser.add_argument("--embedding-api-url", default="https://api.openai.com/v1/embeddings")
    parser.add_argument(
        "--pinecone-storage-mode",
        choices=["single_collection", "timeline_namespace"],
        default="single_collection",
        help="single_collection=Option A, timeline_namespace=Option B",
    )
    parser.add_argument(
        "--pinecone-namespace",
        default="all_timelines",
        help="Namespace used in Option A (single collection mode)",
    )
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


def build_config(args: argparse.Namespace) -> Config:
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    pinecone_api_key = os.getenv("PINECONE_API_KEY", "").strip()
    raw_pinecone_index_host = os.getenv("PINECONE_INDEX_HOST", "")
    pinecone_index_host = normalize_pinecone_index_host(raw_pinecone_index_host)

    if not openai_api_key:
        raise ValueError("Missing OPENAI_API_KEY environment variable")
    if not pinecone_api_key:
        raise ValueError("Missing PINECONE_API_KEY environment variable")
    if not pinecone_index_host:
        raise ValueError("Missing PINECONE_INDEX_HOST environment variable")
    if raw_pinecone_index_host.strip() != pinecone_index_host:
        logging.info("Normalized PINECONE_INDEX_HOST to host-only value: %s", pinecone_index_host)

    return Config(
        input_jsonl=Path(args.input_jsonl),
        checkpoint_path=Path(args.checkpoint_path),
        manifest_path=Path(args.manifest_path),
        dead_letter_path=Path(args.dead_letter_path),
        batch_size=args.batch_size,
        max_retries=args.max_retries,
        retry_base_seconds=args.retry_base_seconds,
        embedding_model=args.embedding_model,
        embedding_model_version=args.embedding_model_version,
        embedding_api_url=args.embedding_api_url,
        openai_api_key=openai_api_key,
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


def request_json_with_retry(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    max_retries: int,
    retry_base_seconds: float,
) -> RetryOutcome:
    encoded = json.dumps(body).encode("utf-8")
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        req = Request(url=url, headers=headers, data=encoded, method="POST")
        try:
            with urlopen(req, timeout=90) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                return RetryOutcome(payload=payload, attempts=attempt)
        except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            sleep_seconds = retry_base_seconds * (2 ** (attempt - 1))
            logging.warning("request failed attempt=%s/%s url=%s retry_in=%.2fs error=%s", attempt, max_retries, url, sleep_seconds, exc)
            time.sleep(sleep_seconds)

    raise RuntimeError(f"Request failed after {max_retries} attempts for {url}: {last_exc}")


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as outfile:
        json.dump(payload, outfile, ensure_ascii=False, indent=2)


def read_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as infile:
        return json.load(infile)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as outfile:
        for row in rows:
            outfile.write(json.dumps(row, ensure_ascii=False) + "\n")


def choose_namespace(config: Config, timeline_id: str) -> str:
    if config.pinecone_storage_mode == "timeline_namespace":
        return timeline_id
    return config.pinecone_namespace


def make_vector_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timeline_id": row.get("timeline_id"),
        "source": row.get("source"),
        "title": row.get("title"),
        "url": row.get("url"),
        "published_at": row.get("published_at"),
        "chunk_index": row.get("chunk_index"),
        "date_bucket": row.get("date_bucket"),
        "chunk_token_count": row.get("chunk_token_count"),
    }


def embed_batch(config: Config, texts: list[str]) -> RetryOutcome:
    return request_json_with_retry(
        url=config.embedding_api_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.openai_api_key}",
        },
        body={"model": config.embedding_model, "input": texts},
        max_retries=config.max_retries,
        retry_base_seconds=config.retry_base_seconds,
    )


def upsert_vectors(config: Config, namespace: str, vectors: list[dict[str, Any]]) -> RetryOutcome:
    return request_json_with_retry(
        url=f"https://{config.pinecone_index_host}/vectors/upsert",
        headers={
            "Content-Type": "application/json",
            "Api-Key": config.pinecone_api_key,
        },
        body={"namespace": namespace, "vectors": vectors},
        max_retries=config.max_retries,
        retry_base_seconds=config.retry_base_seconds,
    )


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as outfile:
        json.dump(manifest, outfile, ensure_ascii=False, indent=2)


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    config = build_config(args)

    if config.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")

    if not config.input_jsonl.exists():
        raise FileNotFoundError(f"Input JSONL not found: {config.input_jsonl}")

    started_at = datetime.now(timezone.utc)
    t0 = time.time()

    dataset_hash = compute_file_sha256(config.input_jsonl)
    rows = load_jsonl(config.input_jsonl)
    checkpoint = read_checkpoint(config.checkpoint_path)
    start_index = int(checkpoint.get("last_success_index", -1)) + 1

    attempted = int(checkpoint.get("attempted", 0))
    succeeded = int(checkpoint.get("succeeded", 0))
    failed = int(checkpoint.get("failed", 0))
    embedding_attempts = int(checkpoint.get("embedding_attempts", 0))
    upsert_attempts = int(checkpoint.get("upsert_attempts", 0))
    detected_model = str(checkpoint.get("embedding_model_detected", ""))

    logging.info("input=%s rows=%s resume_start_index=%s dataset_sha256=%s", config.input_jsonl, len(rows), start_index, dataset_hash)
    logging.info("storage_mode=%s (Option A=single_collection, Option B=timeline_namespace)", config.pinecone_storage_mode)

    for batch_start in range(start_index, len(rows), config.batch_size):
        batch_rows = rows[batch_start : batch_start + config.batch_size]
        attempted += len(batch_rows)

        texts = [str(row.get("chunk_text", "")) for row in batch_rows]

        try:
            embed_result = embed_batch(config, texts)
            embedding_attempts += embed_result.attempts
            detected_model = str(embed_result.payload.get("model") or detected_model)
            embedding_data = embed_result.payload.get("data", [])
            if len(embedding_data) != len(batch_rows):
                raise RuntimeError(
                    f"Embedding response count mismatch batch_start={batch_start} expected={len(batch_rows)} got={len(embedding_data)}"
                )
        except Exception as exc:  # noqa: BLE001
            failed += len(batch_rows)
            dlq_rows = [
                {
                    "error_stage": "embed",
                    "error": str(exc),
                    "batch_start": batch_start,
                    "record": row,
                }
                for row in batch_rows
            ]
            append_jsonl(config.dead_letter_path, dlq_rows)
            logging.error("embed batch failed start=%s size=%s error=%s", batch_start, len(batch_rows), exc)
            write_checkpoint(
                config.checkpoint_path,
                {
                    "last_success_index": batch_start - 1,
                    "attempted": attempted,
                    "succeeded": succeeded,
                    "failed": failed,
                    "embedding_attempts": embedding_attempts,
                    "upsert_attempts": upsert_attempts,
                    "embedding_model_detected": detected_model,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            continue

        vectors_by_namespace: dict[str, list[dict[str, Any]]] = {}
        for row, emb in zip(batch_rows, embedding_data, strict=True):
            timeline_id = str(row.get("timeline_id", "unknown"))
            namespace = choose_namespace(config, timeline_id)
            vector = {
                "id": deterministic_vector_id(row),
                "values": emb["embedding"],
                "metadata": make_vector_metadata(row),
            }
            vectors_by_namespace.setdefault(namespace, []).append(vector)

        failed_rows: list[dict[str, Any]] = []
        successful_count = 0

        for namespace, vectors in vectors_by_namespace.items():
            try:
                upsert_result = upsert_vectors(config, namespace, vectors)
                upsert_attempts += upsert_result.attempts
                successful_count += len(vectors)
            except Exception as exc:  # noqa: BLE001
                failing_ids = {vector["id"] for vector in vectors}
                for row in batch_rows:
                    if deterministic_vector_id(row) in failing_ids:
                        failed_rows.append(
                            {
                                "error_stage": "upsert",
                                "error": str(exc),
                                "batch_start": batch_start,
                                "namespace": namespace,
                                "record": row,
                            }
                        )
                logging.error("upsert failed namespace=%s start=%s size=%s error=%s", namespace, batch_start, len(vectors), exc)

        if failed_rows:
            append_jsonl(config.dead_letter_path, failed_rows)
            failed += len(failed_rows)

        succeeded += successful_count

        write_checkpoint(
            config.checkpoint_path,
            {
                "last_success_index": batch_start + len(batch_rows) - 1,
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": failed,
                "embedding_attempts": embedding_attempts,
                "upsert_attempts": upsert_attempts,
                "embedding_model_detected": detected_model,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        logging.info(
            "batch_complete start=%s size=%s succeeded=%s failed=%s cumulative_attempted=%s",
            batch_start,
            len(batch_rows),
            successful_count,
            len(failed_rows),
            attempted,
        )

    ended_at = datetime.now(timezone.utc)
    runtime_seconds = round(time.time() - t0, 3)

    manifest = {
        "input_jsonl": str(config.input_jsonl),
        "dataset_sha256": dataset_hash,
        "storage_mode": config.pinecone_storage_mode,
        "pinecone_namespace": config.pinecone_namespace,
        "totals": {
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
        },
        "embedding": {
            "model": config.embedding_model,
            "version": config.embedding_model_version,
            "detected_model": detected_model,
        },
        "runtime": {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "seconds": runtime_seconds,
        },
    }
    write_manifest(config.manifest_path, manifest)

    logging.info("done attempted=%s succeeded=%s failed=%s manifest=%s", attempted, succeeded, failed, config.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
