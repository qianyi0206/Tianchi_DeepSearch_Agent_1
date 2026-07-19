# -*- coding: utf-8 -*-
"""
LangGraph 节点函数。
每个函数对应图中的一个节点，接收 ResearchState，返回状态更新字典。
"""
import re
import time
import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage

from config import (
    get_llm, get_flash_llm,
)
from state import ResearchState
from tools import (
    _safe_exa_search, _safe_exa_contents,
    _safe_serper_search, _format_serper_context,
    _safe_wiki_summary, _safe_baike_summary, _safe_jina_scrape,
    _format_findings, _safe_json_obj,
    build_citations,
)
from plan_tips import get_plan_tips, format_tips_for_prompt
from memory import format_execution_trace, save_session
from task_graph import build_initial_dag_from_steps

logger = logging.getLogger("research_agent")


# ─── Execution Memory ────────────────────────────────────────────
# 移植自 Tianchi_DeepSearch_Agent/deepresearch/memory.py
# 轻量版：每步 act_search 完成后记录一条快照，供 reasoning/finalize 使用。
# 避免推理链因截断丢失早期关键结论。

def _record_step(loop: int, search_query: str, result: dict) -> dict:
    """构造一步执行记录快照。"""
    return {
        "loop": loop,
        "query": search_query,
        "candidates": result.get("candidates", [])[:3],
        "confidence": result.get("confidence", 0),
        "evidence": result.get("evidence", [])[:2],
        "sources": result.get("sources", [])[:2],
    }


def _format_execution_trace(trace: list) -> str:
    """
    将执行轨迹格式化为推理链上下文。
    策略：全部步骤都输出简要摘要（candidates + confidence），
    确保早期高置信结论不被截断丢失。
    """
    if not trace:
        return ""
    parts = ["【执行记忆】"]
    for step in trace:
        cands = step.get("candidates", [])
        conf = step.get("confidence", 0)
        q = step.get("query", "")
        ans = cands[0] if cands else "(未确定)"
        status = "✓" if cands else "○"
        parts.append(f"  [Loop {step.get('loop', '?')}] {status} {q}: {ans} (conf={conf:.1f})")
    return "\n".join(parts)


# ─── Preliminary Query Extraction ────────────────────────────────────

_PRELIMINARY_QUERY_PROMPT = """\
从以下多跳研究问题中提取 2-3 个最有效的搜索短语。
问题通常用间接描述来指代实体，你需要识别出可搜索的关键线索。

问题：{question}

规则：
1. 每个短语 3-8 个关键词，必须是可以直接搜索的
2. 优先提取最独特、最可能命中的线索（如年份+事件、特殊属性等）
3. 匹配问题语言：中文线索→中文短语，英文线索→英文短语
4. 不要照抄原文，要把间接描述转化为直接可搜的关键词

输出 JSON 数组：["搜索短语1", "搜索短语2"]"""


def _extract_preliminary_queries(question: str) -> list[str]:
    """用 Flash LLM 从间接描述的多跳问题中提取可搜索的关键短语。"""
    try:
        llm = get_flash_llm(temperature=0, max_tokens=200)
        resp = llm.invoke(_PRELIMINARY_QUERY_PROMPT.format(question=question))
        raw = resp.content.strip()
        arr_match = re.search(r'\[[\s\S]*?\]', raw)
        if arr_match:
            queries = json.loads(arr_match.group(0))
            return [str(q).strip() for q in queries if str(q).strip()][:3]
    except Exception as e:
        logger.warning(f"[_extract_preliminary_queries] failed: {e}")
    # fallback：截取问题前80字符作为搜索词
    return [question[:80]]


# ─── Prompts ─────────────────────────────────────────────────────────

