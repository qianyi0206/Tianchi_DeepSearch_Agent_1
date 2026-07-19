# -*- coding: utf-8 -*-
"""
Deep Research Agent Harness — 入口文件。

编排范式：orchestrator-worker + Reflection + Chain-of-Verification
  nodes.py    — 预搜 / 分解 / 综合 / 答案抽取
  harness.py  — orchestrator / worker_pool / reflection / CoVe
  tools.py    — 并行多源工具 + 三角验证 + 压缩
  task_graph.py — 子任务 DAG
  memory.py   — 可审计执行轨迹与 session 记忆
  config.py / state.py / plan_tips.py
"""

import time
import asyncio
import json
import logging
from typing import AsyncIterator, List

from fastapi import Request
from fastapi.responses import StreamingResponse

from agentscope_runtime.engine import AgentApp
from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest
from langchain_core.messages import BaseMessage, AIMessage
from langgraph.graph import StateGraph, END

from config import MAX_SEARCH_LOOPS
# max_loop 与典型 DAG 深度对齐（见 config.MAX_SEARCH_LOOPS）
from state import ResearchState
from nodes import (
    preliminary_search,
    question_decomposition,
    supplementary_search,
    finalize_summary,
    answer_extraction,
)
from harness import (
    orchestrator,
    should_run_workers,
    worker_pool,
    reflection,
    should_continue_harness,
    chain_of_verification,
)
from memory import load_short_term, load_long_term

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("research_agent")


# ─── Graph: Orchestrator-Worker Harness ──────────────────────────────

def build_research_graph():
    """
    preliminary → decomposition → orchestrator ─┐
         ┌──────────────────────────────────────┘
         ▼
      worker_pool (DAG ready 并行)
         ▼
      reflection (缺口 / 补任务 / 收敛)
         ├─ continue → orchestrator
         └─ exit → supplementary → finalize → CoVe → answer_extraction → END
    """
    g = StateGraph(ResearchState)

    g.add_node("preliminary_search", preliminary_search)
    g.add_node("question_decomposition", question_decomposition)
    g.add_node("orchestrator", orchestrator)
    g.add_node("worker_pool", worker_pool)
    g.add_node("reflection", reflection)
    g.add_node("supplementary_search", supplementary_search)
    g.add_node("finalize_summary", finalize_summary)
    g.add_node("chain_of_verification", chain_of_verification)
    g.add_node("answer_extraction", answer_extraction)

    g.set_entry_point("preliminary_search")
    g.add_edge("preliminary_search", "question_decomposition")
    g.add_edge("question_decomposition", "orchestrator")

    g.add_conditional_edges("orchestrator", should_run_workers, {
        "run_workers": "worker_pool",
        "converge_exit": "supplementary_search",
    })

    g.add_edge("worker_pool", "reflection")

    g.add_conditional_edges("reflection", should_continue_harness, {
        "continue_loop": "orchestrator",
        "exit_loop": "supplementary_search",
    })

    g.add_edge("supplementary_search", "finalize_summary")
    g.add_edge("finalize_summary", "chain_of_verification")
    g.add_edge("chain_of_verification", "answer_extraction")
    g.add_edge("answer_extraction", END)

    return g.compile()


# ─── State Factory ───────────────────────────────────────────────────

def _make_initial_state(
    question: str,
    max_loop: int = MAX_SEARCH_LOOPS,
    session_id: str = "default",
    user_id: str = "default",
) -> ResearchState:
    return {
        "query": question,
        "max_loop": max_loop,
        "session_id": session_id,
        "user_id": user_id,
        "preliminary_context": "",
        "question_hints": "",
        "decomposition": "",
        "problem_type": "",
        "task_dag": [],
        "ready_batch": [],
        "harness_round": 1,
        "converge": False,
        "findings": [],
        "executed_queries": [],
        "visited_urls": [],
        "knowledge_gap": question,
        "current_loop": 1,
        "search_query": "",
        "final_summary": "",
        "final_answer": "",
        "supplementary_context": "",
        "start_time": time.time(),
        "execution_trace": [],
        "citations": [],
        "verification_report": "",
    }


# ─── AgentScope Runtime ──────────────────────────────────────────────

research_graph = None

agent_app = AgentApp(
    app_name="DeepResearchHarness",
    app_description="Orchestrator-worker multi-hop research agent harness",
)


@agent_app.init
async def initialize(self):
    global research_graph
    research_graph = build_research_graph()
    logger.info("Research harness graph initialized (orchestrator-worker)")


@agent_app.query(framework="langgraph")
async def query_func(
    self,
    msgs: List[BaseMessage],
    request: AgentRequest = None,
    **kwargs,
) -> AsyncIterator[tuple[BaseMessage, bool]]:
    question = ""
    for m in reversed(msgs):
        if hasattr(m, "content") and m.content:
            question = m.content
            break

    result = await asyncio.to_thread(
        research_graph.invoke, _make_initial_state(question)
    )
    answer = result.get("final_answer", "未能获取有效回答")
    yield AIMessage(content=answer), True


# ─── Tianchi Endpoint ────────────────────────────────────────────────

@agent_app.endpoint("/", methods=["POST"])
async def tianchi_eval_endpoint(request: Request):
    try:
        data = await request.json()
        question = data.get("question", "")
        session_id = data.get("session_id", "default")
        user_id = data.get("user_id", "default")
    except Exception:
        question, session_id, user_id = "", "default", "default"

    if not question:
        async def error_stream():
            yield f"event: Message\ndata: {json.dumps({'answer': 'Error: No question'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def event_generator():
        task = asyncio.create_task(
            asyncio.to_thread(
                research_graph.invoke,
                _make_initial_state(question, session_id=session_id, user_id=user_id),
            )
        )
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        try:
            while not task.done():
                yield "event: Ping\n\n"
                await asyncio.sleep(15)

            result = task.result()
            answer = result.get("final_answer", "") or "未能获取有效回答"
            # 可选：附带 citation 元数据（天池主评测只读 answer）
            payload = {"answer": answer}
            cites = result.get("citations") or []
            if cites:
                payload["citations"] = cites[:5]
            yield f"event: Message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"Tianchi endpoint error: {e}", exc_info=True)
            yield f"event: Message\ndata: {json.dumps({'answer': f'Error: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@agent_app.endpoint("/short-term-memory/{session_id}", methods=["GET"])
async def get_short_term_memory(session_id: str):
    return load_short_term(session_id)


@agent_app.endpoint("/long-term-memory/{user_id}", methods=["GET"])
async def get_long_term_memory(user_id: str):
    return load_long_term(user_id)
