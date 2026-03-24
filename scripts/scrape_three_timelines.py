#!/usr/bin/env python3
"""Provider-driven scraper for three timeline datasets.

This pipeline ingests payloads from configured providers, maps those payloads to
a canonical article/event JSONL schema, and preserves compatibility with the
existing downstream dedup/chunk/embedding scripts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from providers import fetch_gdelt, fetch_gnews, fetch_guardian, fetch_mediastack

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

CONFIG_PATH = Path("configs/timeline_queries.yaml")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


@dataclass
class ScrapeConfig:
    timeline_id: str
    start_date: str
    end_date: str
    target_count: int
    resume: bool
    providers: list[dict[str, Any]]
    min_delay: float = 0.5
    max_delay: float = 1.6
    rate_limit_seconds: float = 0.4
    max_retries: int = 3


class RateLimiter:
    def __init__(self, minimum_interval: float) -> None:
        self.minimum_interval = minimum_interval
        self._last_call_ts = 0.0

    def wait(self) -> None:
        elapsed = time.time() - self._last_call_ts
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)
        self._last_call_ts = time.time()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape timeline articles via provider adapters")
    parser.add_argument("--timeline-id", default="all", help="Timeline id or all")
    parser.add_argument("--start-date", help="Override timeline start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Override timeline end date (YYYY-MM-DD)")
    parser.add_argument("--target-count", type=int, default=120)
    parser.add_argument("--resume", action="store_true", help="Resume from existing outputs")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to provider/timeline YAML config")
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def random_delay(config: ScrapeConfig) -> None:
    time.sleep(random.uniform(config.min_delay, config.max_delay))


def fetch_text(url: str, *, retries: int, timeout: int = 20, limiter: RateLimiter | None = None) -> str:
    backoff = 1.3
    for attempt in range(1, retries + 1):
        try:
            if limiter:
                limiter.wait()
            req = Request(url=url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=timeout) as resp:  # nosec B310
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            if attempt >= retries:
                raise RuntimeError(f"Fetch failed after {retries} attempts: {url} ({exc})") from exc
            sleep_s = backoff * attempt + random.uniform(0.1, 0.6)
            logging.warning("Retrying (%s/%s) %s in %.2fs", attempt, retries, url, sleep_s)
            time.sleep(sleep_s)
    raise RuntimeError(f"Unreachable fetch state for {url}")


def load_timeline_config(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required: install with `pip install pyyaml`")
    with path.open("r", encoding="utf-8") as infile:
        data = yaml.safe_load(infile)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid timeline config (expected mapping): {path}")
    return data


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url = re.sub(r"#.*$", "", url)
    return url


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def parse_json_ld(html: str) -> list[dict[str, Any]]:
    blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    parsed: list[dict[str, Any]] = []
    for block in blocks:
        raw = block.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
            try:
                obj = json.loads(cleaned)
            except json.JSONDecodeError:
                continue
        if isinstance(obj, list):
            parsed.extend([x for x in obj if isinstance(x, dict)])
        elif isinstance(obj, dict):
            parsed.append(obj)
    return parsed


def extract_article_from_ld(ld_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    def as_str(val: Any) -> str | None:
        return val if isinstance(val, str) and val.strip() else None

    for item in ld_items:
        t = item.get("@type")
        types: list[str] = []
        if isinstance(t, list):
            types = [str(x).lower() for x in t]
        elif isinstance(t, str):
            types = [t.lower()]
        if not any(x in types for x in ["newsarticle", "article", "reportage"]):
            continue

        author_val = item.get("author")
        author = None
        if isinstance(author_val, dict):
            author = as_str(author_val.get("name"))
        elif isinstance(author_val, list) and author_val and isinstance(author_val[0], dict):
            author = as_str(author_val[0].get("name"))
        elif isinstance(author_val, str):
            author = author_val

        canonical = as_str(item.get("mainEntityOfPage"))
        if isinstance(item.get("mainEntityOfPage"), dict):
            canonical = as_str(item["mainEntityOfPage"].get("@id"))

        return {
            "title": as_str(item.get("headline")) or as_str(item.get("name")),
            "published": as_str(item.get("datePublished")),
            "body": as_str(item.get("articleBody")),
            "author": author,
            "section": as_str(item.get("articleSection")),
            "canonical": canonical,
        }
    return None


def normalize_published_at(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(raw)
        except (ValueError, TypeError):
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_url_section(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "/"
    return "/" + path.split("/")[0]


def extract_article_from_url(url: str, config: ScrapeConfig, limiter: RateLimiter) -> dict[str, Any] | None:
    html = fetch_text(url, retries=config.max_retries, limiter=limiter)
    structured = extract_article_from_ld(parse_json_ld(html))
    if not structured:
        return None
    body = structured.get("body") or ""
    if len(body.split()) < 80:
        return None
    return {
        "title": structured.get("title"),
        "published_at": normalize_published_at(structured.get("published")),
        "body": body,
        "author": structured.get("author"),
        "section": structured.get("section"),
        "canonical_url": structured.get("canonical") or url,
    }


def _provider_env_key(provider_name: str) -> str | None:
    mapping = {
        "guardian": "GUARDIAN_API_KEY",
        "gnews": "GNEWS_API_KEY",
        "mediastack": "MEDIASTACK_API_KEY",
    }
    return mapping.get(provider_name)


def fetch_provider_records(config: ScrapeConfig, provider: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(provider.get("name") or "").strip().lower()
    queries = [str(q) for q in provider.get("queries", []) if str(q).strip()]
    if not queries:
        return []

    records: list[dict[str, Any]] = []
    for query in queries:
        try:
            if name == "guardian":
                api_key = os.getenv(_provider_env_key(name) or "", "test")
                records.extend(
                    fetch_guardian(
                        query=query,
                        start_date=config.start_date,
                        end_date=config.end_date,
                        api_key=api_key,
                        page_size=int(provider.get("page_size", 50)),
                        max_pages=int(provider.get("max_pages", 2)),
                    )
                )
            elif name == "gdelt":
                records.extend(
                    fetch_gdelt(
                        query=query,
                        start_date=config.start_date,
                        end_date=config.end_date,
                        max_records=int(provider.get("max_records", 75)),
                    )
                )
            elif name == "gnews":
                api_key = os.getenv(_provider_env_key(name) or "", "")
                if not api_key:
                    logging.info("Skipping gnews (missing GNEWS_API_KEY)")
                    continue
                records.extend(
                    fetch_gnews(
                        query=query,
                        start_date=config.start_date,
                        end_date=config.end_date,
                        api_key=api_key,
                        max_records=int(provider.get("max_records", 50)),
                    )
                )
            elif name == "mediastack":
                api_key = os.getenv(_provider_env_key(name) or "", "")
                if not api_key:
                    logging.info("Skipping mediastack (missing MEDIASTACK_API_KEY)")
                    continue
                records.extend(
                    fetch_mediastack(
                        query=query,
                        start_date=config.start_date,
                        end_date=config.end_date,
                        api_key=api_key,
                        max_records=int(provider.get("max_records", 50)),
                    )
                )
            else:
                logging.warning("Unknown provider: %s", name)
        except Exception as exc:  # noqa: BLE001
            logging.error("Provider fetch failed provider=%s query=%s err=%s", name, query, exc)

    return records


def map_provider_payload(
    *, timeline_id: str, payload: dict[str, Any], config: ScrapeConfig, limiter: RateLimiter
) -> dict[str, Any] | None:
    provider = str(payload.get("_provider") or "unknown")
    source = provider

    if provider == "guardian":
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        body = str(fields.get("bodyText") or "").strip()
        url = normalize_url(str(payload.get("webUrl") or ""))
        title = str(fields.get("headline") or payload.get("webTitle") or "").strip()
        published = normalize_published_at(str(payload.get("webPublicationDate") or ""))
        author = str(fields.get("byline") or "").strip() or None
        section = str(fields.get("sectionName") or payload.get("sectionName") or "").strip() or None
        canonical_url = url
    elif provider == "gdelt":
        url = normalize_url(str(payload.get("url") or ""))
        title = str(payload.get("title") or "").strip()
        published = normalize_published_at(str(payload.get("seendate") or ""))
        section = str(payload.get("sourcecountry") or "").strip() or None
        author = None
        source = str(payload.get("domain") or "gdelt").strip() or "gdelt"
        snippet = str(payload.get("snippet") or "").strip()
        body = "\n\n".join([x for x in [title, snippet] if x]).strip()
        canonical_url = url
    elif provider == "gnews":
        src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        source = str(src.get("name") or "gnews").strip() or "gnews"
        url = normalize_url(str(payload.get("url") or ""))
        title = str(payload.get("title") or "").strip()
        published = normalize_published_at(str(payload.get("publishedAt") or ""))
        author = str(payload.get("author") or "").strip() or None
        section = None
        description = str(payload.get("description") or "").strip()
        content = str(payload.get("content") or "").strip()
        body = "\n\n".join([x for x in [description, content] if x]).strip()
        canonical_url = url
    elif provider == "mediastack":
        source = str(payload.get("source") or "mediastack").strip() or "mediastack"
        url = normalize_url(str(payload.get("url") or ""))
        title = str(payload.get("title") or "").strip()
        published = normalize_published_at(str(payload.get("published_at") or ""))
        author = str(payload.get("author") or "").strip() or None
        section = str(payload.get("category") or "").strip() or None
        description = str(payload.get("description") or "").strip()
        body = "\n\n".join([x for x in [title, description] if x]).strip()
        canonical_url = url
    else:
        return None

    if not url:
        return None

    if len(body.split()) < 120:
        try:
            extracted = extract_article_from_url(url, config, limiter)
        except Exception:  # noqa: BLE001
            extracted = None
        if extracted:
            title = extracted.get("title") or title
            published = extracted.get("published_at") or published
            body = extracted.get("body") or body
            author = extracted.get("author") or author
            section = extracted.get("section") or section
            canonical_url = extracted.get("canonical_url") or canonical_url

    if len(body.split()) < 40:
        return None

    published = published or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    content_hash = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()

    return {
        "timeline_id": timeline_id,
        "source": source,
        "provider": provider,
        "provider_query": payload.get("_provider_query"),
        "url": url,
        "canonical_url": canonical_url,
        "title": title or None,
        "published_at": published,
        "author": author,
        "section": section,
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "content_hash": content_hash,
        "language": "en",
        "is_paywalled": False,
        "url_section": extract_url_section(url),
        "body": body,
    }


def phase_a_provider_ingestion(config: ScrapeConfig, candidate_path: Path) -> list[dict[str, Any]]:
    seen = set()
    candidates: list[dict[str, Any]] = []

    for provider in config.providers:
        records = fetch_provider_records(config, provider)
        provider_name = str(provider.get("name") or "unknown")
        for payload in records:
            url = normalize_url(str(payload.get("webUrl") or payload.get("url") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                {
                    "timeline_id": config.timeline_id,
                    "provider": provider_name,
                    "provider_query": payload.get("_provider_query"),
                    "url": url,
                    "discovered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "payload": payload,
                }
            )

    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("w", encoding="utf-8") as outfile:
        for row in candidates:
            outfile.write(json.dumps(row, ensure_ascii=False) + "\n")

    logging.info("Saved %s provider candidates -> %s", len(candidates), candidate_path)
    return candidates


def phase_b_map_and_dedup(
    config: ScrapeConfig,
    candidates: list[dict[str, Any]],
    article_path: Path,
    failure_path: Path,
    limiter: RateLimiter,
) -> tuple[int, int]:
    articles: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    if config.resume and article_path.exists():
        with article_path.open("r", encoding="utf-8") as infile:
            for line in infile:
                if line.strip():
                    articles.append(json.loads(line))

    canonical_seen = {a.get("canonical_url") for a in articles if a.get("canonical_url")}
    hash_seen = {a.get("content_hash") for a in articles if a.get("content_hash")}

    for candidate in candidates:
        if len(articles) >= config.target_count:
            break

        try:
            payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
            article = map_provider_payload(
                timeline_id=config.timeline_id,
                payload=payload,
                config=config,
                limiter=limiter,
            )
            if not article:
                raise ValueError("Mapping produced no canonical record")
            if article["canonical_url"] in canonical_seen:
                raise ValueError("Duplicate canonical URL")
            if article["content_hash"] in hash_seen:
                raise ValueError("Duplicate body hash")

            canonical_seen.add(article["canonical_url"])
            hash_seen.add(article["content_hash"])
            articles.append(article)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "timeline_id": config.timeline_id,
                    "url": str(candidate.get("url") or ""),
                    "error": str(exc),
                }
            )
        random_delay(config)

    article_path.parent.mkdir(parents=True, exist_ok=True)
    with article_path.open("w", encoding="utf-8") as out_jsonl:
        for row in articles:
            out_jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")

    failure_path.parent.mkdir(parents=True, exist_ok=True)
    with failure_path.open("w", encoding="utf-8", newline="") as out_csv:
        writer = csv.DictWriter(out_csv, fieldnames=["timeline_id", "url", "error"])
        writer.writeheader()
        writer.writerows(failures)

    logging.info("Wrote %s mapped articles -> %s", len(articles), article_path)
    logging.info("Wrote %s failures -> %s", len(failures), failure_path)
    return len(articles), len(failures)


def make_config(base_timeline_id: str, args: argparse.Namespace, timelines_cfg: dict[str, Any]) -> ScrapeConfig:
    raw = timelines_cfg[base_timeline_id]
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid timeline config for {base_timeline_id}")

    providers = raw.get("providers", [])
    if not isinstance(providers, list):
        raise ValueError(f"providers must be a list for {base_timeline_id}")

    return ScrapeConfig(
        timeline_id=base_timeline_id,
        start_date=args.start_date or str(raw.get("start_date")),
        end_date=args.end_date or str(raw.get("end_date")),
        target_count=args.target_count,
        resume=bool(args.resume),
        providers=[p for p in providers if isinstance(p, dict)],
    )


def run_timeline(timeline_id: str, args: argparse.Namespace, timelines_cfg: dict[str, Any]) -> None:
    config = make_config(timeline_id, args, timelines_cfg)
    limiter = RateLimiter(config.rate_limit_seconds)

    candidate_path = Path(f"data/intermediate/candidates_{timeline_id}.jsonl")
    article_path = Path(f"data/processed/articles_{timeline_id}.jsonl")
    failure_path = Path(f"data/processed/failures_{timeline_id}.csv")

    logging.info(
        "Starting timeline=%s date_range=%s..%s providers=%s target=%s",
        timeline_id,
        config.start_date,
        config.end_date,
        [p.get("name") for p in config.providers],
        config.target_count,
    )

    candidates = phase_a_provider_ingestion(config, candidate_path)
    if not candidates:
        logging.warning("No candidates discovered for %s", timeline_id)
        return

    success_count, failure_count = phase_b_map_and_dedup(
        config, candidates, article_path, failure_path, limiter
    )
    logging.info("Timeline complete: %s successes=%s failures=%s", timeline_id, success_count, failure_count)


def main() -> int:
    setup_logging()
    args = parse_args()
    timelines_cfg = load_timeline_config(Path(args.config))

    if args.timeline_id != "all" and args.timeline_id not in timelines_cfg:
        logging.error("Unknown timeline-id: %s", args.timeline_id)
        return 2

    timeline_ids = list(timelines_cfg.keys()) if args.timeline_id == "all" else [args.timeline_id]
    for timeline_id in timeline_ids:
        run_timeline(timeline_id, args, timelines_cfg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