_FINALIZE_PROMPT = """\
You are synthesizing multi-hop research findings to answer a specific question.
Trace the reasoning chain step by step and output structured JSON.

Question: {question}

Research Findings (structured):
{findings_text}

Supplementary Search:
{supplementary}

Source URLs: {sources}

Instructions:
1. Trace the COMPLETE reasoning chain: each hop → what entity was confirmed → how it connects to the next.
2. Match question language: Chinese question → Chinese answer; English → English.
3. Follow format requirements EXACTLY if stated (digits only, specific name style, no punctuation, original language, etc.).
4. Be decisive: commit to the most probable answer based on available evidence.

Output valid JSON only:
{{"reasoning": "step-by-step chain", "final_answer": "precise answer only", "confidence": 0.0}}

Rules for final_answer:
- ONLY the answer value. No "the answer is", no explanation, no punctuation unless required by format.
- confidence: 0.9=certain, 0.7=likely, 0.5=uncertain, 0.3=guessing"""

_FINALIZE_FAST_PROMPT = """\
Determine the answer to this question from the research findings.

Question: {question}

Findings:
{findings_text}

Preliminary context: {preliminary}

Output JSON only: {{"reasoning": "brief chain", "final_answer": "answer only", "confidence": 0.0}}
Be decisive. Commit to the best answer from available evidence. Output JSON only."""


# ─── Hint Generation ────────────────────────────────────────────────

_HINT_GENERATION_PROMPT = """\
Analyze the following research question WITHOUT attempting to solve it.
Identify potential challenges and provide guidance for the research process.

Question: {question}

Analyze and output concisely (3-6 bullet points):
1. **Format requirements**: What format is the answer expected in? (digits only, full name, original language, etc.)
2. **Language strategy**: Should searches use Chinese, English, or both? Which language for each hop?
3. **Potential traps**: Ambiguous terms, common confusions, entities that might be mistaken for others
4. **Search priority**: What is the most effective first search — the rarest/most unique clue in the question?
5. **Answer precision**: Any precision, rounding, unit, or naming convention requirements?

Be brief. Do NOT attempt to answer the question. Focus on pitfalls and strategy only."""


# ─── Problem Type Templates ─────────────────────────────────────
# 移植自 Tianchi_DeepSearch_Agent/deepresearch/nodes/execute_subtasks.py
# 按题型提供专属的查询设计模板（A/E/V 三层：Anchor/Evidence/Verify），
# 显著提升 reasoning 节点生成的搜索查询质量。

PROBLEM_TYPE_TEMPLATES = {
    "entity_chain": (
        "### entity_chain（多跳实体链）\n"
        "- A（Anchor）：单独用【已知最独特的实体全名】搜索\n"
        "- E（Evidence）：【确切个体名】+【空格】+【目标属性/职位/直接关系名词】\n"
        "- V（Verify）：【待验证候选】+【空格】+【约束条件(如年份/身份)】"
    ),
    "document_lookup": (
        "### document_lookup（文档/论文/定位）\n"
        "- A（Anchor）：【核心论文/文档名】+ pdf/archive/site\n"
        "- E（Evidence）：【文档名】+【空格】+【目标属性如:作者/出处/致谢】\n"
        "- V（Verify）：【文档名】+【约束年份/机构名】"
    ),
    "year_resolution": (
        "### year_resolution（年份查找）\n"
        "- A（Anchor）：【已知核心短句/特定实体】+ 年份\n"
        "- E（Evidence）：【前序推导出的确切年份】+【相关事件核心词】\n"
        "- V（Verify）：【年份候选数字】+【前后置附加约束】"
    ),
    "work_identification": (
        "### work_identification（作品/角色识别）\n"
        "- A（Anchor）：【独特的角色名或单句台词】+ 电影/游戏等载体分类\n"
        "- E（Evidence）：【前序定死的作品名】+ 导演/制作/改编词\n"
        "- V（Verify）：【候选单一作品名】+【附加的时间或平台词】"
    ),
    "science_chain": (
        "### science_chain（科学机制）\n"
        "- A（Anchor）：【核心名词:疾病/药物/分子】+ mechanism/pathway\n"
        "- E（Evidence）：【前序特定生化词】+ 表达/直接下游\n"
        "- V（Verify）：【候选单独蛋白名】+ inhibitor/副作用"
    ),
    "rule_check": (
        "### rule_check（规则/条款）\n"
        "- A（Anchor）：【精确法案名/规定名缩写】+ 条文\n"
        "- E（Evidence）：【特定法案名】+【对象属性/条件名词】\n"
        "- V（Verify）：【具体条款编号】+ 例外"
    ),
    "field_extraction": (
        "### field_extraction（字段/数据）\n"
        "- A（Anchor）：【具体统计机构/表单名】+ 年报/数据库\n"
        "- E（Evidence）：【前序定位的确切实体/文档名】+【统计字段名】\n"
        "- V（Verify）：【单独数据候选】+【特定单位】"
    ),
}

