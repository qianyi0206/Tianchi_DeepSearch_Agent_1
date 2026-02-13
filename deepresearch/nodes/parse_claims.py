# deepresearch/nodes/parse_claims.py
# -*- coding: utf-8 -*-


from __future__ import annotations

import json
import os
import re
from typing import Callable, List

from langchain_core.messages import AIMessage, BaseMessage

from ..schemas import Claim
from ..state import DeepResearchState


def _extract_last_user_question(messages: List[BaseMessage]) -> str:
    # 从最后一条用户消息里取问题（简单假设：最后一条 human message 就是问题）
    # langchain_core 里 HumanMessage.content 是 str
    for m in reversed(messages):
        role = getattr(m, "type", "")
        if role == "human":
            return str(m.content).strip()
    # 如果没有 human message，就兜底取最后一条
    return str(messages[-1].content).strip() if messages else ""


def _safe_json_loads(text: str):
    """
    从模型输出中清洗得到json
    """
    # 找到第一个 '[' 到最后一个 ']' 的片段
    m = re.search(r"\[.*\]", text, flags=re.S)
    if m:
        return json.loads(m.group(0))
    # 或者找 '{...}'
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("无法从模型输出中解析 JSON")


def make_parse_claims_node(llm) -> Callable[[DeepResearchState], DeepResearchState]:
    """
    创建一个“解析约束”的图节点。

    该节点负责从用户的对话历史中提取最后的问题，并指示 LLM 将其拆解为一组可验证的约束条件（Claims）。

    Args:
        llm: 用于执行提取任务的大语言模型对象 (LangChain Runnable)。

    Returns:
        Callable[[DeepResearchState], DeepResearchState]: 
            符合 LangGraph 签名的节点函数。

            输入 State:
            - messages (List[BaseMessage]): 必须包含用户的问题（通常是最后一条 HumanMessage）。

            输出 State 更新:
            - question (str): 提取出的用户问题文本。
            - claims (List[Claim]): 拆解得到的约束列表。
            - messages (List[BaseMessage]): 追加一条表示进度的 AIMessage。
    """
    async def parse_claims(state: DeepResearchState) -> DeepResearchState:
        question = _extract_last_user_question(state.get("messages", []))

        prompt = (
            "你是一个信息抽取器。根据实际内容，把用户问题拆成多条可验证的约束(claims)。\n"
            "要求：\n"
            "1) 只输出 JSON 数组，不要输出多余文字。\n"
            "2) 每条包含字段：id, description, must。\n"
            "3) description 用中文，尽量具体，包含年份/金额/地点/关系等细节。\n"
            "4) must 一律给 true（baseline 先都当必须）。\n\n"
            f"用户问题：{question}\n"
        )

        resp = await llm.ainvoke(prompt)
        raw = str(resp.content)

        try:
            arr = _safe_json_loads(raw)
            claims = [Claim(**c) for c in arr]
        except Exception:
            # 兜底：如果解析失败，就给一个最简 claim，至少流程能跑通
            claims = [Claim(id="c1", description="从网页证据中回答该问题", must=True)]

        progress_msg = AIMessage(content=f"[parse_claims] 已解析出 {len(claims)} 条约束。")
        return {
            "question": question,
            "claims": claims,
            "messages": [progress_msg],
        }

    return parse_claims


if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv
    from langchain_core.messages import HumanMessage

    load_dotenv()

    class _DummyLLM:
        """无需外部服务的演示 LLM，返回固定 JSON。"""

        async def ainvoke(self, prompt: str) -> AIMessage:
            _ = prompt  # 演示用，不解析 prompt
            sample = (
                '[{"id":"c1","description":"示例约束：回答 Python 由谁创建","must":true},'
                '{"id":"c2","description":"示例约束：给出创建年份","must":true}]'
            )
            return AIMessage(content=sample)

    async def _demo() -> None:
        from langchain_openai import ChatOpenAI
        if os.getenv("DASHSCOPE_API_KEY"):
            print(os.getenv("DASHSCOPE_API_KEY"))
            llm = ChatOpenAI(
                model=os.getenv("DEEPRESEARCH_MODEL", "qwen-plus"),
                api_key=os.getenv("DASHSCOPE_API_KEY"),
                base_url=os.getenv(
                    "DEEPRESEARCH_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            )
            
        else:
            print("缺少对应 API Key，回退到 Dummy LLM")
            llm = _DummyLLM()

        node = make_parse_claims_node(llm)

        test_question = (
            "某艺术家A，25岁毕业于某学校B，该学校B以某中国近代作家C命名，该作家C出生于中国南方城市。"
            "该艺术家作品在2012年和2021年分别拍出了超过500万和超过2000万的高价，请问该艺术家A是谁？"
        )

        state: DeepResearchState = {
            "messages": [HumanMessage(content=test_question)],
        }

        new_state = await node(state)
        print(f"question: {new_state['question']}")
        print(f"claims: {len(new_state['claims'])}")
        for c in new_state["claims"]:
            print(f"- {c.id}: {c.description} (must={c.must})")

    asyncio.run(_demo())
