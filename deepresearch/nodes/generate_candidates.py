# deepresearch/nodes/generate_candidates.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Callable, List

from langchain_core.messages import SystemMessage, HumanMessage

from ..state import DeepResearchState


def make_generate_candidates_node(llm, max_candidates: int = 5) -> Callable[[DeepResearchState], DeepResearchState]:
    async def generate_candidates(state: DeepResearchState) -> DeepResearchState:
        question = state.get("question", "")
        claims = state.get("claims", [])
        claims_text = "\n".join([f"- {c.id}: {c.description}" for c in claims]) or "(no claims)"

        prompt = (
            "Generate 3-5 plausible candidate answers for the question.\n"
            "Use the claims as constraints. Do NOT use evidence yet.\n"
            "Return JSON list only.\n"
            f"Question:\n{question}\n\n"
            f"Claims:\n{claims_text}\n"
        )
        msg = [
            SystemMessage(content="Return ONLY a JSON list, no extra text."),
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
            candidates = json.loads(content)
            if not isinstance(candidates, list):
                candidates = []
        except Exception:
            candidates = []

        if max_candidates > 0 and len(candidates) > max_candidates:
            candidates = candidates[:max_candidates]

        return {"candidates": candidates}

    return generate_candidates
