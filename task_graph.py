# -*- coding: utf-8 -*-
"""
子任务 DAG：orchestrator-worker 范式的任务图。

- 节点 = 可并行调度的子任务（检索/求证）
- 边 = depends_on 依赖
- ready = 依赖均已终态(done|failed) 且自身 pending 的任务
  （failed 不堵死下游，允许无锚点继续 / 由 reflection 补救）
"""
from __future__ import annotations

import copy
from typing import Optional

_TERMINAL = frozenset({"done", "failed", "skipped"})


def make_task(
    task_id: str,
    description: str,
    search_query: str = "",
    depends_on: Optional[list[str]] = None,
    hop: int = 1,
    kind: str = "search",  # search | verify
) -> dict:
    return {
        "id": task_id,
        "description": description,
        "search_query": search_query or description[:80],
        "depends_on": list(depends_on or []),
        "status": "pending",
        "hop": hop,
        "kind": kind,
        "result": None,
    }


def _deps_satisfied(task: dict, by_id: dict[str, dict]) -> bool:
    """依赖完成或失败均视为可解锁（失败不堵链）。"""
    for dep in task.get("depends_on") or []:
        t = by_id.get(dep)
        if not t or t.get("status") not in _TERMINAL:
            return False
    return True


def refresh_ready(tasks: list[dict]) -> list[dict]:
    """pending 且依赖终态 → ready。"""
    by_id = {t["id"]: t for t in tasks}
    out = []
    for t in tasks:
        t = dict(t)
        if t.get("status") == "pending" and _deps_satisfied(t, by_id):
            t["status"] = "ready"
        out.append(t)
    return out


def get_ready_tasks(tasks: list[dict]) -> list[dict]:
    tasks = refresh_ready(tasks)
    return [t for t in tasks if t.get("status") == "ready"]


def mark_task_done(tasks: list[dict], task_id: str, result: dict) -> list[dict]:
    out = []
    for t in tasks:
        t = dict(t)
        if t["id"] == task_id:
            t["status"] = "done"
            t["result"] = result
        out.append(t)
    return refresh_ready(out)


def mark_task_failed(tasks: list[dict], task_id: str, error: str = "") -> list[dict]:
    out = []
    for t in tasks:
        t = dict(t)
        if t["id"] == task_id:
            t["status"] = "failed"
            t["result"] = {"error": error, "candidates": [], "confidence": 0.1}
        out.append(t)
    return refresh_ready(out)


def add_tasks(tasks: list[dict], new_tasks: list[dict]) -> list[dict]:
    """追加新子任务（回溯补任务），自动去重 id。"""
    existing = {t["id"] for t in tasks}
    out = [dict(t) for t in tasks]
    for nt in new_tasks:
        if nt["id"] in existing:
            continue
        out.append(dict(nt))
        existing.add(nt["id"])
    return refresh_ready(out)


def all_terminal(tasks: list[dict]) -> bool:
    if not tasks:
        return True
    return all(t.get("status") in _TERMINAL for t in tasks)


def has_unfinished(tasks: list[dict]) -> bool:
    return any(t.get("status") in ("pending", "ready") for t in tasks)


def dag_summary(tasks: list[dict]) -> str:
    if not tasks:
        return "(empty DAG)"
    lines = []
    for t in tasks:
        deps = ",".join(t.get("depends_on") or []) or "-"
        cands = []
        if isinstance(t.get("result"), dict):
            cands = t["result"].get("candidates") or []
        ans = cands[0] if cands else ""
        lines.append(
            f"  [{t.get('status')}] {t['id']} hop={t.get('hop', '?')} "
            f"deps=[{deps}] q={t.get('search_query', '')[:50]} → {ans}"
        )
    return "\n".join(lines)


def build_initial_dag_from_steps(steps: list[dict]) -> list[dict]:
    """
    steps: [{description, search_query, depends_on?}, ...]
    默认串行依赖；若 step 自带 depends_on 则尊重之（空列表=可并行）。
    """
    tasks = []
    prev_id = None
    for i, s in enumerate(steps, 1):
        tid = f"t{i}"
        if "depends_on" in s and s["depends_on"] is not None:
            deps = list(s.get("depends_on") or [])
        else:
            deps = [prev_id] if prev_id else []
        tasks.append(
            make_task(
                task_id=tid,
                description=str(s.get("description") or s.get("sub_question") or f"step {i}"),
                search_query=str(s.get("search_query") or s.get("query") or "")[:120],
                depends_on=deps,
                hop=i,
                kind="search",
            )
        )
        prev_id = tid
    # 过滤不存在的依赖，避免永远无法 ready
    ids = {t["id"] for t in tasks}
    cleaned = []
    for t in tasks:
        t = dict(t)
        t["depends_on"] = [d for d in (t.get("depends_on") or []) if d in ids]
        cleaned.append(t)
    return refresh_ready(cleaned)


def clone_tasks(tasks: list[dict]) -> list[dict]:
    return copy.deepcopy(tasks or [])


def completed_results(tasks: list[dict]) -> list[dict]:
    out = []
    for t in tasks or []:
        if t.get("status") == "done" and isinstance(t.get("result"), dict):
            r = dict(t["result"])
            r.setdefault("sub_query", t.get("search_query", ""))
            r.setdefault("task_id", t["id"])
            out.append(r)
    return out


def next_task_id(tasks: list[dict], prefix: str = "r") -> str:
    n = 1
    ids = {t["id"] for t in tasks}
    while f"{prefix}{n}" in ids:
        n += 1
    return f"{prefix}{n}"


def upstream_anchors(tasks: list[dict], task: dict, min_conf: float = 0.55) -> list[str]:
    """从该任务直接依赖的 done 结果中取高 conf 候选作为锚点。"""
    by_id = {t["id"]: t for t in tasks}
    anchors = []
    for dep in task.get("depends_on") or []:
        t = by_id.get(dep)
        if not t or t.get("status") != "done":
            continue
        r = t.get("result") or {}
        cands = r.get("candidates") or []
        conf = float(r.get("confidence") or 0)
        if cands and conf >= min_conf:
            a = str(cands[0]).strip()
            if a and a not in anchors:
                anchors.append(a)
    return anchors
