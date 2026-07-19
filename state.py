# -*- coding: utf-8 -*-
"""
LangGraph State 定义 — Agent Harness 全链路状态。
"""
from typing import TypedDict


class ResearchState(TypedDict):
    # ── 输入 ──
    query: str
    max_loop: int
    session_id: str
    user_id: str

    # ── 预分析 ──
    preliminary_context: str
    question_hints: str
    decomposition: str
    problem_type: str

    # ── Orchestrator-Worker DAG ──
    task_dag: list          # list[SubTask dict]
    ready_batch: list       # 本轮调度的 ready 任务 id 列表
    harness_round: int      # reflection 轮次
    converge: bool          # reflection 判定可收敛

    # ── 检索与证据 ──
    findings: list          # 结构化发现
    executed_queries: list[str]
    visited_urls: list[str]
    knowledge_gap: str
    current_loop: int       # 兼容旧字段 / 与 harness_round 同步
    search_query: str

    # ── 记忆与审计 ──
    execution_trace: list
    citations: list         # [{claim, source, url}]

    # ── 输出 ──
    supplementary_context: str
    final_summary: str
    final_answer: str
    verification_report: str

    # ── 控制 ──
    start_time: float
