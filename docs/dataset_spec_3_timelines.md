# Dataset Spec: 3 Timelines

## 1. Timeline IDs and windows

- `budget_2026`: 2026-01-15 to 2026-03-15
- `us_iran_conflict`: 2025-10-01 to 2026-03-24 (adjustable)
- `israel_palestine_conflict`: 2025-10-01 to 2026-03-24 (adjustable)

## 2. Allowed domains only

- `timesofindia.indiatimes.com`
- `economictimes.indiatimes.com`

## 3. Target counts per timeline

- Goal: 100–200 each
- Soft floor: 80
- Hard floor: 60 (flag if below)

## 4. Inclusion criteria

- Must match timeline-specific keyword packs.
- Must have valid published datetime and body content.

## 5. Exclusion criteria

- Photo galleries, pure video pages, near-empty market snippets, non-article stubs, duplicate wires, and any article with a body text word count under 200 words.

## 6. Required metadata fields

- `timeline_id`, `source`, `url`, `canonical_url`, `title`, `published_at`, `author`, `section`, `scraped_at`, `content_hash`, `language`, `is_paywalled` (boolean), `url_section` (extracted top-level directory, e.g., `/world`, `/business`).

## 7. Dedup policy

- Canonical URL dedup + exact hash dedup + near-duplicate collapse.
- For articles matching keywords in both `us_iran_conflict` and `israel_palestine_conflict`, the scraper must append both to the `timeline_id` array rather than arbitrarily dropping one.

## 8. Target keyword packs

### `budget_2026`

- Primary (must include at least 1): `Budget 2026`, `Union Budget`, `Nirmala Sitharaman`, `economic survey`.
- Secondary (contextual amplifiers): `fiscal deficit`, `income tax slabs`, `capex`, `capital expenditure`, `customs duty`, `direct tax`.

### `us_iran_conflict`

- Primary (must include at least 1): `US strikes`, `Iran-backed`, `IRGC`, `Islamic Revolutionary Guard Corps`, `Tehran`.
- Secondary (contextual amplifiers): `Strait of Hormuz`, `Red Sea shipping`, `Houthi`, `drone attack`, `sanctions`, `nuclear facility`.

### `israel_palestine_conflict`

- Primary (must include at least 1): `Gaza`, `Hamas`, `IDF`, `Israel Defense Forces`, `Netanyahu`, `Palestine`.
- Secondary (contextual amplifiers): `Rafah`, `ceasefire`, `hostages`, `West Bank`, `two-state solution`, `UNRWA`.

## 9. Scraper engineering note

- Configure the scraper to inspect `<script type="application/ld+json">` in page source.
- For both Times of India and Economic Times, full article text is typically available in the JSON-LD `articleBody` field, including cases where the visual frontend is blocked by a paywall.
