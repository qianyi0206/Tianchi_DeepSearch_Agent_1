# deepresearch/nodes/retrieve.py
# -*- coding: utf-8 -*-
"""
Node: retrieve
Goal: claim-first search + fetch documents into state.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from langchain_core.messages import AIMessage

from ..state import DeepResearchState


_BLOCKED_HOST_PARTS = {
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "zhihu.com",
}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    h = _host(url)
    return any(p in h for p in _BLOCKED_HOST_PARTS)


def _build_query_plan(claim_queries: Dict[str, List[str]], global_queries: List[str]) -> List[Tuple[Optional[str], str]]:
    # global-first to ensure anchor queries are executed before cap is hit
    plan: List[Tuple[Optional[str], str]] = []
    for q in global_queries or []:
        if isinstance(q, str) and q.strip():
            plan.append((None, q.strip()))
    for cid, qs in (claim_queries or {}).items():
        for q in qs:
            if isinstance(q, str) and q.strip():
                plan.append((cid, q.strip()))
    return plan


def make_retrieve_node(searcher, fetcher):
    async def retrieve(state: DeepResearchState) -> DeepResearchState:
        global_queries = state.get("queries", [])
        claim_queries = state.get("claim_queries", {})

        documents = []
        seen_urls = set()
        max_docs = 20
        per_query_results = 3

        plan = _build_query_plan(claim_queries, global_queries)
        claim_hit_count: Dict[str, int] = {}

        for cid, query in plan:
            if len(documents) >= max_docs:
                break

            try:
                results = await searcher.search(query)
            except Exception:
                continue

            for result in results[:per_query_results]:
                if len(documents) >= max_docs:
                    break
                if result.url in seen_urls:
                    continue
                if _is_blocked(result.url):
                    continue

                seen_urls.add(result.url)
                try:
                    doc = await fetcher.fetch(result.url)
                    documents.append(doc)
                    if cid:
                        claim_hit_count[cid] = claim_hit_count.get(cid, 0) + 1
                except Exception:
                    continue

        status = f"Fetched {len(documents)} documents"
        if claim_hit_count:
            status += f"; claim_hits={claim_hit_count}"

        return {
            "documents": documents,
            "messages": [AIMessage(content=status)],
        }

    return retrieve


if __name__ == "__main__":
    pass
