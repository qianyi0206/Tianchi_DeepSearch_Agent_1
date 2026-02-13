# deepresearch/nodes/timeline_align.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Callable, List

from langchain_core.messages import SystemMessage, HumanMessage

from ..schemas import Document
from ..state import DeepResearchState


def _extract_years(text: str) -> List[str]:
    years = re.findall(r"\b(1[6-9]\d{2}|20\d{2})\b", text)
    # de-dup preserve order
    seen = set()
    out = []
    for y in years:
        if y not in seen:
            seen.add(y)
            out.append(y)
    return out


def _top_years(years: List[str], k: int = 3) -> List[str]:
    freq = {}
    for y in years:
        freq[y] = freq.get(y, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [y for y, _ in ranked[:k]]


def make_timeline_align_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    async def timeline_align(state: DeepResearchState) -> DeepResearchState:
        question = state.get("question", "")
        docs: List[Document] = state.get("documents", [])
        claims = state.get("claims", [])
        claims_text = "\n".join([f"- {c.id}: {c.description}" for c in claims]) or "(no claims)"

        # Heuristic: collect year candidates from evidence
        years = []
        for d in docs:
            years += _extract_years(d.content or "")
        top = _top_years(years, k=3)

        prompt = (
            "Given the question and claims, propose 1-3 likely year anchors.\n"
            "Return JSON only: {\"years\": [\"YYYY\", ...], \"queries\": [\"...\"]}\n"
            f"Question:\n{question}\n\n"
            f"Claims:\n{claims_text}\n\n"
            f"Observed years from evidence: {top}\n"
        )
        msg = [
            SystemMessage(content="Return JSON only. No markdown."),
            HumanMessage(content=prompt),
        ]

        try:
            resp = await llm.ainvoke(msg)
            content = str(resp.content).strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            data = json.loads(content)
            years = data.get("years") or []
            queries = data.get("queries") or []
        except Exception:
            years = top
            queries = []

        return {"timeline_years": years, "timeline_queries": queries}

    return timeline_align
