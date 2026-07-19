# -*- coding: utf-8 -*-
"""
可审计执行记忆：请求内 trace + 进程内 session 持久化。

- record_step / format_trace：注入 reasoning / finalize
- save_session / load_session：供 memory HTTP 端点与跨轮复用
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

_lock = threading.Lock()
# session_id -> {messages, metadata, traces, updated_at}
_SHORT_TERM: dict[str, dict] = {}
# user_id -> list of durable facts / past Q&A summaries
_LONG_TERM: dict[str, list] = {}


def record_step(
    loop: int,
    search_query: str,
    result: dict,
    task_id: str = "",
    sources_extra: Optional[list] = None,
) -> dict:
    return {
        "loop": loop,
        "task_id": task_id,
        "query": search_query,
        "candidates": (result.get("candidates") or [])[:3],
        "confidence": result.get("confidence", 0),
        "evidence": (result.get("evidence") or [])[:2],
        "sources": (sources_extra or result.get("sources") or [])[:3],
        "triangulated": bool(result.get("triangulated")),
        "credibility": result.get("credibility", 0),
        "ts": time.time(),
    }


def format_execution_trace(trace: list) -> str:
    if not trace:
        return ""
    parts = ["【执行记忆 / Audit Trail】"]
    for step in trace:
        cands = step.get("candidates") or []
        conf = step.get("confidence", 0)
        q = step.get("query", "")
        ans = cands[0] if cands else "(未确定)"
        flag = "✓" if cands else "○"
        tri = " △" if step.get("triangulated") else ""
        tid = step.get("task_id") or "?"
        parts.append(
            f"  [L{step.get('loop', '?')}/{tid}] {flag}{tri} {q}: {ans} "
            f"(conf={float(conf):.2f})"
        )
    return "\n".join(parts)


def save_session(
    session_id: str,
    *,
    question: str = "",
    answer: str = "",
    execution_trace: Optional[list] = None,
    findings: Optional[list] = None,
    citations: Optional[list] = None,
    task_dag: Optional[list] = None,
    user_id: str = "default",
) -> None:
    if not session_id:
        session_id = "default"
    payload = {
        "session_id": session_id,
        "messages": [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ],
        "metadata": {
            "findings_count": len(findings or []),
            "trace_steps": len(execution_trace or []),
            "citations": citations or [],
            "updated_at": time.time(),
        },
        "execution_trace": execution_trace or [],
        "task_dag": task_dag or [],
        "findings": findings or [],
    }
    with _lock:
        _SHORT_TERM[session_id] = payload
        # 长期记忆：压缩为一条可复用事实
        facts = _LONG_TERM.setdefault(user_id, [])
        if answer and question:
            facts.append({
                "question": question[:200],
                "answer": answer[:200],
                "citations": (citations or [])[:5],
                "ts": time.time(),
            })
            _LONG_TERM[user_id] = facts[-50:]  # 保留最近 50 条


def load_short_term(session_id: str) -> dict:
    with _lock:
        data = _SHORT_TERM.get(session_id)
        if not data:
            return {"session_id": session_id, "messages": [], "metadata": {}}
        return {
            "session_id": session_id,
            "messages": data.get("messages", []),
            "metadata": data.get("metadata", {}),
            "execution_trace": data.get("execution_trace", []),
        }


def load_long_term(user_id: str) -> list:
    with _lock:
        return list(_LONG_TERM.get(user_id, []))


def reuse_entities_from_memory(user_id: str = "default", limit: int = 5) -> list[str]:
    """从长期记忆抽出历史答案实体，供查询锚定（可选增强）。"""
    items = load_long_term(user_id)
    out = []
    for it in reversed(items):
        a = (it.get("answer") or "").strip()
        if a and a not in out:
            out.append(a)
        if len(out) >= limit:
            break
    return out
