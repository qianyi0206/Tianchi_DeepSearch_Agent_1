# deepresearch/nodes/time_anchor.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Callable, List

from langchain_core.messages import SystemMessage, HumanMessage

from ..state import DeepResearchState


def make_time_anchor_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    async def time_anchor(state: DeepResearchState) -> DeepResearchState:
        question = state.get("question", "")
        claims = state.get("claims", [])
        claims_text = "\n".join([f"- {c.id}: {c.description}" for c in claims]) or "(no claims)"

        prompt = (
            "Extract time/sequence anchors from the question/claims.\n"
            "Focus on words like before/after/resumed/shortly/preceded/returned.\n"
            "Return JSON only:\n"
            '{ "time_anchors": ["..."], "time_queries": ["..."] }\n'
            "Make 2-4 time-specific queries using official sources keywords like press release, schedule, program resume.\n"
            f"Question:\n{question}\n\n"
            f"Claims:\n{claims_text}\n"
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
            anchors = data.get("time_anchors") or []
            queries = data.get("time_queries") or []
        except Exception:
            anchors = []
            queries = []

        return {"time_anchors": anchors, "time_queries": queries}

    return time_anchor
