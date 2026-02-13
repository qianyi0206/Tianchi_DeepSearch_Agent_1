# run_one_eval.py
# -*- coding: utf-8 -*-
import asyncio

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from deepresearch.config import create_llm
from deepresearch.graph import build_deepresearch_graph
from deepresearch.tools.search_tool import build_searcher
from deepresearch.tools.fetch_tool import SimpleFetcher


TEST = {
    "id": 2,
    "question": (
        "在某一年，一位法国天文学家对一颗彗星的光谱进行了开创性观测，同年的一张太阳黑子照片后来在东亚某大都市的天文展览中展出。"
        "也是在这一年，一位尚不满二十岁的南欧创业者，在家乡小镇创办了他的出版事业。十余年后，他将公司总部迁往了该国北部的商业中心。"
        "他所创立的这家出版公司的名字是什么？"
    ),
}


def _safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        safe = text.encode("gbk", errors="ignore").decode("gbk", errors="ignore")
        print(safe)


async def main():
    llm = create_llm()
    searcher = build_searcher()
    fetcher = SimpleFetcher(timeout_s=20.0)

    graph = build_deepresearch_graph(llm, searcher, fetcher).compile(
        checkpointer=MemorySaver(),
        store=InMemoryStore(),
    )

    msgs = [HumanMessage(content=TEST["question"])]
    config = {"configurable": {"thread_id": f"test-{TEST['id']}"}}
    result_state = await graph.ainvoke({"messages": msgs}, config=config)

    _safe_print("\n====== [parse_claims 输出] claims ======")
    for c in result_state.get("claims", []):
        _safe_print(f"{c.id}: {c.description}")

    _safe_print("\n====== [plan_queries 输出] queries ======")
    for q in result_state.get("queries", []):
        _safe_print(f"- {q}")

    _safe_print("\n====== [plan_queries 输出] claim_queries ======")
    for cid, qlist in result_state.get("claim_queries", {}).items():
        _safe_print(f"{cid}:")
        for q in qlist:
            _safe_print(f"  - {q}")

    _safe_print("\n====== [retrieve 输出] documents URLs ======")
    docs = result_state.get("documents", [])
    for i, d in enumerate(docs, start=1):
        title = (d.title or "").strip().replace("\n", " ")
        _safe_print(f"[S{i}] {title} | {d.url}")

    _safe_print("\n====== [finalize 输出] final_answer ======")
    _safe_print(result_state.get("final_answer", ""))
    _safe_print(f"[canonical] {result_state.get('final_answer_canonical', '')}")
    _safe_print(f"[normalized] {result_state.get('final_answer_normalized', '')}")


if __name__ == "__main__":
    asyncio.run(main())

