#!/usr/bin/env python3
"""Prepare chunked timeline datasets from scraped article JSONL files.

Pipeline:
1) Clean boilerplate/navigation text from article bodies.
2) Validate minimum body length.
3) Deduplicate by canonical URL, exact content hash, and near-duplicate similarity.
4) Chunk cleaned text into overlapping token windows.
5) Emit timeline chunk files + combined chunk file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIMELINES = ["budget_2026", "us_iran_conflict", "israel_palestine_conflict"]
DEFAULT_INPUT_TEMPLATE = "data/processed/articles_{timeline}.jsonl"
DEFAULT_OUTPUT_TEMPLATE = "data/processed/chunks_{timeline}.jsonl"
DEFAULT_OUTPUT_ALL = "data/processed/chunks_all_timelines.jsonl"


NAV_PATTERNS = [
    r"^\s*home\s*$",
    r"^\s*news\s*$",
    r"^\s*live blog\s*$",
    r"^\s*follow us on\b.*$",
    r"^\s*read more\b.*$",
    r"^\s*also read\b.*$",
    r"^\s*click here\b.*$",
    r"^\s*subscribe\b.*$",
    r"^\s*advertisement\s*$",
    r"^\s*\(with inputs from .*\)\s*$",
]

INLINE_BOILERPLATE_PATTERNS = [
    r"\bdownload\s+our\s+app\b",
    r"\bfollow\s+us\s+on\s+(twitter|x|facebook|instagram|youtube)\b",
    r"\bwatch\s+live\b",
    r"\bfor\s+more\s+updates\b",
    r"\bread\s+also\b",
    r"\bclick\s+here\s+to\s+read\b",
]

WORD_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass
class PrepConfig:
    input_template: str
    output_template: str
    output_all: str
    min_body_chars: int
    min_chunk_tokens: int
    max_chunk_tokens: int
    overlap_tokens: int
    near_dup_threshold: float
    timeline_id: str


@dataclass
class PreparedArticle:
    timeline_id: str
    source: str
    title: str
    url: str
    canonical_url: str
    published_at: str
    date_bucket: str
    body: str
    dedup_hash: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare timeline chunks from article JSONL files")
    parser.add_argument("--timeline-id", default="all", help="Timeline id or 'all'")
    parser.add_argument("--input-template", default=DEFAULT_INPUT_TEMPLATE)
    parser.add_argument("--output-template", default=DEFAULT_OUTPUT_TEMPLATE)
    parser.add_argument("--output-all", default=DEFAULT_OUTPUT_ALL)
    parser.add_argument("--min-body-chars", type=int, default=400)
    parser.add_argument("--min-chunk-tokens", type=int, default=500)
    parser.add_argument("--max-chunk-tokens", type=int, default=900)
    parser.add_argument("--overlap-tokens", type=int, default=100)
    parser.add_argument("--near-dup-threshold", type=float, default=0.95)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s | %(levelname)s | %(message)s")


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text)


def untokenize(tokens: list[str]) -> str:
    text = " ".join(tokens)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text.strip()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    url = re.sub(r"#.*$", "", url)
    url = re.sub(r"\?.*$", "", url)
    return url.rstrip("/")


def parse_date_bucket(published_at: str | None) -> str:
    if not published_at:
        return "unknown"
    raw = published_at.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m")


def strip_boilerplate(body: str) -> str:
    lines = [ln.strip() for ln in body.splitlines()]
    cleaned: list[str] = []

    for line in lines:
        if not line:
            continue
        lowered = line.lower().strip()
        if any(re.match(pat, lowered) for pat in NAV_PATTERNS):
            continue
        line_wo_inline = line
        for pat in INLINE_BOILERPLATE_PATTERNS:
            line_wo_inline = re.sub(pat, "", line_wo_inline, flags=re.IGNORECASE)
        line_wo_inline = re.sub(r"\s+", " ", line_wo_inline).strip()
        if line_wo_inline:
            cleaned.append(line_wo_inline)

    merged = "\n".join(cleaned)
    merged = re.sub(r"\n{2,}", "\n", merged)
    return merged.strip()


def similarity_score(text_a: str, text_b: str) -> float:
    """Compute lightweight near-duplicate similarity with 5-gram Jaccard."""

    def char_ngrams(text: str, n: int = 5) -> set[str]:
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        if len(normalized) < n:
            return {normalized} if normalized else set()
        return {normalized[i : i + n] for i in range(0, len(normalized) - n + 1)}

    a = char_ngrams(text_a)
    b = char_ngrams(text_b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def chunk_tokens(tokens: list[str], min_tokens: int, max_tokens: int, overlap: int) -> list[list[str]]:
    if len(tokens) < min_tokens:
        return []

    chunks: list[list[str]] = []
    step = max_tokens - overlap
    if step <= 0:
        raise ValueError("overlap-tokens must be smaller than max-chunk-tokens")

    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        window = tokens[start:end]

        if len(window) < min_tokens and chunks:
            chunks[-1].extend(window)
            break
        if len(window) < min_tokens and not chunks:
            break

        chunks.append(window)
        if end >= len(tokens):
            break
        start += step

    return chunks


def load_articles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        logging.warning("Input not found: %s", path)
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as infile:
        for line in infile:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                logging.warning("Skipping malformed JSON line in %s", path)
    return rows


def prepare_articles(rows: list[dict[str, Any]], config: PrepConfig) -> list[PreparedArticle]:
    prepared: list[PreparedArticle] = []

    for row in rows:
        raw_body = str(row.get("body") or "")
        cleaned_body = strip_boilerplate(raw_body)
        if len(cleaned_body) < config.min_body_chars:
            continue

        timeline_id = str(row.get("timeline_id") or config.timeline_id)
        source = str(row.get("source") or "")
        title = str(row.get("title") or "")
        url = str(row.get("url") or "")
        canonical = str(row.get("canonical_url") or url)
        published_at = str(row.get("published_at") or "")
        dedup_hash = hashlib.sha256(cleaned_body.encode("utf-8", errors="ignore")).hexdigest()

        prepared.append(
            PreparedArticle(
                timeline_id=timeline_id,
                source=source,
                title=title,
                url=url,
                canonical_url=canonical,
                published_at=published_at,
                date_bucket=parse_date_bucket(published_at),
                body=cleaned_body,
                dedup_hash=dedup_hash,
            )
        )

    return prepared


def deduplicate_articles(articles: list[PreparedArticle], near_dup_threshold: float) -> list[PreparedArticle]:
    canonical_seen: set[str] = set()
    hash_seen: set[str] = set()
    kept: list[PreparedArticle] = []

    for article in articles:
        canonical = normalize_url(article.canonical_url or article.url)
        if canonical and canonical in canonical_seen:
            continue

        if article.dedup_hash in hash_seen:
            continue

        is_near_duplicate = False
        for existing in kept:
            if similarity_score(article.body, existing.body) >= near_dup_threshold:
                is_near_duplicate = True
                break
        if is_near_duplicate:
            continue

        if canonical:
            canonical_seen.add(canonical)
        hash_seen.add(article.dedup_hash)
        kept.append(article)

    return kept


def build_chunk_records(articles: list[PreparedArticle], config: PrepConfig) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for article in articles:
        tokens = tokenize(article.body)
        token_chunks = chunk_tokens(tokens, config.min_chunk_tokens, config.max_chunk_tokens, config.overlap_tokens)

        for idx, token_chunk in enumerate(token_chunks):
            chunk_text = untokenize(token_chunk)
            record = {
                "timeline_id": article.timeline_id,
                "source": article.source,
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at,
                "chunk_index": idx,
                "date_bucket": article.date_bucket,
                "chunk_text": chunk_text,
                "chunk_token_count": len(token_chunk),
            }
            records.append(record)

    return records


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_for_timeline(timeline_id: str, config: PrepConfig) -> tuple[list[PreparedArticle], list[dict[str, Any]]]:
    input_path = Path(config.input_template.format(timeline=timeline_id))
    rows = load_articles(input_path)
    prepared = prepare_articles(rows, config)

    logging.info(
        "timeline=%s loaded=%s after_clean_length_filter=%s",
        timeline_id,
        len(rows),
        len(prepared),
    )

    return prepared, []


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    if args.min_chunk_tokens < 1 or args.max_chunk_tokens < args.min_chunk_tokens:
        raise ValueError("Invalid chunk token bounds")
    if args.overlap_tokens < 0:
        raise ValueError("overlap-tokens must be >= 0")
    if args.timeline_id != "all" and args.timeline_id not in TIMELINES:
        raise ValueError(f"Unknown timeline-id: {args.timeline_id}")

    timeline_ids = TIMELINES if args.timeline_id == "all" else [args.timeline_id]

    all_prepared: list[PreparedArticle] = []
    prepared_by_timeline: dict[str, list[PreparedArticle]] = {}

    for timeline_id in timeline_ids:
        config = PrepConfig(
            input_template=args.input_template,
            output_template=args.output_template,
            output_all=args.output_all,
            min_body_chars=args.min_body_chars,
            min_chunk_tokens=args.min_chunk_tokens,
            max_chunk_tokens=args.max_chunk_tokens,
            overlap_tokens=args.overlap_tokens,
            near_dup_threshold=args.near_dup_threshold,
            timeline_id=timeline_id,
        )
        prepared, _ = run_for_timeline(timeline_id, config)
        prepared_by_timeline[timeline_id] = prepared
        all_prepared.extend(prepared)

    deduped_global = deduplicate_articles(all_prepared, args.near_dup_threshold)
    kept_urls = {article.url for article in deduped_global}

    all_chunk_rows: list[dict[str, Any]] = []

    for timeline_id in timeline_ids:
        config = PrepConfig(
            input_template=args.input_template,
            output_template=args.output_template,
            output_all=args.output_all,
            min_body_chars=args.min_body_chars,
            min_chunk_tokens=args.min_chunk_tokens,
            max_chunk_tokens=args.max_chunk_tokens,
            overlap_tokens=args.overlap_tokens,
            near_dup_threshold=args.near_dup_threshold,
            timeline_id=timeline_id,
        )

        timeline_kept = [article for article in prepared_by_timeline[timeline_id] if article.url in kept_urls]
        timeline_chunks = build_chunk_records(timeline_kept, config)

        output_timeline = Path(args.output_template.format(timeline=timeline_id))
        write_jsonl(output_timeline, timeline_chunks)

        all_chunk_rows.extend(timeline_chunks)
        logging.info(
            "timeline=%s deduped_articles=%s chunks=%s output=%s",
            timeline_id,
            len(timeline_kept),
            len(timeline_chunks),
            output_timeline,
        )

    all_output_path = Path(args.output_all)
    write_jsonl(all_output_path, all_chunk_rows)
    logging.info("all_timelines chunks=%s output=%s", len(all_chunk_rows), all_output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
