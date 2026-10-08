"""Web search via DuckDuckGo's HTML endpoint (no API key needed).

DuckDuckGo can change its HTML or rate-limit scrapers at any time, so this
parser is defensive and the action fails with a clear message instead of
returning garbage.
"""

from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.actions.base import BaseAction

SEARCH_URL = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 5


def _real_url(href: str) -> str:
    """Turn DuckDuckGo's redirect link (//duckduckgo.com/l/?uddg=...) into the target URL."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


class _ResultParser(HTMLParser):
    """Collect (title, url, snippet) from <a class="result__a"> / <a class="result__snippet">."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        href = attrs.get("href") or ""
        if "result__a" in classes:
            if "duckduckgo.com/y.js" in href:  # sponsored result
                return
            self.results.append({"title": "", "url": _real_url(href), "snippet": ""})
            self._field, self._buf = "title", []
        elif "result__snippet" in classes and self.results:
            self._field, self._buf = "snippet", []

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._field and self.results:
            self.results[-1][self._field] = " ".join("".join(self._buf).split())
            self._field = None


def parse_results(html: str, limit: int = MAX_RESULTS) -> list[dict[str, str]]:
    parser = _ResultParser()
    parser.feed(html)
    return [r for r in parser.results if r["url"].startswith("http")][:limit]


class SearchAction(BaseAction):
    """Search the web for information."""

    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Search the web for information"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        query = (params.get("query") or "").strip()
        if not query:
            return {"success": False, "message": "Missing 'query' parameter."}

        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                response = await client.get(
                    SEARCH_URL,
                    params={"q": query},  # httpx URL-encodes this
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                response.raise_for_status()
        except httpx.HTTPError as e:
            return {"success": False, "message": f"Search failed: {e}"}

        results = parse_results(response.text)
        if not results:
            return {
                "success": False,
                "message": "No results (DuckDuckGo may be rate-limiting; try again in a minute).",
                "results": [],
            }
        return {
            "success": True,
            "query": query,
            "message": f"Found {len(results)} results for '{query}'.",
            "results": results,
        }
