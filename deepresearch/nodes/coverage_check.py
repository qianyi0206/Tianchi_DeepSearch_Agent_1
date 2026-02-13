# deepresearch/nodes/coverage_check.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Callable, List

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..schemas import Claim, Document
from ..state import DeepResearchState

def _extract_years(text: str) -> List[str]:
    years = []
    for token in text.replace("–", "-").replace("—", "-").split():
        if token.isdigit() and len(token) == 4:
            y = int(token)
            if 1500 <= y <= 2099:
                years.append(str(y))
    # de-dup preserve order
    seen = set()
    out = []
    for y in years:
        if y not in seen:
            seen.add(y)
            out.append(y)
    return out


def _format_evidence(docs: List[Document], max_chars_each: int = 800) -> str:
    if not docs:
        return ""
    chunks = []
    for i, d in enumerate(docs, start=1):
        title = (d.title or "").strip().replace("\n", " ")
        content = (d.content or "")[:max_chars_each]
        chunks.append(f"[S{i}] {title}\n{content}")
    return "\n\n".join(chunks)


def make_coverage_check_node(llm, max_retries: int = 1) -> Callable[[DeepResearchState], DeepResearchState]:
    async def coverage_check(state: DeepResearchState) -> DeepResearchState:
        retry_count = int(state.get("retry_count", 0))
        claims: List[Claim] = state.get("claims", [])
        docs: List[Document] = state.get("documents", [])
        missing_claims = state.get("missing_claims", [])

        if retry_count >= max_retries:
            return {"next_action": "score_candidates"}

        if not docs:
            return {
                "next_action": "retrieve",
                "retry_count": retry_count + 1,
                "queries": state.get("queries", []),
                "messages": [
                    AIMessage(content="[coverage_check] No documents; retrying retrieval.")
                ],
            }

        if not missing_claims:
            return {"next_action": "score_candidates"}

        claims_text = "\n".join([f"- {c.id}: {c.description}" for c in claims]) or "(no claims)"
        evidence_text = _format_evidence(docs)

        prompt = (
            "You are a careful research assistant.\n"
            "Task: Check if the evidence likely covers the claims. If not, propose 1-3 targeted queries.\n"
            "Return JSON only:\n"
            "{\n"
            '  "missing_claims": ["c1", "c2"],\n'
            '  "queries": ["query1", "query2"]\n'
            "}\n"
        )
        msg = [
            SystemMessage(content="Return JSON only. No markdown."),
            HumanMessage(
                content=(
                    f"Claims:\n{claims_text}\n\n"
                    f"Missing Claims (from verification):\n{missing_claims}\n\n"
                    f"Evidence:\n{evidence_text}\n"
                )
            ),
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
            new_queries = data.get("queries") or []
        except Exception:
            new_queries = []

        if not new_queries and missing_claims:
            # Fallback: build targeted queries from missing claim descriptions
            desc_map = {c.id: c.description for c in claims}
            for cid in missing_claims:
                desc = desc_map.get(cid, "")
                if desc:
                    new_queries.append(desc[:120])

        # Add year anchors from evidence if missing claims persist
        if missing_claims and docs:
            years = []
            for d in docs:
                years += _extract_years(d.content or "")
            years = list(dict.fromkeys(years))
            if years:
                for y in years[:3]:
                    new_queries.append(f"{y} {missing_claims}")

        if not new_queries:
            return {"next_action": "score_candidates"}

        existing = state.get("queries", [])
        merged = existing + [q for q in new_queries if q not in existing]

        return {
            "next_action": "retrieve",
            "retry_count": retry_count + 1,
            "queries": merged,
            "messages": [
                AIMessage(
                    content=f"[coverage_check] Added {len(merged) - len(existing)} targeted queries."
                )
            ],
        }

    return coverage_check
