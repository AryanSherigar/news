import json
from datetime import datetime, timezone
from typing import Any

import aiohttp

from app.schemas import NewsItem


NO_NEWS_CONTEXT = [
    NewsItem(
        title="No live news retrieved",
        link="",
        source="System",
        published_at="",
    )
]


async def fetch_news_context(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Fetch structured news context for a given topic using the rss2json proxy.

    Returns a dict with serialized prompt context, structured items for API consumers,
    and the timestamp when the fetch completed.
    """
    fetched_at = datetime.now(timezone.utc)

    try:
        encoded_query = query.replace(" ", "+")
        url = f"https://www.rss2json.com/api.json?rss_url=https://news.google.com/rss/search?q={encoded_query}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return _fallback_context(fetched_at)

                data = await response.json()
                items = data.get("items", [])[:max_results]
                if not items:
                    return _fallback_context(fetched_at)

                news_items = [_to_news_item(item) for item in items]
                return {
                    "prompt_context": json.dumps([item.model_dump() for item in news_items], ensure_ascii=False, indent=2),
                    "items": [item.model_dump() for item in news_items],
                    "fetched_at": fetched_at.isoformat(),
                }

    except Exception as e:
        print(f"Error fetching news: {e}")
        return _fallback_context(fetched_at)


def _to_news_item(item: dict[str, Any]) -> NewsItem:
    source_info = item.get("source") or {}
    source_name = source_info.get("title") if isinstance(source_info, dict) else ""

    return NewsItem(
        title=item.get("title", ""),
        link=item.get("link", ""),
        source=source_name or "Unknown source",
        published_at=item.get("pubDate", ""),
    )


def _fallback_context(fetched_at: datetime) -> dict[str, Any]:
    return {
        "prompt_context": json.dumps([item.model_dump() for item in NO_NEWS_CONTEXT], ensure_ascii=False, indent=2),
        "items": [item.model_dump() for item in NO_NEWS_CONTEXT],
        "fetched_at": fetched_at.isoformat(),
    }
