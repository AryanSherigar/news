# Dataset Spec: 3 Timelines (Provider-Driven Ingestion)

## 1. Timeline IDs and windows

- `budget_2026`: 2026-01-15 to 2026-03-15
- `us_iran_conflict`: 2025-10-01 to 2026-03-24 (adjustable)
- `israel_palestine_conflict`: 2025-10-01 to 2026-03-24 (adjustable)

Date windows are defined in `configs/timeline_queries.yaml` and can be overridden at runtime with `--start-date` / `--end-date`.

## 2. Provider mix and source strategy

Primary providers are now explicitly configured per timeline:

- **Guardian Content API** (`guardian`): full text + metadata source when available.
- **GDELT DOC API** (`gdelt`): event/GKG-oriented coverage and long-tail discovery.
- **Optional enrichers**: `gnews`, `mediastack` (enabled only if API keys are present).

Per-timeline source strategy and query packs are maintained in `configs/timeline_queries.yaml` under each timeline entry.

## 3. Target counts per timeline

- Goal: 100–200 each
- Soft floor: 80
- Hard floor: 60 (flag if below)

## 4. Canonical intermediate schema (JSONL)

Every mapped record must preserve these fields so downstream dedup/chunk/embed scripts remain compatible:

- `timeline_id`
- `source`
- `provider`
- `provider_query`
- `url`
- `canonical_url`
- `title`
- `published_at`
- `author`
- `section`
- `scraped_at`
- `content_hash`
- `language`
- `is_paywalled` (boolean)
- `url_section` (top-level URL path, e.g. `/world`, `/business`)
- `body`

## 5. Inclusion criteria

- Record must be returned by at least one configured provider query for that timeline.
- Must have a valid URL and usable body text after mapping/enrichment.
- Must have a normalized publication timestamp (`published_at`) or fallback ingestion timestamp.

## 6. Exclusion criteria

- Empty URLs, malformed payloads, records with near-empty body text, and hard duplicates.
- Duplicate canonical URLs.
- Duplicate exact content hashes.

## 7. Dedup policy

- Canonical URL dedup + exact hash dedup in ingestion stage.
- Near-duplicate collapse remains in `scripts/prepare_three_timelines_chunks.py`.
- If one article/event is relevant to multiple timelines, assign explicit timeline ownership upstream via timeline query strategy (or post-process multi-labeling if required).

## 8. Engineering notes

- The scraper no longer relies on `ALLOWED_DOMAINS` and DuckDuckGo discovery.
- Provider loops are the first-phase discovery mechanism.
- For providers that do not always ship full body text (for example, GDELT/GNews/Mediastack), the scraper attempts URL-level JSON-LD extraction and maps to the canonical schema.
- Optional providers are automatically skipped when their API keys are not set.

## 9. Runtime environment variables

- `GUARDIAN_API_KEY` (defaults to `test` for low-volume development)
- `GNEWS_API_KEY` (optional)
- `MEDIASTACK_API_KEY` (optional)
