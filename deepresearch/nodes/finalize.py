# deepresearch/nodes/finalize.py
# -*- coding: utf-8 -*-
"""
Finalize node:
- Produce final answer with citations [S1][S2]...
- If evidence is insufficient, be explicit about missing evidence.
"""
from __future__ import annotations

from typing import Callable, List

from langchain_core.messages import AIMessage

from ..schemas import Claim, Document
from ..state import DeepResearchState
from ..utils.answer_normalize import (
    canonicalize_answer,
    extract_final_answer,
    normalize_answer,
)


def _format_sources(docs: List[Document], max_chars_each: int = 1800) -> str:
    if not docs:
        return "(no evidence documents)"

    chunks = []
    for i, d in enumerate(docs, start=1):
        title = (d.title or "").strip().replace("\n", " ")
        content = d.content or ""
        if len(content) > max_chars_each:
            content = content[:max_chars_each] + "\n[TRUNCATED]"
        chunks.append(f"[S{i}] {title}\nURL: {d.url}\nCONTENT:\n{content}\n")
    return "\n\n".join(chunks)


def make_finalize_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    async def finalize(state: DeepResearchState) -> DeepResearchState:
        question: str = state.get("question", "")
        claims: List[Claim] = state.get("claims", [])
        docs: List[Document] = state.get("documents", [])
        selected_candidate: str = state.get("selected_candidate", "")
        candidates: List[str] = state.get("candidates", [])

        # Hard fallback when no evidence exists
        if not docs:
            answer_text = (
                "Final Answer: Unknown\n"
                "Evidence:\n"
                "- No sources were retrieved, so the answer cannot be verified.\n"
                "Sources:\n"
            )
            answer_value = "Unknown"
            return {
                "final_answer": answer_text,
                "final_answer_canonical": answer_value,
                "final_answer_normalized": normalize_answer(answer_value),
                "messages": [AIMessage(content=answer_text)],
            }

        claims_text = (
            "\n".join([f"- {c.id}: {c.description}" for c in claims])
            if claims
            else "(no claims)"
        )
        sources_text = _format_sources(docs)

        prompt = (
            "You are a deep research assistant. You MUST answer based ONLY on the evidence pack.\n"
            "Output format (must follow exactly):\n"
            "1) First line: Final Answer: <answer only>\n"
            "2) Evidence: bullet list. Each bullet MUST end with citations like [S1] or [S2][S3].\n"
            "3) Sources list: format 'S1: url'\n"
            "4) For each claim, either provide cited support or explicitly say it is missing.\n\n"
            f"Question:\n{question}\n\n"
            f"Selected candidate (if any):\n{selected_candidate}\n\n"
            f"Claims:\n{claims_text}\n\n"
            f"Evidence Pack:\n{sources_text}\n"
        )

        resp = await llm.ainvoke(prompt)
        answer_text = str(resp.content).strip()

        raw_answer = extract_final_answer(answer_text)
        pool = list(candidates)
        if selected_candidate:
            pool.append(selected_candidate)
        canonical = canonicalize_answer(raw_answer, pool)
        normalized = normalize_answer(canonical)

        # Keep the original evidence body, but standardize the first line answer.
        lines = answer_text.splitlines()
        if lines:
            if lines[0].lower().startswith("final answer:"):
                lines[0] = f"Final Answer: {canonical}"
            else:
                lines.insert(0, f"Final Answer: {canonical}")
            answer_text = "\n".join(lines)

        return {
            "final_answer": answer_text,
            "final_answer_canonical": canonical,
            "final_answer_normalized": normalized,
            "messages": [AIMessage(content=answer_text)],
        }

    return finalize
