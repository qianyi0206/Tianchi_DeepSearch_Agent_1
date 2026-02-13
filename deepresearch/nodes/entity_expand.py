# deepresearch/nodes/entity_expand.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Callable, List

from langchain_core.messages import SystemMessage, HumanMessage

from ..state import DeepResearchState


def make_entity_expand_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    async def entity_expand(state: DeepResearchState) -> DeepResearchState:
        question = state.get("question", "")
        claims = state.get("claims", [])
        claims_text = "\n".join([f"- {c.id}: {c.description}" for c in claims]) or "(no claims)"

        prompt = (
            "Extract key entities (people, places, organizations, works, events) from the question/claims,\n"
            "and propose alias/alternate names (translations, abbreviations, pen names, historical names).\n"
            "Return JSON only:\n"
            '{ "entities": ["..."], "expanded": ["..."] }\n'
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
            entities = data.get("entities") or []
            expanded = data.get("expanded") or []
        except Exception:
            entities = []
            expanded = []

        # de-dup and keep short list
        def _dedup(xs: List[str]) -> List[str]:
            seen = set()
            out = []
            for x in xs:
                if not x or not isinstance(x, str):
                    continue
                if x in seen:
                    continue
                seen.add(x)
                out.append(x)
            return out

        entities = _dedup(entities)
        expanded = _dedup(expanded)

        return {"entities": entities, "expanded_entities": expanded}

    return entity_expand
