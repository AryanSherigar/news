import aiohttp
from typing import Optional


async def fetch_news_context(query: str, max_results: int = 5) -> str:
    """
    Fetch news context for a given topic using rss2json proxy.
    
    This mimics the frontend's fetchNewsContext function.
    Falls back gracefully if news fetch fails.
    """
    try:
        encoded_query = query.replace(" ", "+")
        url = f"https://www.rss2json.com/api.json?rss_url=https://news.google.com/rss/search?q={encoded_query}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return "No live news retrieved. Rely on your internal knowledge."
                
                data = await response.json()
                items = data.get("items", [])[:max_results]
                
                if not items:
                    return "No live news retrieved. Rely on your internal knowledge."
                
                news_lines = []
                for item in items:
                    title = item.get("title", "")
                    pubDate = item.get("pubDate", "")
                    news_lines.append(f"- {title} ({pubDate})")
                
                return "\n".join(news_lines)
    
    except Exception as e:
        print(f"Error fetching news: {e}")
        return "No live news retrieved. Rely on your internal knowledge."
