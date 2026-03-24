#!/usr/bin/env python3
"""Two-phase scraper for three timeline datasets.

Phase A: discover candidate URLs via keyword search queries per source.
Phase B: fetch + extract article metadata/content, then write processed and failure outputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen


ALLOWED_DOMAINS = [
    "timesofindia.indiatimes.com",
    "economictimes.indiatimes.com",
]

TIMELINES: dict[str, dict[str, Any]] = {
    "budget_2026": {
        "start_date": "2026-01-15",
        "end_date": "2026-03-15",
        "keywords": [
            "Union Budget 2026",
            "Budget 2026",
            "Nirmala Sitharaman",
            "tax slab",
            "fiscal deficit",
            "capex",
            "budget speech",
            "budget session",
        ],
    },
    "us_iran_conflict": {
        "start_date": "2025-10-01",
        "end_date": "2026-03-24",
        "keywords": [
            "US Iran tensions",
            "Iran retaliation",
            "US strikes",
            "IRGC",
            "sanctions on Iran",
            "Persian Gulf escalation",
            "Middle East escalation US Iran",
        ],
    },
    "israel_palestine_conflict": {
        "start_date": "2025-10-01",
        "end_date": "2026-03-24",
        "keywords": [
            "Israel Palestine conflict",
            "Gaza ceasefire",
            "West Bank violence",
            "Hamas Israel",
            "humanitarian aid Gaza",
            "UN resolution Gaza",
        ],
    },
}

QUERY_TEMPLATES = [
    "site:{domain} {keyword} {date_hint}",
    "site:{domain} {keyword} analysis",
    "site:{domain} {keyword} live updates",
]

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
    min_delay: float = 0.8
    max_delay: float = 2.2
    rate_limit_seconds: float = 0.8
    max_retries: int = 4


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
    parser = argparse.ArgumentParser(description="Scrape timeline articles in two phases")
    parser.add_argument(
        "--timeline-id",
        default="all",
        help="Timeline id (budget_2026|us_iran_conflict|israel_palestine_conflict) or all",
    )
    parser.add_argument("--start-date", help="Override timeline start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Override timeline end date (YYYY-MM-DD)")
    parser.add_argument("--target-count", type=int, default=120)
    parser.add_argument("--resume", action="store_true", help="Resume from existing outputs")
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def random_delay(config: ScrapeConfig) -> None:
    time.sleep(random.uniform(config.min_delay, config.max_delay))


def fetch_text(url: str, *, retries: int, timeout: int = 20, limiter: RateLimiter | None = None) -> str:
    backoff = 1.5
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
            sleep_s = backoff * attempt + random.uniform(0.2, 0.8)
            logging.warning("Retrying (%s/%s) %s in %.2fs", attempt, retries, url, sleep_s)
            time.sleep(sleep_s)
    raise RuntimeError(f"Unreachable fetch state for {url}")


def ddg_search(query: str, *, max_urls: int = 10, retries: int = 3, limiter: RateLimiter | None = None) -> list[str]:
    """Use DuckDuckGo HTML endpoint for lightweight URL discovery."""
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    html = fetch_text(search_url, retries=retries, limiter=limiter)
    hrefs = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)

    urls: list[str] = []
    for href in hrefs:
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            if uddg:
                href = uddg
        if href.startswith("http"):
            urls.append(href)
        if len(urls) >= max_urls:
            break
    return urls


def normalize_url(url: str) -> str:
    url = url.strip()
    url = re.sub(r"#.*$", "", url)
    return url


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def date_hint(start_date: str, end_date: str) -> str:
    return f"{start_date}..{end_date}"


def phase_a_discover(config: ScrapeConfig, candidate_path: Path, limiter: RateLimiter) -> list[dict[str, Any]]:
    seen = set()
    candidates: list[dict[str, Any]] = []

    if config.resume and candidate_path.exists():
        with candidate_path.open("r", encoding="utf-8") as infile:
            for line in infile:
                if not line.strip():
                    continue
                record = json.loads(line)
                seen.add(record["url"])
                candidates.append(record)
        logging.info("Loaded %s existing candidates for %s", len(candidates), config.timeline_id)

    for domain in ALLOWED_DOMAINS:
        for keyword in TIMELINES[config.timeline_id]["keywords"]:
            for template in QUERY_TEMPLATES:
                query = template.format(
                    domain=domain,
                    keyword=keyword,
                    date_hint=date_hint(config.start_date, config.end_date),
                )
                try:
                    urls = ddg_search(query, max_urls=10, retries=config.max_retries, limiter=limiter)
                except Exception as exc:  # noqa: BLE001
                    logging.error("Discovery failed for query '%s': %s", query, exc)
                    continue

                for url in urls:
                    normalized = normalize_url(url)
                    if normalized in seen:
                        continue
                    if not any(extract_domain(normalized).endswith(d) for d in ALLOWED_DOMAINS):
                        continue
                    record = {
                        "timeline_id": config.timeline_id,
                        "source": domain,
                        "query": query,
                        "keyword": keyword,
                        "url": normalized,
                        "discovered_at": datetime.now(timezone.utc).isoformat(),
                    }
                    candidates.append(record)
                    seen.add(normalized)

                random_delay(config)
                if len(candidates) >= config.target_count * 4:
                    break
            if len(candidates) >= config.target_count * 4:
                break
        if len(candidates) >= config.target_count * 4:
            break

    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("w", encoding="utf-8") as outfile:
        for record in candidates:
            outfile.write(json.dumps(record, ensure_ascii=False) + "\n")

    logging.info("Saved %s candidates -> %s", len(candidates), candidate_path)
    return candidates


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
            if isinstance(obj, list):
                parsed.extend([x for x in obj if isinstance(x, dict)])
            elif isinstance(obj, dict):
                parsed.append(obj)
        except json.JSONDecodeError:
            cleaned = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
            try:
                obj = json.loads(cleaned)
                if isinstance(obj, list):
                    parsed.extend([x for x in obj if isinstance(x, dict)])
                elif isinstance(obj, dict):
                    parsed.append(obj)
            except json.JSONDecodeError:
                continue
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

        section = as_str(item.get("articleSection"))
        body = as_str(item.get("articleBody"))
        title = as_str(item.get("headline")) or as_str(item.get("name"))
        published = as_str(item.get("datePublished"))
        canonical = as_str(item.get("mainEntityOfPage"))
        if isinstance(item.get("mainEntityOfPage"), dict):
            canonical = as_str(item["mainEntityOfPage"].get("@id"))

        return {
            "title": title,
            "published": published,
            "body": body,
            "author": author,
            "section": section,
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


def extract_article(url: str, timeline_id: str, config: ScrapeConfig, limiter: RateLimiter) -> dict[str, Any]:
    html = fetch_text(url, retries=config.max_retries, limiter=limiter)
    ld = parse_json_ld(html)
    structured = extract_article_from_ld(ld)
    if not structured:
        raise ValueError("No usable JSON-LD article object found")

    content = structured.get("body")
    if not content or len(content.split()) < 200:
        raise ValueError("Body missing or too short (<200 words)")

    published_at = normalize_published_at(structured.get("published"))
    if not published_at:
        raise ValueError("Missing/invalid published date")

    source = extract_domain(url)
    canonical_url = structured.get("canonical") or url
    record = {
        "timeline_id": timeline_id,
        "source": source,
        "url": url,
        "canonical_url": canonical_url,
        "title": structured.get("title"),
        "published_at": published_at,
        "author": structured.get("author"),
        "section": structured.get("section"),
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "content_hash": hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest(),
        "language": "en",
        "is_paywalled": False,
        "url_section": extract_url_section(url),
        "body": content,
    }
    return record


def phase_b_extract(
    config: ScrapeConfig,
    candidates: list[dict[str, Any]],
    article_path: Path,
    failure_path: Path,
    limiter: RateLimiter,
) -> tuple[int, int]:
    processed_urls: set[str] = set()
    articles: list[dict[str, Any]] = []

    if config.resume and article_path.exists():
        with article_path.open("r", encoding="utf-8") as infile:
            for line in infile:
                if not line.strip():
                    continue
                row = json.loads(line)
                processed_urls.add(row.get("url", ""))
                articles.append(row)
        logging.info("Loaded %s existing articles for resume", len(articles))

    failures: list[dict[str, str]] = []
    canonical_seen = {a.get("canonical_url") for a in articles if a.get("canonical_url")}
    hash_seen = {a.get("content_hash") for a in articles if a.get("content_hash")}

    for candidate in candidates:
        if len(articles) >= config.target_count:
            break

        url = candidate["url"]
        if url in processed_urls:
            continue

        try:
            article = extract_article(url, config.timeline_id, config, limiter)
            if article["canonical_url"] in canonical_seen:
                raise ValueError("Duplicate canonical URL")
            if article["content_hash"] in hash_seen:
                raise ValueError("Duplicate body hash")

            articles.append(article)
            processed_urls.add(url)
            canonical_seen.add(article["canonical_url"])
            hash_seen.add(article["content_hash"])
            logging.info("Extracted %s (%s/%s)", url, len(articles), config.target_count)
        except Exception as exc:  # noqa: BLE001
            failures.append({"timeline_id": config.timeline_id, "url": url, "error": str(exc)})
            logging.error("Extraction failed: %s -> %s", url, exc)

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

    logging.info("Wrote %s articles -> %s", len(articles), article_path)
    logging.info("Wrote %s failures -> %s", len(failures), failure_path)

    return len(articles), len(failures)


def make_config(base_timeline_id: str, args: argparse.Namespace) -> ScrapeConfig:
    tl = TIMELINES[base_timeline_id]
    return ScrapeConfig(
        timeline_id=base_timeline_id,
        start_date=args.start_date or tl["start_date"],
        end_date=args.end_date or tl["end_date"],
        target_count=args.target_count,
        resume=bool(args.resume),
    )


def run_timeline(timeline_id: str, args: argparse.Namespace) -> None:
    config = make_config(timeline_id, args)
    limiter = RateLimiter(config.rate_limit_seconds)

    candidate_path = Path(f"data/intermediate/candidates_{timeline_id}.jsonl")
    article_path = Path(f"data/processed/articles_{timeline_id}.jsonl")
    failure_path = Path(f"data/processed/failures_{timeline_id}.csv")

    logging.info(
        "Starting timeline=%s date_range=%s..%s target=%s resume=%s",
        timeline_id,
        config.start_date,
        config.end_date,
        config.target_count,
        config.resume,
    )

    candidates = phase_a_discover(config, candidate_path, limiter)
    if not candidates:
        logging.warning("No candidates discovered for %s", timeline_id)
        return

    success_count, failure_count = phase_b_extract(
        config, candidates, article_path, failure_path, limiter
    )
    logging.info(
        "Timeline complete: %s successes=%s failures=%s",
        timeline_id,
        success_count,
        failure_count,
    )


def main() -> int:
    setup_logging()
    args = parse_args()

    if args.timeline_id != "all" and args.timeline_id not in TIMELINES:
        logging.error("Unknown timeline-id: %s", args.timeline_id)
        return 2

    timeline_ids = list(TIMELINES.keys()) if args.timeline_id == "all" else [args.timeline_id]
    for timeline_id in timeline_ids:
        run_timeline(timeline_id, args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
