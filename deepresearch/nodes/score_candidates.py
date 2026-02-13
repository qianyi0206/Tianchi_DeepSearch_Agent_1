# deepresearch/nodes/score_candidates.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Callable, List

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from ..schemas import Document
from ..state import DeepResearchState


def _format_evidence(docs: List[Document], max_chars_each: int = 600) -> str:
    if not docs:
        return ""
    chunks = []
    for i, d in enumerate(docs, start=1):
        title = (d.title or "").strip().replace("\n", " ")
        content = (d.content or "")[:max_chars_each]
        chunks.append(f"[S{i}] {title}\n{content}")
    return "\n\n".join(chunks)


def make_score_candidates_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    async def score_candidates(state: DeepResearchState) -> DeepResearchState:
        candidates = state.get("candidates", [])
        docs = state.get("documents", [])
        question = state.get("question", "")

        if not candidates:
            return {"selected_candidate": "", "candidate_scores": []}

        evidence_text = _format_evidence(docs)
        prompt = (
            "Given the evidence, score each candidate (0-5) and pick the best.\n"
            "Return JSON only:\n"
            "{\n"
            '  "scores": [{"candidate": "...", "score": 0-5, "reason": "..."}],\n'
            '  "best": "candidate or empty"\n'
            "}\n"
            f"Question:\n{question}\n\n"
            f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
            f"Evidence:\n{evidence_text}\n"
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
            scores = data.get("scores") or []
            best = data.get("best") or ""
        except Exception:
            scores = []
            best = ""

        return {
            "candidate_scores": scores,
            "selected_candidate": best,
            "messages": [AIMessage(content=f"[score_candidates] best: {best}")],
        }

    return score_candidates
