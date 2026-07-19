# -*- coding: utf-8 -*-
"""
Agent Harness 控制面：orchestrator-worker + Reflection + Chain-of-Verification。

编排循环：
  orchestrator → worker_pool(并行) → reflection → (continue | converge)
  → supplementary → finalize → chain_of_verification → answer_extraction
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import HumanMessage, SystemMessage

from config import (
    get_flash_llm,
    TIME_BUDGET_SECS, MAX_SEARCH_LOOPS,
    MAX_PARALLEL_WORKERS, MAX_REFLECTION_NEW_TASKS,
    ENABLE_CHAIN_OF_VERIFICATION, COVE_MAX_QUESTIONS, COVE_SKIP_CONF,
    ANCHOR_MIN_CONF,
)
from state import ResearchState
from task_graph import (
    make_task, get_ready_tasks, mark_task_done, mark_task_failed,
    add_tasks, all_terminal, has_unfinished, dag_summary,
    build_initial_dag_from_steps, next_task_id, refresh_ready,
    upstream_anchors,
)
from memory import record_step, format_execution_trace
from tools import (
    worker_execute_subtask, _format_findings, _safe_json_obj,
    build_citations,
)

logger = logging.getLogger("research_agent")


# ─── 从 decomposition / JSON 构建初始 DAG ───────────────────────────

def steps_from_decomposition_text(decomposition: str, question: str) -> list[dict]:
    """无二次 LLM：正则 + 兜底从计划文本抽 steps。"""
    steps = []
    for m in re.finditer(
        r"Step\s*(\d+)\s*[:：]\s*(.+?)(?:→|Suggested query[:：]\s*|查询[:：]\s*)(.+?)(?:\n|$)",
        decomposition or "",
        flags=re.I,
    ):
        steps.append({
            "description": m.group(2).strip()[:200],
            "search_query": m.group(3).strip().strip("[]\"'")[:120],
        })
    if not steps:
        # 尝试 JSON 数组
        m = re.search(r"\[[\s\S]*\]", decomposition or "")
        if m:
            try:
                arr = json.loads(m.group(0))
                for item in arr:
                    if isinstance(item, dict):
                        steps.append({
                            "description": str(item.get("description") or item.get("sub_question") or ""),
                            "search_query": str(item.get("search_query") or item.get("query") or "")[:120],
                            "depends_on": item.get("depends_on"),
                        })
            except Exception:
                pass
    if not steps:
        steps = [{"description": question[:200], "search_query": question[:80]}]
    return steps[:5]


def parse_plan_to_dag(decomposition: str, question: str) -> list[dict]:
    """兼容入口：文本/JSON → DAG（不再额外调 LLM）。"""
    return build_initial_dag_from_steps(steps_from_decomposition_text(decomposition, question))


def build_dag_from_steps(steps: list, question: str) -> list[dict]:
    if not steps:
        steps = [{"description": question[:200], "search_query": question[:80]}]
    cleaned = []
    for s in steps[:5]:
        if not isinstance(s, dict):
            continue
        cleaned.append({
            "description": str(s.get("description") or s.get("sub_question") or ""),
            "search_query": str(s.get("search_query") or s.get("query") or "")[:120],
            "depends_on": s.get("depends_on"),
        })
    if not cleaned:
        cleaned = [{"description": question[:200], "search_query": question[:80]}]
    return build_initial_dag_from_steps(cleaned)


# ─── Orchestrator ───────────────────────────────────────────────────

def orchestrator(state: ResearchState) -> dict:
    tasks = refresh_ready(list(state.get("task_dag") or []))
    harness_round = int(state.get("harness_round") or state.get("current_loop") or 1)
    max_loop = int(state.get("max_loop") or MAX_SEARCH_LOOPS)
    elapsed = time.time() - state.get("start_time", time.time())

    if state.get("converge"):
        logger.info("[orchestrator] already converge")
        return {"task_dag": tasks, "ready_batch": [], "converge": True}

    if elapsed > TIME_BUDGET_SECS:
        logger.warning(f"[orchestrator] time budget ({elapsed:.0f}s), force converge")
        return {"task_dag": tasks, "ready_batch": [], "converge": True}

    # 轮次耗尽：仅当无未完成任务时收敛；仍有 ready/pending 则再给最后一批
    if harness_round > max_loop:
        if not has_unfinished(tasks):
            logger.info(f"[orchestrator] rounds exhausted ({harness_round}>{max_loop})")
            return {"task_dag": tasks, "ready_batch": [], "converge": True}
        logger.info(f"[orchestrator] over max_loop but unfinished remain, drain ready batch")

    ready = get_ready_tasks(tasks)
    batch = ready[:MAX_PARALLEL_WORKERS]
    batch_ids = [t["id"] for t in batch]

    # 锚点：仅注入「直接上游 done + conf 达标」的实体
    updated = []
    for t in tasks:
        t = dict(t)
        if t["id"] in batch_ids:
            anchors = upstream_anchors(tasks, t, min_conf=ANCHOR_MIN_CONF)
            if anchors:
                sq = (t.get("search_query") or "").strip()
                anchor = anchors[-1]
                # 已包含则不重复；中文用子串判断
                if anchor not in sq:
                    t["search_query"] = f"{anchor} {sq}".strip()[:120]
        updated.append(t)
    tasks = updated
    batch = [t for t in tasks if t["id"] in batch_ids]

    logger.info(
        f"[orchestrator] round={harness_round} ready={batch_ids}\n{dag_summary(tasks)}"
    )

    if not batch_ids:
        return {
            "task_dag": tasks,
            "ready_batch": [],
            "converge": True,
            "current_loop": harness_round,
        }

    return {
        "task_dag": tasks,
        "ready_batch": batch_ids,
        "converge": False,
        "current_loop": harness_round,
        "search_query": batch[0].get("search_query", "") if batch else "",
    }


def should_run_workers(state: ResearchState) -> str:
    if state.get("converge") or not state.get("ready_batch"):
        return "converge_exit"
    return "run_workers"


# ─── Worker Pool ─────────────────────────────────────────────────────

def worker_pool(state: ResearchState) -> dict:
    tasks = list(state.get("task_dag") or [])
    batch_ids = list(state.get("ready_batch") or [])
    query = state["query"]
    findings = list(state.get("findings") or [])
    executed = list(state.get("executed_queries") or [])
    visited = list(state.get("visited_urls") or [])
    trace = list(state.get("execution_trace") or [])
    harness_round = int(state.get("harness_round") or state.get("current_loop") or 1)

    by_id = {t["id"]: dict(t) for t in tasks}
    batch_tasks = [by_id[i] for i in batch_ids if i in by_id]

    def _run_one(task: dict) -> tuple[str, dict]:
        sq = task.get("search_query") or task.get("description") or ""
        # 锚点仅来自直接上游
        anchors = upstream_anchors(tasks, task, min_conf=ANCHOR_MIN_CONF)
        logger.info(f"[worker] start {task['id']} q={sq!r} anchors={anchors}")
        result = worker_execute_subtask(
            search_focus=sq,
            original_question=query,
            confirmed_entities=anchors,
            executed_queries=executed,
            expand=True,
        )
        result["task_id"] = task["id"]
        result["sub_query"] = sq
        return task["id"], result

    results_map: dict[str, dict] = {}
    workers = min(MAX_PARALLEL_WORKERS, max(1, len(batch_tasks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_run_one, t) for t in batch_tasks]
        for fut in as_completed(futs):
            try:
                tid, result = fut.result()
                results_map[tid] = result
            except Exception as e:
                logger.error(f"[worker] failed: {e}", exc_info=True)

    for t in batch_tasks:
        tid = t["id"]
        if tid in results_map:
            result = results_map[tid]
            tasks = mark_task_done(tasks, tid, result)
            findings.append(result)
            for q in result.get("tried_queries") or [result.get("sub_query")]:
                if q and q not in executed:
                    executed.append(q)
            for u in result.get("sources") or []:
                if u not in visited:
                    visited.append(u)
            trace.append(record_step(
                loop=harness_round,
                search_query=result.get("sub_query", ""),
                result=result,
                task_id=tid,
            ))
            logger.info(
                f"[worker] done {tid} conf={result.get('confidence', 0):.2f} "
                f"tri={result.get('triangulated')} cands={result.get('candidates', [])[:2]}"
            )
        else:
            tasks = mark_task_failed(tasks, tid, "worker exception")

    return {
        "task_dag": tasks,
        "findings": findings,
        "executed_queries": executed,
        "visited_urls": visited,
        "execution_trace": trace,
        "ready_batch": [],
        "knowledge_gap": state.get("knowledge_gap") or query,
    }


# ─── Reflection ──────────────────────────────────────────────────────

_REFLECTION_PROMPT = """\
You are the Reflection module of a multi-hop research harness.
Analyze whether evidence is sufficient to answer the question, or whether new sub-tasks are needed.