_DEFAULT_TYPE_TEMPLATE = (
    "### 通用模板\n"
    "- A（Anchor）：【最核心的具体实体】+【特征词】\n"
    "- E（Evidence）：【已确认实体名】+【目标关系名词】\n"
    "- V（Verify）：【待验证答案】+【核验约束词】"
)


_PROBLEM_TYPE_DETECT_PROMPT = """\
判断以下研究问题的题型，只输出一个词。

题型定义：
- entity_chain: 多跳实体链（通过间接描述逐步定位实体）
- document_lookup: 查找特定文档/论文/作品的属性
- year_resolution: 查找特定年份/时间
- work_identification: 识别特定作品/角色/影视
- science_chain: 科学机制/生物医学链条
- rule_check: 法规/规则/条款查询
- field_extraction: 统计数据/字段提取

问题: {question}

只输出一个题型词:"""


def _detect_problem_type(question: str) -> str:
    """用 Flash LLM 判定题型，返回 problem_type 字符串。"""
    try:
        resp = get_flash_llm(temperature=0, max_tokens=20).invoke(
            _PROBLEM_TYPE_DETECT_PROMPT.format(question=question)
        )
        t = resp.content.strip().lower().replace(" ", "_")
        for valid in PROBLEM_TYPE_TEMPLATES:
            if valid in t:
                return valid
    except Exception as e:
        logger.warning(f"[detect_problem_type] failed: {e}")
    return "entity_chain"


def _get_type_template(problem_type: str) -> str:
    """根据 problem_type 获取对应的查询设计模板。"""
    return PROBLEM_TYPE_TEMPLATES.get(problem_type, _DEFAULT_TYPE_TEMPLATE)


def _generate_hints(question: str) -> str:
    """用 Flash LLM 预分析问题的陷阱、格式要求和搜索策略。"""
    try:
        resp = get_flash_llm(temperature=0, max_tokens=400).invoke(
            _HINT_GENERATION_PROMPT.format(question=question)
        )
        hints = resp.content.strip()
        logger.info(f"[hint_generation] {len(hints)} chars")
        return hints
    except Exception as e:
        logger.warning(f"[hint_generation] failed: {e}")
        return ""


# ─── Node: Preliminary Search ────────────────────────────────────────

def _detect_search_lang(query: str) -> tuple[str, str]:
    """根据问题语言返回 (gl, hl) 供 Serper 使用。"""
    if re.search(r'[\u4e00-\u9fff]', query):
        return "cn", "zh"
    return "us", "en"


