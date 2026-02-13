# deepresearch/tools/search_tool.py
# -*- coding: utf-8 -*-
"""
Search tool:
- Prefer SerpApi if configured
- Fallback to DuckDuckGo
"""
from __future__ import annotations

import os
import asyncio
from typing import List, Optional

import httpx
from dotenv import load_dotenv

from ..schemas import SearchResult


def _getenv(name: str, default: Optional[str] = None) -> str:
    v = os.getenv(name, default)
    return v if v is not None else ""


class SerpApiSearcher:
    def __init__(self, api_key: str, engine: str = "google", max_results: int = 5, timeout_s: float = 20.0):
        self.api_key = api_key
        self.engine = engine
        self.max_results = max_results
        self.timeout_s = timeout_s

    async def search(self, query: str) -> List[SearchResult]:
        url = "https://serpapi.com/search.json"
        params = {
            "engine": self.engine,
            "q": query,
            "api_key": self.api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        organic = data.get("organic_results") or []
        results: List[SearchResult] = []
        for r in organic:
            title = (r.get("title") or "").strip()
            link = (r.get("link") or "").strip()
            snippet = (r.get("snippet") or None)
            if title and link:
                results.append(SearchResult(title=title, url=link, snippet=snippet))

        return results[: self.max_results]


class DuckDuckGoSearcher:
    def __init__(self, max_results: int = 5):
        self.max_results = max_results

    def _search_sync(self, query: str) -> List[SearchResult]:
        try:
            from ddgs import DDGS
        except Exception:
            return []

        results: List[SearchResult] = []
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=self.max_results):
                    url = r.get("href") or ""
                    title = r.get("title") or ""
                    snippet = r.get("body")
                    if url and title:
                        results.append(SearchResult(title=title, url=url, snippet=snippet))
        except Exception:
            return []

        return results

    async def search(self, query: str) -> List[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query)


def build_searcher():
    # Ensure .env is loaded in CLI contexts
    try:
        load_dotenv(".env")
    except Exception:
        pass

    serp_key = _getenv("SERPAPI_API_KEY", "")
    if serp_key.strip():
        engine = _getenv("SERPAPI_ENGINE", "google").strip() or "google"
        max_results = int(_getenv("SERPAPI_MAX_RESULTS", "5"))
        return SerpApiSearcher(api_key=serp_key, engine=engine, max_results=max_results)

    return DuckDuckGoSearcher(max_results=int(_getenv("SERPAPI_MAX_RESULTS", "5")))
