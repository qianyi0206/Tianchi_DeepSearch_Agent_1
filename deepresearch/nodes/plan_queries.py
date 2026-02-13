# deepresearch/nodes/plan_queries.py
# -*- coding: utf-8 -*-
import asyncio
import json
from langchain_core.messages import SystemMessage, HumanMessage
from ..state import DeepResearchState


def _dedup(items):
    out = []
    seen = set()
    for x in items:
        if not isinstance(x, str):
            continue
        x = x.strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


async def _fallback_claim_queries(llm, question: str, claim_id: str, claim_desc: str):
    prompt = (
        "Generate exactly 2 concise web search queries in English for this claim.\n"
        "Focus on factual verification and include key entities/time anchors if present.\n"
        "Return JSON list only.\n"
        f"Question: {question}\n"
        f"Claim ({claim_id}): {claim_desc}\n"
    )
    msg = [
        SystemMessage(content="Return ONLY a JSON list of 2 strings."),
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
        arr = json.loads(content)
        if isinstance(arr, list):
            return _dedup(arr)[:2]
    except Exception:
        pass
    # deterministic fallback
    return _dedup([f"{claim_desc} verification", f"{question} {claim_desc}"])[:2]


def make_plan_queries_node(llm):
    async def plan_queries(state: DeepResearchState):
        claims = state.get("claims", [])
        candidates = state.get("candidates", [])
        entities = state.get("entities", [])
        expanded_entities = state.get("expanded_entities", [])
        question = state.get("question", "")
        time_queries = state.get("time_queries", [])
        timeline_queries = state.get("timeline_queries", [])
        timeline_years = state.get("timeline_years", [])

        claims_data = []
        for c in claims:
            if hasattr(c, "model_dump"):
                claims_data.append(c.model_dump())
            elif hasattr(c, "dict"):
                claims_data.append(c.dict())
            else:
                claims_data.append(str(c))

        prompt = f"""
You are a research query planner.
Return JSON only in this format:
{{
  "global_queries": ["..."],
  "claim_queries": {{
    "c1": ["..."],
    "c2": ["..."]
  }}
}}

Task:
1) Generate 4-8 global queries for broad retrieval.
2) Generate 1-3 targeted queries per claim id.
3) Use exact phrase quotes where helpful.
4) Use multilingual queries when relevant.
5) Prefer source-friendly phrasing and add year anchors when present.

Question:
{question}

Claims:
{json.dumps(claims_data, ensure_ascii=False, indent=2)}

Candidates:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Entities:
{json.dumps(entities, ensure_ascii=False, indent=2)}

Expanded Entities:
{json.dumps(expanded_entities, ensure_ascii=False, indent=2)}
"""

        msg = [
            SystemMessage(content="Return ONLY valid JSON. No markdown."),
            HumanMessage(content=prompt),
        ]

        global_queries = []
        claim_queries = {}

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

            if isinstance(data, list):
                global_queries = data
                claim_queries = {}
            else:
                global_queries = data.get("global_queries") or []
                claim_queries = data.get("claim_queries") or {}
        except Exception:
            global_queries = [
                "key entity name official site",
                "key concept timeline",
                "historical background",
            ]
            claim_queries = {}

        # Add time/timeline support
        global_queries.extend(time_queries or [])
        global_queries.extend(timeline_queries or [])
        for y in timeline_years or []:
            if isinstance(y, str) and y.strip():
                global_queries.append(f"{y} {question}")

        # Year tokens in question
        for tok in question.replace("–", "-").split():
            if tok.isdigit() and len(tok) == 4:
                global_queries.append(f"{tok} {question}")

        global_queries = _dedup(global_queries)

        # Normalize claim query map and keep known claim ids only
        known_ids = {getattr(c, "id", None) for c in claims}
        normalized_claim_queries = {}
        for cid, qlist in (claim_queries or {}).items():
            if cid not in known_ids:
                continue
            if not isinstance(qlist, list):
                continue
            normalized_claim_queries[cid] = _dedup(qlist)

        # Ensure each claim has targeted query fallback (English-focused).
        tasks = []
        missing_claims = []
        for c in claims:
            cid = getattr(c, "id", "")
            desc = getattr(c, "description", "")
            if cid and cid not in normalized_claim_queries:
                missing_claims.append((cid, desc))
                tasks.append(_fallback_claim_queries(llm, question, cid, desc))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (cid, _), res in zip(missing_claims, results):
                if isinstance(res, Exception):
                    normalized_claim_queries[cid] = []
                else:
                    normalized_claim_queries[cid] = _dedup(res)

        return {
            "queries": global_queries,
            "claim_queries": normalized_claim_queries,
        }

    return plan_queries