def preliminary_search(state: ResearchState) -> dict:
    query = state["query"]
    logger.info(f"[preliminary_search] {query}")

    # 1) Flash LLM 提取可搜索的关键短语（~1s）
    search_queries = _extract_preliminary_queries(query)
    logger.info(f"[preliminary_search] extracted queries: {search_queries}")

    context_parts = []
    gl, hl = _detect_search_lang(query)

    # 2) Serper (Google) —— Knowledge Graph / AnswerBox / Snippet
    serper_data = _safe_serper_search(search_queries[0], num=5, gl=gl, hl=hl)
    serper_ctx = _format_serper_context(serper_data, max_organic=3)
    if serper_ctx:
        context_parts.append(serper_ctx)
        logger.info(f"[preliminary_search] serper: {len(serper_ctx)} chars"
                     f" (KG={'knowledgeGraph' in serper_data})")

    # 3) Serper organic → Jina 抓全文（仅在 KG/AnswerBox 未命中时）
    has_direct_answer = bool(
        serper_data.get("knowledgeGraph") or serper_data.get("answerBox")
    )
    if not has_direct_answer:
        organic = serper_data.get("organic", [])[:2]
        for r in organic:
            url = r.get("link", "")
            if url:
                full_text = _safe_jina_scrape(url, max_chars=3000)
                if full_text:
                    passage = full_text[:1500]
                    context_parts.append(f"[{r.get('title', '')}]({url})\n{passage}")
                    logger.info(f"[preliminary_search] jina fetched: {url[:60]}")

    # 4) 百科查询：中文用百度百科为主 + Wikipedia 为辅；英文用 Wikipedia
    if hl == "zh":
        # 中文：先查百度百科
        for sq in search_queries[:2]:
            baike_text = _safe_baike_summary(sq, max_chars=2000)
            if baike_text:
                context_parts.append(f"[百度百科] {baike_text[:800]}")
                logger.info(f"[preliminary_search] baike hit: {sq}")
                break
        # 百度百科未命中时，回退到中文 Wikipedia
        if not any("[百度百科]" in p for p in context_parts):
            for sq in search_queries[:2]:
                wiki_text = _safe_wiki_summary(sq, sentences=5, lang="zh")
                if wiki_text:
                    context_parts.append(f"[Wikipedia] {wiki_text[:800]}")
                    logger.info(f"[preliminary_search] wiki hit: {sq}")
                    break
    else:
        # 英文：直接查 Wikipedia
        for sq in search_queries[:2]:
            wiki_text = _safe_wiki_summary(sq, sentences=5, lang="en")
            if wiki_text:
                context_parts.append(f"[Wikipedia] {wiki_text[:800]}")
                logger.info(f"[preliminary_search] wiki hit: {sq}")
                break

    # 5) Exa 语义搜索（保持原有逻辑）
    all_results = []
    seen_urls = set()
    for sq in search_queries:
        results = _safe_exa_search(sq, num_results=3, search_type="auto")
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    if all_results:
        urls = [r["url"] for r in all_results[:4]]
        pages = _safe_exa_contents(urls)
        exa_ctx = "\n\n".join(
            f"[{p['url']}]\n{p['text'][:1200]}" for p in pages
        )
        if exa_ctx:
            context_parts.append(exa_ctx)

    ctx = "\n\n".join(context_parts)
    logger.info(f"[preliminary_search] total context: {len(ctx)} chars "
                f"({len(context_parts)} sources)")

    # 6) Hint Generation：预分析问题陷阱/格式/语言策略（1次 flash 调用）
    hints = _generate_hints(query)

    return {"preliminary_context": ctx, "question_hints": hints}


# ─── Node: Question Decomposition ────────────────────────────────────

