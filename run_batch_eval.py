# run_batch_eval.py
# -*- coding: utf-8 -*-
import argparse
import asyncio
import json
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from deepresearch.config import create_llm
from deepresearch.graph import build_deepresearch_graph
from deepresearch.tools.search_tool import build_searcher
from deepresearch.tools.fetch_tool import SimpleFetcher


def load_questions(path: str) -> List[Dict[str, Any]]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


async def run_one(graph, item: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
    question = item.get("question", "")
    msgs = [HumanMessage(content=question)]
    config = {"configurable": {"thread_id": thread_id}}
    result_state = await graph.ainvoke({"messages": msgs}, config=config)

    docs = result_state.get("documents", [])
    sources = [{"title": d.title, "url": d.url} for d in docs]

    return {
        "id": item.get("id"),
        "question": question,
        "final_answer": result_state.get("final_answer", ""),
        "final_answer_canonical": result_state.get("final_answer_canonical", ""),
        "final_answer_normalized": result_state.get("final_answer_normalized", ""),
        "queries": result_state.get("queries", []),
        "claim_queries": result_state.get("claim_queries", {}),
        "entities": result_state.get("entities", []),
        "expanded_entities": result_state.get("expanded_entities", []),
        "time_anchors": result_state.get("time_anchors", []),
        "time_queries": result_state.get("time_queries", []),
        "timeline_years": result_state.get("timeline_years", []),
        "timeline_queries": result_state.get("timeline_queries", []),
        "candidates": result_state.get("candidates", []),
        "selected_candidate": result_state.get("selected_candidate", ""),
        "candidate_scores": result_state.get("candidate_scores", []),
        "claim_verification": result_state.get("claim_verification", []),
        "missing_claims": result_state.get("missing_claims", []),
        "sources": sources,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="question.jsonl")
    parser.add_argument("--output", default="results.jsonl")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="0 means all")
    args = parser.parse_args()

    items = load_questions(args.input)
    if args.limit and args.limit > 0:
        items = items[args.start : args.start + args.limit]
    else:
        items = items[args.start :]

    llm = create_llm()
    searcher = build_searcher()
    fetcher = SimpleFetcher(timeout_s=20.0)
    graph_builder = build_deepresearch_graph(llm, searcher, fetcher)
    graph = graph_builder.compile(checkpointer=MemorySaver(), store=InMemoryStore())

    with open(args.output, "w", encoding="utf-8") as f:
        for idx, item in enumerate(items):
            thread_id = f"batch-{item.get('id', idx)}"
            try:
                out = await run_one(graph, item, thread_id)
                f.write(json.dumps(out, ensure_ascii=False) + "\n")
            except Exception as exc:
                err = {
                    "id": item.get("id", idx),
                    "question": item.get("question", ""),
                    "error": str(exc),
                }
                f.write(json.dumps(err, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
