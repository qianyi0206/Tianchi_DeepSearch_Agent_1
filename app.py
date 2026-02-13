# app.py
# -*- coding: utf-8 -*-
import uuid
from typing import AsyncIterator, List

from agentscope_runtime.engine import AgentApp
from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest
from langchain_core.messages import BaseMessage

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from deepresearch.config import create_llm
from deepresearch.graph import build_deepresearch_graph
from deepresearch.tools.search_tool import build_searcher
from deepresearch.tools.fetch_tool import SimpleFetcher


short_term_memory: BaseCheckpointSaver = None
long_term_memory: BaseStore = None

agent_app = AgentApp(
    app_name="DeepResearchAPI",
    app_description="A minimal DeepResearch API (4-node graph: claims->queries->retrieve->finalize)",
)


@agent_app.init
async def initialize(self):
    """
    只初始化一次：构建 LLM、工具、图。
    """
    global short_term_memory
    global long_term_memory

    short_term_memory = MemorySaver()
    long_term_memory = InMemoryStore()

    llm = create_llm()
    searcher = build_searcher()
    fetcher = SimpleFetcher(timeout_s=20.0)

    graph_builder = build_deepresearch_graph(llm, searcher, fetcher)
    # 目前先不做复杂持久化，把 checkpointer/store 先挂上
    self.graph = graph_builder.compile(checkpointer=short_term_memory, store=long_term_memory)


@agent_app.query(framework="langgraph")
async def query_func(
    self,
    msgs: List[BaseMessage],
    request: AgentRequest = None,
    **kwargs,
) -> AsyncIterator[tuple[BaseMessage, bool]]:
    """
    最小实现：不做 token streaming，直接跑完图后返回最终 AIMessage。
    你先把“能跑通”当第一目标。
    """
    session_id = request.session_id
    user_id = request.user_id

    # LangGraph thread_id 用于短期记忆/检查点
    config = {"configurable": {"thread_id": session_id}}

    result_state = await self.graph.ainvoke(
        {"messages": msgs, "session_id": session_id, "user_id": user_id},
        config=config,
    )

    # 最后一条消息就是 finalize 写入的最终回答
    final_msg = result_state["messages"][-1]
    yield final_msg, True


@agent_app.endpoint("/short-term-memory/{session_id}", methods=["GET"])
async def get_short_term_memory(session_id: str):
    config = {"configurable": {"thread_id": session_id}}
    value = await short_term_memory.aget_tuple(config)
    if value is None:
        return {"error": "No memory found for session_id"}

    return {
        "session_id": session_id,
        "messages": value.checkpoint["channel_values"]["messages"],
        "metadata": value.metadata,
    }


@agent_app.endpoint("/long-term-memory/{user_id}", methods=["GET"])
async def get_long_term_memory(user_id: str):
    namespace = (user_id, "memories")
    items = long_term_memory.search(namespace)

    def serialize(item):
        return {
            "namespace": item.namespace,
            "key": item.key,
            "value": item.value,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "score": item.score,
        }

    return [serialize(i) for i in items]