def question_decomposition(state: ResearchState) -> dict:
    query = state["query"]
    preliminary = state.get("preliminary_context", "")
    hints = state.get("question_hints", "")

    # 题型检测（~1次 flash 调用）
    problem_type = _detect_problem_type(query)
    logger.info(f"[question_decomposition] problem_type={problem_type}")

    tips = get_plan_tips(query)
    tips_block = format_tips_for_prompt(tips)
    if tips_block:
        logger.info(f"[question_decomposition] plan_tips matched: {len(tips)} tips")

    # Hint Generation 注入
    hints_block = ""
    if hints:
        hints_block = f"""
<question-analysis>
The following preliminary analysis highlights potential pitfalls, format requirements, and search strategies.
Use this to guide your decomposition — do NOT treat it as answers.
{hints}
</question-analysis>
"""
        logger.info(f"[question_decomposition] hints injected: {len(hints)} chars")

    system_prompt = f"""You are an expert at decomposing complex multi-hop research questions.
These questions use indirect descriptions to reference entities through attributes, relationships,
temporal anchors, and cross-domain links — never by name directly.

Question: {query}

<rules>
1. Trace the FULL reasoning chain (typically 3-6 hops). Each hop = one searchable fact.
2. Default dependency: step i depends on step i-1. Independent clues may use "depends_on": [].
3. search_query: 3-8 keywords; language matches the clue (Chinese clues → Chinese terms).
4. Note final answer format/language constraints in answer_format.
</rules>
{tips_block}{hints_block}
Output valid JSON only (no markdown):
{{
  "plan_text": "brief chain summary",
  "answer_format": "format/language constraints",
  "steps": [
    {{"description": "sub-question", "search_query": "keywords", "depends_on": []}},
    {{"description": "...", "search_query": "...", "depends_on": ["t1"]}}
  ]
}}
Note: depends_on uses task ids t1,t2,... matching step order. Omit or [] for first hop / parallelizable hops."""

    user_msg = f"""Background context from preliminary search: {preliminary[:3000]}

Decompose into JSON steps for a task DAG. Identify intermediate entities before the final answer."""

    decomp = ""
    steps = []
    try:
        resp = get_llm(temperature=0, max_tokens=2048).invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]
        )
        raw = resp.content.strip()
        logger.info(f"[question_decomposition] {len(raw)} chars")
        try:
            obj = _safe_json_obj(raw)
            steps = obj.get("steps") or []
            plan_text = str(obj.get("plan_text") or "")
            answer_format = str(obj.get("answer_format") or "")
            # 可读 decomposition 文本（供 reflection）
            lines = [plan_text, f"Answer format: {answer_format}"]
            for i, s in enumerate(steps, 1):
                if isinstance(s, dict):
                    lines.append(
                        f"Step {i}: {s.get('description', '')} → Suggested query: {s.get('search_query', '')}"
                    )
            decomp = "\n".join(lines)
        except Exception:
            decomp = raw
            steps = []
    except Exception as e:
        logger.warning(f"[question_decomposition] LLM failed ({e}), using fallback plan")
        decomp = (
            f"Fallback plan: search key entities.\n"
            f"Step 1: Search the key clues → Suggested query: {query[:80]}"
        )
        steps = [{"description": query[:200], "search_query": query[:80]}]

    # 一次 LLM 产物直接建 DAG（无二次 parse LLM）
    from harness import build_dag_from_steps, parse_plan_to_dag
    try:
        if steps:
            # depends_on 里的 t1 等保持；若模型写了错误依赖，build 侧会串行兜底
            task_dag = build_dag_from_steps(steps, query)
        else:
            task_dag = parse_plan_to_dag(decomp, query)
    except Exception as e:
        logger.warning(f"[question_decomposition] DAG build failed: {e}")
        task_dag = build_initial_dag_from_steps([{
            "description": query[:200],
            "search_query": query[:80],
        }])
    logger.info(f"[question_decomposition] initial DAG size={len(task_dag)}")

    return {
        "decomposition": decomp,
        "problem_type": problem_type,
        "knowledge_gap": query,
        "current_loop": 1,
        "harness_round": 1,
        "task_dag": task_dag,
        "ready_batch": [],
        "converge": False,
        "findings": [],
        "executed_queries": [],
        "visited_urls": [],
        "execution_trace": [],
        "citations": [],
        "verification_report": "",
    }


# ─── 旧 reasoning/act_search 路径已由 harness.orchestrator/worker_pool 替代 ──


# ─── Node: Supplementary Search ──────────────────────────────────────

def supplementary_search(state: ResearchState) -> dict:
    query = state["query"]

    logger.info(f"[supplementary_search] {query}")
    results = _safe_exa_search(query, num_results=5, search_type="neural")
    if not results:
        return {"supplementary_context": ""}

    urls = [r["url"] for r in results[:3]]
    pages = _safe_exa_contents(urls)

    parts = [f"[{r['title']}]({r['url']})" for r in results]
    content_parts = [f"=== {p['url']} ===\n{p['text'][:1500]}" for p in pages]
    ctx = "Search results:\n" + "\n".join(parts)
    if content_parts:
        ctx += "\n\nPage contents:\n" + "\n\n".join(content_parts)

    logger.info(f"[supplementary_search] {len(ctx)} chars")
    return {
        "supplementary_context": ctx,
        "visited_urls": state.get("visited_urls", []) + urls,
    }


