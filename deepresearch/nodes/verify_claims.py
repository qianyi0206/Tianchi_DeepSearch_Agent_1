# deepresearch/nodes/verify_claims.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Callable, List

from langchain_core.messages import SystemMessage, HumanMessage

from ..schemas import Claim, Document
from ..state import DeepResearchState


def _format_evidence(docs: List[Document], max_chars_each: int = 800) -> str:
    if not docs:
        return ""
    chunks = []
    for i, d in enumerate(docs, start=1):
        title = (d.title or "").strip().replace("\n", " ")
        content = (d.content or "")[:max_chars_each]
        chunks.append(f"[S{i}] {title}\n{content}")
    return "\n\n".join(chunks)


def make_verify_claims_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    async def verify_claims(state: DeepResearchState) -> DeepResearchState:
        claims: List[Claim] = state.get("claims", [])
        docs: List[Document] = state.get("documents", [])

        if not claims or not docs:
            return {"missing_claims": [c.id for c in claims], "claim_verification": []}

        claims_text = "\n".join([f"- {c.id}: {c.description}" for c in claims])
        evidence_text = _format_evidence(docs)

        prompt = (
            "Verify each claim against the evidence pack.\n"
            "Return JSON only in this format:\n"
            "{\n"
            '  "items": [\n'
            '    {"id":"c1","supported":true,"sources":["S1","S3"],"note":"..."}\n'
            "  ],\n"
            '  "missing_claims": ["c2"]\n'
            "}\n"
        )
        msg = [
            SystemMessage(content="Return JSON only. No markdown."),
            HumanMessage(
                content=(
                    f"Claims:\n{claims_text}\n\n"
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
            items = data.get("items") or []
            missing = data.get("missing_claims") or []
        except Exception:
            items = []
            missing = [c.id for c in claims]

        return {"claim_verification": items, "missing_claims": missing}

    return verify_claims
