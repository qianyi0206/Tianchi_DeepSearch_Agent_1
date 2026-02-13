# -*- coding: utf-8 -*-
import os
import uuid
from typing import AsyncIterator, List

from agentscope_runtime.engine import AgentApp
from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest
from langchain.agents import AgentState, create_agent
from langchain.tools import tool
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

# Set environment variables for trace
os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_OTEL_ONLY"] = "true"

short_term_memory: BaseCheckpointSaver = None
long_term_memory: BaseStore = None

# Create the AgentApp instance
agent_app = AgentApp(
    app_name="LangGraphAgent",
    app_description="A LangGraph-based research assistant",
)


@tool
def get_weather(location: str, date: str) -> str:
    """Get the weather for a location and date."""
    print(f"Getting weather for {location} on {date}...")
    return f"The weather in {location} is sunny with a temperature of 25°C."


class CustomAgentState(AgentState):
    user_id: str
    session_id: dict


@agent_app.init
async def initialize(self):
    """
    Initialize agent, only will be called once when the AgentApp is started.
    """

    global short_term_memory
    global long_term_memory

    short_term_memory = MemorySaver()
    long_term_memory = InMemoryStore()

    tools = [get_weather]
    llm = ChatOpenAI(
        model="qwen-plus",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    prompt = """You are a proactive research assistant. """
    self.agent = create_agent(
        llm,
        tools,
        system_prompt=prompt,
        checkpointer=short_term_memory,
        store=long_term_memory,
        state_schema=CustomAgentState,
        name="LangGraphAgent",
    )


@agent_app.query(framework="langgraph")
async def query_func(
    self,
    msgs: List[BaseMessage],
    request: AgentRequest = None,
    **kwargs,
) -> AsyncIterator[tuple[BaseMessage, bool]]:
    session_id = request.session_id
    user_id = request.user_id
    print(f"Received query from user {user_id} with session {session_id}")
    memory_namespace = (user_id, "memories")

    async for chunk, meta_data in self.agent.astream(
        input={"messages": msgs, "session_id": session_id, "user_id": user_id},
        stream_mode="messages",
        config={"configurable": {"thread_id": session_id}},
    ):
        is_last_chunk = (
            True if getattr(chunk, "chunk_position", "") == "last" else False
        )
        if meta_data["langgraph_node"] == "tools":
            memory_id = str(uuid.uuid4())
            memory = {"latest_tool_call": chunk.name}
            long_term_memory.put(
                memory_namespace,
                memory_id,
                memory,
            )
        yield chunk, is_last_chunk


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
    namespace_for_long_term_memory = (user_id, "memories")
    long_term_mem = long_term_memory.search(namespace_for_long_term_memory)

    def serialize_search_item(item):
        return {
            "namespace": item.namespace,
            "key": item.key,
            "value": item.value,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "score": item.score,
        }

    serialized = [serialize_search_item(item) for item in long_term_mem]
    return serialized

if __name__ == "__main__":
    agent_app.run()