# ─── Node: Finalize Summary ───────────────────────────────────────────

def finalize_summary(state: ResearchState) -> dict:
    query = state["query"]
    findings = state.get("findings", [])
    visited_urls = state.get("visited_urls", [])
    supplementary = state.get("supplementary_context", "")
    preliminary = state.get("preliminary_context", "")
    elapsed = time.time() - state.get("start_time", time.time())
    execution_trace = state.get("execution_trace", [])

    # 快速路径：仅在时间紧迫时（>7.5min）启用
    fast_path = elapsed > 450
    # 执行记忆：在 findings_text 前插入完整推理链摘要（可审计轨迹）
    memory_ctx = format_execution_trace(execution_trace) or _format_execution_trace(execution_trace)
    findings_text = _format_findings(findings)
    if memory_ctx:
        findings_text = memory_ctx + "\n\n" + findings_text
    # DAG 完成态摘要
    dag = state.get("task_dag") or []
    if dag:
        done_n = sum(1 for t in dag if t.get("status") == "done")
        findings_text = f"[DAG] {done_n}/{len(dag)} tasks done\n\n" + findings_text
    sources_str = json.dumps(visited_urls[:15], ensure_ascii=False)

    if fast_path:
        logger.info(f"[finalize] fast_path (elapsed={elapsed:.0f}s)")
        prompt = _FINALIZE_FAST_PROMPT.format(
            question=query,
            findings_text=findings_text[:4000],
            preliminary=preliminary[:1000],
        )
        llm = get_flash_llm(max_tokens=1024)
    else:
        logger.info(f"[finalize] full_path (elapsed={elapsed:.0f}s)")
        prompt = _FINALIZE_PROMPT.format(
            question=query,
            findings_text=findings_text[:6000],
            supplementary=supplementary[:2000],
            sources=sources_str,
        )
        llm = get_llm(temperature=0, max_tokens=4096)

    answer_draft = ""
    reasoning_text = ""
    try:
        resp = llm.invoke(prompt)
        raw = resp.content.strip()
        logger.info(f"[finalize] raw_len={len(raw)}")
        reasoning_text = raw
        try:
            obj = _safe_json_obj(raw)
            answer_draft = str(obj.get("final_answer", "")).strip()
            confidence = float(obj.get("confidence", 0))
            reasoning_text = str(obj.get("reasoning", raw))
            logger.info(f"[finalize] answer_draft={answer_draft!r} confidence={confidence:.2f}")
        except Exception as e:
            logger.warning(f"[finalize] JSON parse failed: {e}, falling back to text")
            m = re.search(r'"final_answer"\s*:\s*"([^"]*)"', raw)
            if m:
                answer_draft = m.group(1).strip()
            m2 = re.search(r'FINAL ANSWER:\s*(.+)', raw)
            if m2 and not answer_draft:
                answer_draft = m2.group(1).strip()
    except Exception as e:
        # LLM 超时或内容审查：直接从 findings 中取置信度最高的候选
        logger.warning(f"[finalize] LLM failed ({e}), using best-finding fallback")
        best = max(findings, key=lambda f: f.get("confidence", 0) if isinstance(f, dict) else 0, default=None)
        if best and isinstance(best, dict) and best.get("candidates"):
            answer_draft = best["candidates"][0]
            reasoning_text = f"Fallback: best finding conf={best.get('confidence',0):.2f}"
        logger.info(f"[finalize] fallback answer_draft={answer_draft!r}")

    cites = build_citations(findings, answer_draft)
    return {
        "final_summary": reasoning_text,
        "final_answer": answer_draft,   # 预填，CoVe / answer_extraction 再处理
        "citations": cites,
    }