Original question: {question}

Decomposition plan:
{decomposition}

Task DAG status:
{dag}

Findings so far:
{findings}

Execution memory:
{memory}

Knowledge gap note: {gap}

Decide ONE action:
1) "converge" — evidence is sufficient OR no meaningful new search possible OR findings stopped improving
2) "add_tasks" — critical missing hop; propose 1-{max_new} NEW sub-tasks (not duplicates of done queries)
3) "continue" — DAG still has pending/ready work (no new tasks needed)

Output JSON only:
{{
  "action": "converge" | "add_tasks" | "continue",
  "reason": "brief",
  "knowledge_gap": "what is still missing",
  "new_tasks": [
    {{"description": "...", "search_query": "...", "depends_on": []}}
  ]
}}
Rules:
- If DAG still has pending/ready tasks, prefer "continue" over "converge"
- search_query: 3-8 keywords; include confirmed entity names when available
- Do NOT repeat queries already tried
"""


def reflection(state: ResearchState) -> dict:
    query = state["query"]
    tasks = list(state.get("task_dag") or [])
    findings = state.get("findings") or []
    harness_round = int(state.get("harness_round") or state.get("current_loop") or 1)
    max_loop = int(state.get("max_loop") or MAX_SEARCH_LOOPS)
    elapsed = time.time() - state.get("start_time", time.time())
    executed = state.get("executed_queries") or []

    # 时间硬兜底
    if elapsed > TIME_BUDGET_SECS:
        logger.warning("[reflection] time budget → converge")
        return {
            "converge": True,
            "harness_round": harness_round + 1,
            "current_loop": harness_round + 1,
            "knowledge_gap": "time budget exhausted",
        }

    unfinished = has_unfinished(tasks)

    # 轮次兜底：有未完成任务时不因 round 强制停（交给 orchestrator drain）
    if harness_round >= max_loop and not unfinished:
        logger.info(f"[reflection] max rounds & terminal → converge")
        return {
            "converge": True,
            "harness_round": harness_round + 1,
            "current_loop": harness_round + 1,
            "knowledge_gap": "max reflection rounds",
        }

    # 无新增信息：仅在 DAG 已终态时收敛
    recent = findings[-3:] if findings else []
    if (
        recent
        and all(isinstance(f, dict) and not f.get("candidates") for f in recent)
        and harness_round >= 2
        and not unfinished
    ):
        logger.info("[reflection] no new candidates & terminal → converge")
        return {
            "converge": True,
            "harness_round": harness_round + 1,
            "current_loop": harness_round + 1,
            "knowledge_gap": "no new information",
        }

    # DAG 仍有 ready：直接 continue，省一次 reflection LLM
    if get_ready_tasks(tasks):
        logger.info("[reflection] ready tasks remain → continue without LLM")
        return {
            "task_dag": tasks,
            "converge": False,
            "knowledge_gap": state.get("knowledge_gap") or query,
            "harness_round": harness_round + 1,
            "current_loop": harness_round + 1,
            "ready_batch": [],
        }

    memory = format_execution_trace(state.get("execution_trace") or [])
    prompt = _REFLECTION_PROMPT.format(
        question=query,
        decomposition=(state.get("decomposition") or "")[:2000],
        dag=dag_summary(tasks),
        findings=_format_findings(findings)[:4000],
        memory=memory[:1500],
        gap=state.get("knowledge_gap") or "",
        max_new=MAX_REFLECTION_NEW_TASKS,
    )

    action = "continue"
    new_tasks_spec = []
    gap = state.get("knowledge_gap") or query
    reason = ""

    try:
        resp = get_flash_llm(temperature=0, max_tokens=600).invoke(
            [SystemMessage(content=prompt), HumanMessage(content="Output JSON only.")]
        )
        obj = _safe_json_obj(resp.content)
        action = str(obj.get("action", "continue")).lower().strip()
        gap = str(obj.get("knowledge_gap") or gap)
        reason = str(obj.get("reason") or "")
        new_tasks_spec = obj.get("new_tasks") or []
        if not isinstance(new_tasks_spec, list):
            new_tasks_spec = []
    except Exception as e:
        logger.warning(f"[reflection] LLM failed ({e}), heuristic fallback")
        action = "add_tasks" if all_terminal(tasks) and harness_round < max_loop else (
            "continue" if unfinished else "converge"
        )

    # 未完成任务时禁止错误 converge
    if action == "converge" and unfinished:
        action = "continue"
        reason = (reason + " | override: unfinished DAG").strip(" |")

    converge = action == "converge"
    updated_tasks = tasks

    if action == "add_tasks" and new_tasks_spec:
        built = []
        for spec in new_tasks_spec[:MAX_REFLECTION_NEW_TASKS]:
            if not isinstance(spec, dict):
                continue
            sq = str(spec.get("search_query") or "").strip()
            if not sq or sq in executed:
                continue
            # gap-fill 禁止整题前 80 字当 query
            if sq.strip() == (query[:80] or "").strip() and len(query) > 40:
                continue
            tid = next_task_id(updated_tasks + built, prefix="r")
            deps = spec.get("depends_on") or []
            if not isinstance(deps, list):
                deps = []
            ids = {t["id"] for t in updated_tasks}
            deps = [d for d in deps if d in ids]
            built.append(make_task(
                task_id=tid,
                description=str(spec.get("description") or sq),
                search_query=sq[:120],
                depends_on=deps,
                hop=harness_round + 1,
                kind="search",
            ))
        if built:
            updated_tasks = add_tasks(updated_tasks, built)
            converge = False
            logger.info(f"[reflection] add_tasks: {[t['id'] for t in built]} ({reason})")
        else:
            converge = all_terminal(updated_tasks)
    elif action == "continue":
        if all_terminal(updated_tasks):
            # 终态 continue：用 knowledge_gap 短 query 补一轮（禁止整题）
            gap_q = (gap or "").strip()
            if len(gap_q) > 80:
                gap_q = gap_q[:80]
            if (
                gap_q
                and gap_q not in executed
                and gap_q != query[:80]
                and harness_round < max_loop
            ):
                tid = next_task_id(updated_tasks, prefix="g")
                updated_tasks = add_tasks(updated_tasks, [make_task(
                    task_id=tid,
                    description=f"Fill gap: {gap_q[:120]}",
                    search_query=gap_q,
                    depends_on=[],
                    hop=harness_round + 1,
                )])
                converge = False
                logger.info(f"[reflection] gap-fill task {tid}: {gap_q!r}")
            else:
                converge = True
        else:
            converge = False
    else:
        converge = True

    logger.info(
        f"[reflection] action={action} converge={converge} "
        f"round→{harness_round + 1} reason={reason[:80]}"
    )
    return {
        "task_dag": updated_tasks,
        "converge": converge,
        "knowledge_gap": gap,
        "harness_round": harness_round + 1,
        "current_loop": harness_round + 1,
        "ready_batch": [],
    }


def should_continue_harness(state: ResearchState) -> str:
    elapsed = time.time() - state.get("start_time", time.time())
    if elapsed > TIME_BUDGET_SECS:
        return "exit_loop"
    if state.get("converge"):
        return "exit_loop"
    tasks = refresh_ready(list(state.get("task_dag") or []))
    if get_ready_tasks(tasks) or any(t.get("status") == "pending" for t in tasks):
        return "continue_loop"
    return "exit_loop"


# ─── Chain-of-Verification ───────────────────────────────────────────

_COVE_GEN_PROMPT = """\
You are running Chain-of-Verification (CoVe) for a multi-hop answer.
Generate {n} short verification questions that would FALSIFY the draft if wrong.