# ─── Answer Post-processing ──────────────────────────────────────────

def _postprocess_answer(answer: str, query: str) -> str:
    """
    天池评测归一化对齐。规则：转小写/去首尾空格由评测方做，我们处理：
      1. 数值题 → 纯整数（去单位后缀、去小数点）
      2. 有序数词 "第5名" → "5"
      3. 多实体：逗号/分号后补空格（英文语法要求）
    """
    if not answer:
        return answer

    q_lower = query.lower()

    # ── 1. 有序数词："第5名/位/届/次" → "5" ──
    ordinal_m = re.match(r'^第\s*(\d+)\s*[名位届次轮场]', answer.strip())
    if ordinal_m:
        return ordinal_m.group(1)

    # ── 2. 数值后处理 ──
    # 检测题目是否明确要求数字
    explicit_numeric = bool(re.search(
        r'只回答数字|直接回答数字|answer with arabic numeral|答.*数字',
        q_lower
    ))
    # 答案本身是"数字 + 不带量级的中文后缀"，如 "2008年" "5次" "3届"
    # 注意：万/亿/百/千不在此列（会改变数量级，不能简单截断）
    num_suffix_m = re.fullmatch(
        r'\s*(-?\d[\d,]*(?:\.\d+)?)\s*[年月日届次名个位轮场]\s*', answer
    )
    # 答案本身是纯浮点数，如 "3.0" "2008.0"
    looks_float = re.fullmatch(r'\s*-?\d+\.\d+\s*', answer)

    if explicit_numeric or num_suffix_m or looks_float:
        m = re.search(r'-?\d[\d,]*(?:\.\d+)?', answer)
        if m:
            try:
                answer = str(int(float(m.group(0).replace(',', ''))))
            except Exception:
                pass
        return answer

    # ── 3. 多实体：逗号/分号后补空格 ──
    # 仅当逗号后不是数字时处理，避免 "1,234" → "1, 234"
    answer = re.sub(r',(?!\s)(?!\d)', ', ', answer)
    answer = re.sub(r';(?!\s)', '; ', answer)

    return answer.strip()


# ─── Answer Type Detection ──────────────────────────────────────────

_ANSWER_TYPE_PROMPT = """\
判断以下问题期望的答案类型，只输出一个词。

类型定义：
- number: 纯数字答案（价格、距离、年份、数量、排名等）
- date: 具体日期（如 2024-01-15、August 5, 2025）
- name: 人名、地名、机构名、作品名等专有名词
- string: 其他文本答案

问题: {question}

只输出一个词 (number/date/name/string):"""

_TYPE_SPECIFIC_RULES = {
    "number": """
ANSWER TYPE: NUMBER — apply these rules:
- Output digits ONLY, no units, no currency symbols, no commas
- $100 → 100; 70% → 70; 4.0 L → 4; 1,234 m → 1234
- If question says "thousand hours", output 13 not 13000
- "第5名" → 5; "2008年" → 2008
- If fractional with .0, drop decimal: 3.0 → 3
- Follow precision in question (round as instructed)""",
    "date": """
ANSWER TYPE: DATE — apply these rules:
- Follow the exact date format requested in the question
- If no format specified, use the most natural format for the question language
- Chinese: 2024年1月15日 or 2024-01-15
- English: January 15, 2024 or 2024-01-15
- Output date only, no extra words""",
    "name": """
ANSWER TYPE: NAME — apply these rules:
- Use the commonly known form, not overly formal or technical names
- People: first + last name only (no titles, no middle name unless asked)
- Countries: common name (China, not People's Republic of China; Brunei, not Brunei Darussalam)
- Keep names in their ORIGINAL language unless question explicitly asks for translation
- No articles (the, a) unless part of an official name""",
    "string": """
ANSWER TYPE: STRING — apply these rules:
- Keep the answer as SHORT as possible
- No articles/abbreviations unless explicitly in the expected answer
- No ending punctuation (no period, no exclamation mark)
- If multiple items, separate with commas: "item1, item2, item3"
- Use simplest commonly accepted term""",
}


def _detect_answer_type(query: str) -> str:
    """用 Flash LLM 判定答案类型: number/date/name/string"""
    try:
        resp = get_flash_llm(temperature=0, max_tokens=10).invoke(
            _ANSWER_TYPE_PROMPT.format(question=query)
        )
        t = resp.content.strip().lower()
        for valid in ("number", "date", "name", "string"):
            if valid in t:
                return valid
    except Exception as e:
        logger.warning(f"[detect_answer_type] failed: {e}")
    return "string"


# ─── Node: Answer Extraction ─────────────────────────────────────────

def answer_extraction(state: ResearchState) -> dict:
    query = state["query"]
    summary = state.get("final_summary", "")
    draft = state.get("final_answer", "")   # finalize 已预提取

    # 答案类型检测（~1次 flash 调用）
    answer_type = _detect_answer_type(query)
    type_rules = _TYPE_SPECIFIC_RULES.get(answer_type, _TYPE_SPECIFIC_RULES["string"])
    logger.info(f"[answer_extraction] detected type={answer_type}")

    system_prompt = f"""You extract the PRECISE final answer from research findings.

PROCESS (follow strictly):
STEP 1: Read the research reasoning below. Based ONLY on the evidence, independently determine what the answer should be. Do NOT look at the draft answer yet.
STEP 2: Now compare your independently derived answer with this draft: {draft}
  - If they AGREE → use it.
  - If they DIFFER → pick the one with stronger evidence support from the reasoning.
STEP 3: Format the chosen answer according to the GENERAL and TYPE-SPECIFIC rules below.

GENERAL RULES:
1. Output ONLY the answer. No explanations, no "The answer is", no "答案是".
2. Language: Chinese question → Chinese answer; English → English.
   UNLESS the question explicitly requests otherwise.
3. Follow format requirements EXACTLY if stated in the question:
   - "形如：Alibaba Group Limited" → English company full name
   - "Answer with Arabic numerals" / "只回答数字" → digits only
   - "形如：张三和李四" → two names joined with 和
   - "Answer with first name and last name only" → "First Last"
   - "请用中文全称回答" → full official Chinese name
   - "不要加任何标点符号" → zero punctuation
   - "请用原名回答" / "Please answer with english name" → original language name
   - "用拉丁学名" → Latin binomial name
{type_rules}

Question: {query}"""

    user_msg = f"""Research reasoning:
{summary[:3000]}

Follow the 3-step process: derive independently → compare with draft → format. Output ONLY the final answer."""

    try:
        llm = get_flash_llm(temperature=0, max_tokens=256)
        resp = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
        answer = resp.content.strip()
        answer = re.sub(r'^(The answer is|答案是|FINAL ANSWER:|Final Answer:)\s*',
                        '', answer, flags=re.IGNORECASE).strip()
        answer = answer.strip('"\'。.，,')
    except Exception as e:
        # LLM 超时或内容审查：直接用 finalize 输出的 draft 答案
        logger.warning(f"[answer_extraction] LLM failed ({e}), using draft fallback")
        answer = draft.strip('"\'。.，,') if draft else ""

    # 归一化后处理：数值整数化、多实体格式
    answer = _postprocess_answer(answer, query)

    # 可审计轨迹持久化（供 memory 端点 / 复用）
    try:
        save_session(
            state.get("session_id") or "default",
            question=query,
            answer=answer,
            execution_trace=state.get("execution_trace") or [],
            findings=state.get("findings") or [],
            citations=state.get("citations") or [],
            task_dag=state.get("task_dag") or [],
            user_id=state.get("user_id") or "default",
        )
    except Exception as e:
        logger.warning(f"[answer_extraction] save_session failed: {e}")

    logger.info(f"[answer_extraction] -> {answer!r}")
    return {"final_answer": answer}