Question: {question}
Draft answer: {draft}
Evidence findings:
{findings}

Output JSON only:
{{"verification_questions": ["q1", "q2"]}}
"""

_COVE_CHECK_PROMPT = """\
Verify draft answer against evidence.

Question: {question}
Draft answer: {draft}
Verification Q&A:
{qa}

Findings:
{findings}

Output JSON only:
{{
  "verdict": "support" | "revise" | "uncertain",
  "final_answer": "answer only (revised if needed)",
  "reason": "brief",
  "citations": [{{"claim": "...", "source": "url or evidence snippet"}}]
}}
"""


def _should_skip_cove(findings: list, draft: str) -> bool:
    if not draft or not findings:
        return False
    best_conf = 0.0
    any_tri = False
    for f in findings:
        if not isinstance(f, dict):
            continue
        best_conf = max(best_conf, float(f.get("confidence") or 0))
        if f.get("triangulated"):
            any_tri = True
    return best_conf >= COVE_SKIP_CONF and any_tri


def chain_of_verification(state: ResearchState) -> dict:
    findings = state.get("findings") or []
    draft = state.get("final_answer") or ""

    if not ENABLE_CHAIN_OF_VERIFICATION:
        return {
            "verification_report": "disabled",
            "citations": build_citations(findings, draft),
        }

    if _should_skip_cove(findings, draft):
        cites = build_citations(findings, draft)
        logger.info("[cove] skip (high conf + triangulated)")
        return {
            "verification_report": "skipped_high_confidence",
            "citations": cites,
        }

    query = state["query"]
    findings_text = _format_findings(findings)[:5000]
    report_parts = []

    vqs = []
    try:
        resp = get_flash_llm(temperature=0, max_tokens=300).invoke(
            _COVE_GEN_PROMPT.format(
                n=COVE_MAX_QUESTIONS,
                question=query,
                draft=draft,
                findings=findings_text[:3000],
            )
        )
        obj = _safe_json_obj(resp.content)
        vqs = [str(q).strip() for q in (obj.get("verification_questions") or []) if str(q).strip()]
        vqs = vqs[:COVE_MAX_QUESTIONS]
    except Exception as e:
        logger.warning(f"[cove] gen questions failed: {e}")
        vqs = [
            f"What evidence supports that the answer is {draft}?",
            "Is there a conflicting entity that better matches all constraints?",
        ]

    qa_lines = []
    for vq in vqs:
        ans = _answer_from_findings(vq, findings)
        qa_lines.append(f"Q: {vq}\nA: {ans}")
        report_parts.append(f"VQ: {vq} → {ans}")

    new_answer = draft
    citations = build_citations(findings, draft)
    verdict = "support"

    try:
        resp = get_flash_llm(temperature=0, max_tokens=500).invoke(
            _COVE_CHECK_PROMPT.format(
                question=query,
                draft=draft,
                qa="\n\n".join(qa_lines)[:2500],
                findings=findings_text[:3000],
            )
        )
        obj = _safe_json_obj(resp.content)
        verdict = str(obj.get("verdict", "support")).lower()
        fa = str(obj.get("final_answer") or "").strip()
        if fa and verdict == "revise":
            new_answer = fa
        elif fa and not draft:
            new_answer = fa
        cite_in = obj.get("citations") or []
        if isinstance(cite_in, list) and cite_in:
            merged = []
            for c in cite_in[:8]:
                if isinstance(c, dict):
                    merged.append({
                        "claim": str(c.get("claim", ""))[:200],
                        "source": str(c.get("source", ""))[:300],
                        "evidence": str(c.get("evidence", c.get("source", "")))[:300],
                    })
            if merged:
                citations = merged + [c for c in citations if c not in merged]
        report_parts.append(f"verdict={verdict} reason={obj.get('reason', '')}")
    except Exception as e:
        logger.warning(f"[cove] check failed: {e}")
        report_parts.append(f"check_failed={e}")

    if new_answer:
        citations = build_citations(findings, new_answer) or citations

    report = " | ".join(report_parts)[:2000]
    logger.info(f"[cove] verdict={verdict} answer={new_answer!r}")

    # 记忆只在 answer_extraction 最终写一次
    out = {
        "verification_report": report,
        "citations": citations[:12],
        "final_summary": (state.get("final_summary") or "") + f"\n\n[CoVe] {report}",
    }
    if new_answer:
        out["final_answer"] = new_answer
    return out


def _answer_from_findings(question: str, findings: list) -> str:
    if not findings:
        return "NO_EVIDENCE"
    terms = [t for t in re.split(r"\s+", question.lower()) if len(t) >= 3]
    # 中文：按字 bigram 弱匹配
    if re.search(r"[\u4e00-\u9fff]", question):
        terms += [question[i:i + 2] for i in range(len(question) - 1) if "\u4e00" <= question[i] <= "\u9fff"]
    best, best_s = None, -1
    for f in findings:
        if not isinstance(f, dict):
            continue
        blob = " ".join(
            [str(x) for x in (f.get("candidates") or [])]
            + [str(x) for x in (f.get("evidence") or [])]
        ).lower()
        score = sum(1 for t in terms if t and t in blob)
        conf = float(f.get("confidence") or 0)
        score = score + conf  # 同分偏高 conf
        if score > best_s:
            best_s, best = score, f
    if not best:
        return "NO_EVIDENCE"
    cands = best.get("candidates") or []
    evid = best.get("evidence") or []
    if cands:
        return f"{cands[0]} (evidence: {evid[0] if evid else 'n/a'})"
    if evid:
        return evid[0][:200]
    return "NO_EVIDENCE"
