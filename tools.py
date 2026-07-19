# -*- coding: utf-8 -*-
"""
搜索工具层：Exa API 封装 + 结构化信息提取 + 查询优化。
所有外部 I/O 都在这里，节点代码不直接调用 Exa。
"""
import re
import json
import logging
import socket
from typing import Optional

import requests
from exa_py import Exa
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

from config import (
    EXA_API_KEY, SERPER_API_KEY, JINA_API_KEY, get_flash_llm,
    TRIANGULATION_AGREE_BOOST, MAX_PARALLEL_TOOLS,
    MIN_CREDIBILITY_ACCEPT, SKIP_SECOND_SOURCE_CONF,
    EXPAND_BELOW_CONF, VERIFY_BELOW_CONF,
)

EXA_TIMEOUT_SECS = 30     # Exa API 单次调用超时（秒）
SERPER_TIMEOUT_SECS = 15  # Serper API 单次调用超时（秒）
JINA_TIMEOUT_SECS = 20    # Jina Reader 单次调用超时（秒）
WAYBACK_TIMEOUT_SECS = 15 # Wayback Machine 查询超时（秒）
COMPRESS_MAX_CHARS = 2000 # 文档压缩：输入最大字符数

logger = logging.getLogger("research_agent")


# ─── Document Compression ─────────────────────────────────────────
# 移植自 Tianchi_DeepSearch_Agent/deepresearch/tools/compress.py
# 核心改进：用 Flash LLM 从页面中提取与子任务相关的有用信息，
# 替代直接截断前 N 字符，显著提升信噪比。

_COMPRESS_PROMPT = """\
你是信息提取专家。请根据问题与当前搜索焦点，从以下文档中提取所有相关有用信息。

原始问题：{question}
当前搜索焦点：{search_focus}

文档内容：
{doc_content}

规则：
1) 只提取与搜索焦点直接相关的事实、数据、名称、日期等关键信息
2) 保留原文措辞，不要改写或推测
3) 如果文档与搜索焦点完全无关，只输出"无关"
4) 用简洁的要点列表输出，每条一行"""


def _compress_doc(text: str, question: str, search_focus: str,
                  max_input_chars: int = COMPRESS_MAX_CHARS) -> str:
    """
    用 Flash LLM 压缩单个文档，提取与搜索焦点相关的信息。
    失败时退回 _extract_query_passages 关键词匹配。
    """
    if not text or not text.strip():
        return ""

    doc_content = text[:max_input_chars] if len(text) > max_input_chars else text

    try:
        resp = get_flash_llm(temperature=0, max_tokens=512).invoke(
            _COMPRESS_PROMPT.format(
                question=question,
                search_focus=search_focus,
                doc_content=doc_content,
            )
        )
        result = resp.content.strip()
        if not result or result == "无关":
            return ""
        return result
    except Exception as e:
        logger.warning(f"[compress_doc] failed: {e}, falling back to keyword extraction")
        return _extract_query_passages(text, query=search_focus)


# ─── Query-aware Passage Extraction ─────────────────────────────────
# 移植自 Tianchi_DeepSearch_Agent/deepresearch/tools/fetch_tool.py
# 核心改进：不再取页面前 N 字符，而是找与查询最相关的段落

def _clean_text(text: str) -> str:
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """将文本按滑动窗口切块。"""
    if not text:
        return []
    chunk_size = max(200, chunk_size)
    overlap = max(0, min(overlap, chunk_size // 2))
    chunks: list[str] = []
    step = chunk_size - overlap
    for i in range(0, len(text), step):
        piece = text[i: i + chunk_size]
        if piece:
            chunks.append(piece)
        if i + chunk_size >= len(text):
            break
    return chunks


def _keyword_score(query: str, passage: str) -> int:
    """轻量关键词命中打分，无额外依赖。支持中英文分词。"""
    if not query or not passage:
        return 0
    terms = [t.strip().lower()
             for t in re.split(r"[\s,;，。！？:：()\[\]{}\"'`]+", query)
             if t.strip()]
    if not terms:
        return 0
    p = passage.lower()
    return sum(1 for t in terms if len(t) >= 2 and t in p)


def _extract_query_passages(
    text: str,
    query: Optional[str],
    top_k: int = 3,
    chunk_size: int = 800,
    overlap: int = 120,
) -> str:
    """
    按 query 从全文中抽取最相关的段落，替代直接截取前 N 字符。
    无 query 时退回原文截断。
    """
    if not query or not query.strip() or not text:
        return text[:2500]

    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return text[:2500]

    scored = [(_keyword_score(query, c), idx, c) for idx, c in enumerate(chunks)]
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    picked = [c for s, _, c in scored[:max(1, top_k)] if s > 0]

    if not picked:
        # 关键词全未命中（如问题语言与页面语言不同），退回原文前段
        return _clean_text(text[:2500])

    return _clean_text("\n\n".join(picked))

# ─── Exa Client ─────────────────────────────────────────────────────
exa_client = Exa(api_key=EXA_API_KEY) if EXA_API_KEY else None


def _run_with_timeout(fn, timeout: float = EXA_TIMEOUT_SECS):
    """线程级超时，避免改动进程级 socket.setdefaulttimeout（并行不安全）。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            logger.warning(f"call timed out after {timeout}s")
            return None
        except Exception as e:
            logger.warning(f"call error: {e}")
            return None


def _safe_exa_search(query: str, num_results=5, search_type="auto") -> list[dict]:
    """Returns list of {title, url}."""
    if not exa_client:
        return []

    def _call():
        r = exa_client.search(query, num_results=num_results, type=search_type)
        return [{"title": x.title, "url": x.url} for x in r.results]

    try:
        out = _run_with_timeout(_call, EXA_TIMEOUT_SECS)
        return out if isinstance(out, list) else []
    except Exception as e:
        logger.warning(f"exa_search error: {e}")
        return []


def _safe_exa_contents(urls: list[str]) -> list[dict]:
    """Returns list of {url, text}."""
    if not exa_client or not urls:
        return []

    def _call():
        r = exa_client.get_contents(urls)
        return [{"url": x.url, "text": (x.text or "")[:3000]} for x in r.results]

    try:
        out = _run_with_timeout(_call, EXA_TIMEOUT_SECS)
        return out if isinstance(out, list) else []
    except Exception as e:
        logger.warning(f"exa_contents error: {e}")
        return []


# ─── Serper (Google Search) ─────────────────────────────────────────

def _safe_serper_search(
    query: str, num: int = 5, gl: str = "us", hl: str = "en", tbs: str = None,
) -> dict:
    """
    Google 搜索（Serper API），返回完整结构。
    包含 knowledgeGraph / answerBox / organic（带 snippet）/ peopleAlsoAsk。
    无 API Key 或请求失败时返回空 dict。
    """
    if not SERPER_API_KEY:
        return {}
    try:
        payload = {"q": query, "gl": gl, "hl": hl, "num": num}
        if tbs:
            payload["tbs"] = tbs
        resp = requests.post(
            "https://google.serper.dev/search",
            json=payload,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            timeout=SERPER_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"serper_search error: {e}")
        return {}


def _format_serper_context(data: dict, max_organic: int = 3) -> str:
    """
    将 Serper 返回的完整 JSON 格式化为可拼入 context 的文本。
    优先输出 KnowledgeGraph / AnswerBox，其次 organic snippet。
    """
    if not data:
        return ""
    parts = []

    kg = data.get("knowledgeGraph", {})
    if kg:
        kg_text = f"[Google Knowledge Graph] {kg.get('title', '')}"
        if kg.get("type"):
            kg_text += f" ({kg['type']})"
        if kg.get("description"):
            kg_text += f"\n  {kg['description']}"
        attrs = kg.get("attributes", {})
        if attrs:
            kg_text += "\n  " + "; ".join(f"{k}: {v}" for k, v in list(attrs.items())[:8])
        parts.append(kg_text)

    ab = data.get("answerBox", {})
    if ab:
        answer = ab.get("answer") or ab.get("snippet", "")
        if answer:
            parts.append(f"[Google Answer Box] {answer}")

    for r in data.get("organic", [])[:max_organic]:
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        if snippet:
            parts.append(f"[{title}]({link})\n  {snippet}")

    return "\n\n".join(parts)


# ─── 百度百科查询（通过 Serper + Jina）───────────────────────────────

def _safe_baike_summary(entity: str, max_chars: int = 2000) -> str:
    """
    百度百科实体摘要查询。
    通过 Serper 搜索 site:baike.baidu.com 获取 URL，再用 Jina 抓取正文。
    适用于中文内容查询，国内可直连。
    """
    try:
        query = f"{entity} site:baike.baidu.com"
        serper_data = _safe_serper_search(query, num=3, gl="cn", hl="zh")
        if not serper_data:
            return ""

        # 从 organic 结果中取第一条百度百科链接
        url = ""
        for r in serper_data.get("organic", [])[:3]:
            link = r.get("link", "")
            if "baike.baidu.com" in link:
                url = link
                break

        if not url:
            return ""

        # 用 Jina 抓取百度百科页面正文
        text = _safe_jina_scrape(url, max_chars=max_chars)
        if text:
            return text.strip()

        # Jina 失败时退回 Serper snippet
        for r in serper_data.get("organic", [])[:3]:
            if r.get("link", "") == url:
                return (r.get("snippet", "")).strip()
        return ""
    except Exception as e:
        logger.warning(f"baike_summary error: {e}")
        return ""


# ─── Wikipedia 查询（通过 Serper + Jina 间接访问）─────────────────────

def _safe_wiki_summary(entity: str, sentences: int = 5, lang: str = "en") -> str:
    """
    Wikipedia 实体摘要查询。
    通过 Serper 搜索 site:wikipedia.org 获取 URL，再用 Jina 抓取正文。
    无需直连 Wikipedia，适用于国内网络环境。
    """
    try:
        wiki_domain = "zh.wikipedia.org" if lang == "zh" else "en.wikipedia.org"
        query = f"{entity} site:{wiki_domain}"
        serper_data = _safe_serper_search(query, num=1, gl="us", hl=lang)
        if not serper_data:
            return ""

        # 从 organic 结果中取第一条 Wikipedia 链接
        url = ""
        for r in serper_data.get("organic", [])[:3]:
            link = r.get("link", "")
            if "wikipedia.org" in link:
                url = link
                break

        if not url:
            # 没搜到 Wikipedia 页面，退回 snippet 作为摘要
            for r in serper_data.get("organic", [])[:1]:
                snippet = r.get("snippet", "")
                if snippet:
                    return snippet.strip()
            return ""

        # 用 Jina 抓取 Wikipedia 页面正文
        text = _safe_jina_scrape(url, max_chars=2000)
        if text:
            return text.strip()

        # Jina 失败时退回 Serper snippet
        for r in serper_data.get("organic", [])[:3]:
            if r.get("link", "") == url:
                return (r.get("snippet", "")).strip()
        return ""
    except Exception as e:
        logger.warning(f"wiki_summary error: {e}")
        return ""


# ─── Wayback Machine ──────────────────────────────────────────────

def _safe_wayback_search(url: str, year: int, month: int, day: int = 1) -> dict:
    """
    Wayback Machine 存档查询。返回 {archived_url, timestamp} 或空 dict。
    """
    try:
        ts = f"{year:04d}{month:02d}{day:02d}"
        resp = requests.get(
            "https://archive.org/wayback/available",
            params={"url": url, "timestamp": ts},
            timeout=WAYBACK_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        data = resp.json()
        closest = (data.get("archived_snapshots") or {}).get("closest", {})
        if closest and closest.get("available"):
            return {
                "archived_url": closest["url"],
                "timestamp": closest.get("timestamp", ""),
            }
    except Exception as e:
        logger.warning(f"wayback_search error: {e}")
    return {}


# ─── Jina Reader (网页抓取) ───────────────────────────────────────

def _safe_jina_scrape(url: str, max_chars: int = 5000) -> str:
    """
    用 Jina Reader 把网页转为干净 Markdown 文本。
    作为 Serper / Wayback 找到 URL 后的配套内容获取器。
    无 API Key 时也能用（Jina 免费层不需要 key，但有限流）。
    """
    try:
        headers = {"Accept": "text/markdown"}
        if JINA_API_KEY:
            headers["Authorization"] = f"Bearer {JINA_API_KEY}"
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers=headers,
            timeout=JINA_TIMEOUT_SECS,
        )
        if resp.status_code == 200:
            return _clean_text(resp.text[:max_chars])
    except Exception as e:
        logger.warning(f"jina_scrape error: {e}")
    return ""


# ─── JSON 解析工具 ───────────────────────────────────────────────────

def _safe_json_obj(text: str) -> dict:
    """从文本中安全解析第一个 JSON 对象，支持 markdown 代码块包裹。"""
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*\n?', '', t)
    t = re.sub(r'\n?```\s*$', '', t)
    t = t.strip()
    if t.startswith('{') and t.endswith('}'):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        raise ValueError("no JSON object found")
    return json.loads(m.group(0))


# ─── Structured Extraction ──────────────────────────────────────────

_EXTRACT_PROMPT = """\
Extract specific factual information from the web content below.
从以下网页内容中提取具体事实信息。

Original question: {original_question}
Current search focus: {search_focus}

Web content:
{content}

Output valid JSON only:
{{"candidates": ["best answer"], "confidence": 0.0, "evidence": ["key fact 1", "key fact 2"]}}

Rules:
- candidates: specific names / numbers / dates / places in their ORIGINAL language (Chinese stays Chinese, English stays English)
- confidence: 0.9=clearly found, 0.6=likely correct, 0.3=uncertain, 0.1=not found
- evidence: 2-4 direct quotes or key paraphrased facts from the content
- If nothing relevant: {{"candidates": [], "confidence": 0.1, "evidence": []}}
Output JSON only."""


_REFLECT_EXPAND_PROMPT = """\
You are a search query optimizer for multi-hop research.

Original question: {question}
Primary search query: {primary_query}
Confirmed entities from previous steps: {confirmed_entities}
Queries already tried (do NOT repeat): {tried_queries}

Generate {n} alternative search queries for the SAME information need as the primary query.
Requirements:
- Different keywords, phrasings, or perspectives from the primary query
- Do NOT repeat any query from the "already tried" list
- Keep each query concise: 3-8 keywords
- Match the language of the primary query (Chinese query → Chinese variants)
- CRITICAL: If confirmed_entities is NOT empty, EVERY variant MUST contain at least one confirmed entity verbatim as an anchor keyword. Do NOT use vague references like "the winner" or "该获奖者" — use the actual confirmed name/entity.

Output JSON array only: ["query1", "query2"]"""


def _bounded_search_and_extract(search_focus: str, original_question: str) -> dict:
    """有界搜索+提取：最多2次Exa搜索 + 1次Flash LLM，绝对不会死循环。"""
    results = _safe_exa_search(search_focus, num_results=5, search_type="auto")
    if not results:
        results = _safe_exa_search(search_focus, num_results=5, search_type="neural")

    empty = {"sub_query": search_focus, "candidates": [], "confidence": 0.1,
             "evidence": [], "sources": []}
    if not results:
        return empty

    urls = [r["url"] for r in results[:2]]
    pages = _safe_exa_contents(urls)
    if not pages:
        return {**empty, "sources": urls}

    # 文档压缩：长文档用 Flash LLM 提取，短文档直接用关键词匹配（省 LLM 调用）
    content_parts = []
    for p in pages:
        text = p['text']
        if len(text) > 1500:
            compressed = _compress_doc(text, original_question, search_focus)
            if compressed:
                content_parts.append(f"[{p['url']}]\n{compressed}")
                continue
        # 短文档或压缩返回空 → 关键词匹配
        fallback = _extract_query_passages(text, query=search_focus)
        if fallback:
            content_parts.append(f"[{p['url']}]\n{fallback}")
    content = "\n\n".join(content_parts)
    prompt = _EXTRACT_PROMPT.format(
        original_question=original_question,
        search_focus=search_focus,
        content=content,
    )
    raw = ""
    try:
        resp = get_flash_llm(max_tokens=512).invoke(prompt)
        raw = resp.content.strip()
        obj = _safe_json_obj(raw)
        return {
            "sub_query": search_focus,
            "candidates": [str(c).strip() for c in obj.get("candidates", []) if str(c).strip()][:3],
            "confidence": float(obj.get("confidence", 0.1)),
            "evidence": [str(e).strip() for e in obj.get("evidence", []) if str(e).strip()][:4],
            "sources": urls,
        }
    except Exception as e:
        logger.warning(f"[extract] parse error: {e}, raw={raw[:100]}")
        fallback_text = pages[0]["text"][:400] if pages else ""
        return {
            "sub_query": search_focus,
            "candidates": [],
            "confidence": 0.2,
            "evidence": [fallback_text] if fallback_text else [],
            "sources": urls,
        }


_VERIFY_CANDIDATE_PROMPT = """\
You are verifying whether a candidate answer is correct for a specific sub-question.

Original question: {original_question}
Sub-question being answered: {sub_query}
Candidate answer: {candidate}
Evidence provided: {evidence}

Evaluate critically:
1. Does the evidence DIRECTLY support this candidate as the answer to the sub-question?
2. Could the evidence be referring to something else (e.g. a related but different entity)?
3. Is there any mismatch between what was asked and what was found?

Output valid JSON only:
{{"verdict": "accept" or "reject", "reason": "brief explanation", "refined_query": "better search query if rejected, empty string if accepted"}}"""


def _verify_candidate(candidate: str, sub_query: str, original_question: str,
                      evidence: list) -> dict:
    """
    对候选答案做一次 self-check。
    返回 {"rejected": bool, "reason": str, "refined_query": str}
    """
    prompt = _VERIFY_CANDIDATE_PROMPT.format(
        original_question=original_question,
        sub_query=sub_query,
        candidate=candidate,
        evidence="; ".join(evidence[:3]) if evidence else "(no evidence)",
    )
    try:
        resp = get_flash_llm(temperature=0, max_tokens=256).invoke(prompt)
        raw = resp.content.strip()
        obj = _safe_json_obj(raw)
        verdict = str(obj.get("verdict", "accept")).lower().strip()
        return {
            "rejected": verdict == "reject",
            "reason": str(obj.get("reason", "")),
            "refined_query": str(obj.get("refined_query", "")),
        }
    except Exception as e:
        logger.warning(f"[verify_candidate] failed: {e}")
        return {"rejected": False, "reason": "verification failed", "refined_query": ""}


def _serper_search_and_extract(search_focus: str, original_question: str) -> dict:
    """
    Serper (Google) 搜索 + 有条件 Jina 抓取 + Flash LLM 提取。
    作为 _bounded_search_and_extract (Exa) 的兜底方案。
    返回格式与 _bounded_search_and_extract 一致。
    """
    empty = {"sub_query": search_focus, "candidates": [], "confidence": 0.1,
             "evidence": [], "sources": []}

    data = _safe_serper_search(search_focus, num=5)
    if not data:
        return empty

    content_parts = []
    sources = []

    # 优先用 KnowledgeGraph / AnswerBox（已结构化，不需要 Jina）
    kg = data.get("knowledgeGraph", {})
    if kg:
        kg_text = kg.get("title", "")
        if kg.get("description"):
            kg_text += f": {kg['description']}"
        attrs = kg.get("attributes", {})
        if attrs:
            kg_text += "\n" + "; ".join(f"{k}: {v}" for k, v in list(attrs.items())[:8])
        content_parts.append(f"[Knowledge Graph]\n{kg_text}")

    ab = data.get("answerBox", {})
    if ab:
        answer = ab.get("answer") or ab.get("snippet", "")
        if answer:
            content_parts.append(f"[Answer Box] {answer}")

    # Organic 结果：有 KG 时只用 snippet；无 KG 时用 Jina 抓全文
    has_direct = bool(kg or ab)
    for r in data.get("organic", [])[:2]:
        url = r.get("link", "")
        if not url:
            continue
        sources.append(url)
        if has_direct:
            snippet = r.get("snippet", "")
            if snippet:
                content_parts.append(f"[{r.get('title', '')}] {snippet}")
        else:
            full_text = _safe_jina_scrape(url, max_chars=3000)
            if full_text:
                if len(full_text) > 1500:
                    compressed = _compress_doc(full_text, original_question, search_focus)
                else:
                    compressed = ""
                passage = compressed or _extract_query_passages(full_text, query=search_focus)
                content_parts.append(f"[{r.get('title', '')}]\n{passage}")
            else:
                snippet = r.get("snippet", "")
                if snippet:
                    content_parts.append(f"[{r.get('title', '')}] {snippet}")

    if not content_parts:
        return {**empty, "sources": sources}

    content = "\n\n".join(content_parts)
    prompt = _EXTRACT_PROMPT.format(
        original_question=original_question,
        search_focus=search_focus,
        content=content,
    )
    raw = ""
    try:
        resp = get_flash_llm(max_tokens=512).invoke(prompt)
        raw = resp.content.strip()
        obj = _safe_json_obj(raw)
        return {
            "sub_query": search_focus,
            "candidates": [str(c).strip() for c in obj.get("candidates", []) if str(c).strip()][:3],
            "confidence": float(obj.get("confidence", 0.1)),
            "evidence": [str(e).strip() for e in obj.get("evidence", []) if str(e).strip()][:4],
            "sources": sources,
        }
    except Exception as e:
        logger.warning(f"[serper_extract] parse error: {e}, raw={raw[:100]}")
        return {**empty, "sources": sources}


def _reflect_and_expand_queries(
    primary_query: str,
    question: str,
    confirmed_entities: list,
    executed_queries: list,
    n: int = 2,
) -> list[str]:
    """
    一次 flash LLM 调用，将 primary_query 扩展为 N 个不重复的变体查询。
    用于 act_search 提高单轮搜索的覆盖面。
    """
    tried = {q.lower().strip() for q in executed_queries}
    prompt = _REFLECT_EXPAND_PROMPT.format(
        question=question,
        primary_query=primary_query,
        confirmed_entities=confirmed_entities if confirmed_entities else "(none)",
        tried_queries=executed_queries[-5:] if executed_queries else "(none)",
        n=n,
    )
    try:
        resp = get_flash_llm(max_tokens=200).invoke(prompt)
        raw = resp.content.strip()
        arr_match = re.search(r'\[[\s\S]*?\]', raw)
        if arr_match:
            variants = json.loads(arr_match.group(0))
            result = []
            for v in variants:
                v = str(v).strip()
                if v and v.lower() not in tried and v.lower() != primary_query.lower():
                    result.append(v)
            return result[:n]
    except Exception as e:
        logger.warning(f"[reflect_expand] failed: {e}")
    return []


def _format_findings(findings: list) -> str:
    """
    将结构化 findings 格式化为 LLM 可读的文本。
    分权重输出：最近 2 个 finding 详细展示，更早的只保留一行摘要。
    防止推理链断裂——早期高置信结论始终可见，不会被长 evidence 挤出截断窗口。
    """
    if not findings:
        return "(no findings yet)"
    parts = []
    n = len(findings)
    for i, f in enumerate(findings, 1):
        if not isinstance(f, dict):
            parts.append(f"[Step {i}] {str(f)}")
            continue

        cands = f.get("candidates", [])
        conf = f.get("confidence", 0)

        if i > n - 2:
            # 最近 2 个：详细输出（query + candidates + evidence + sources）
            sq = f.get("sub_query", "")
            evid = f.get("evidence", [])
            srcs = f.get("sources", [])
            line = f"[Step {i}] Query: {sq}"
            if cands:
                line += f"\n  → Candidates: {', '.join(cands[:3])} (confidence: {conf:.1f})"
            else:
                line += f"\n  → No candidates found (confidence: {conf:.1f})"
            if evid:
                line += f"\n  Evidence: {'; '.join(evid[:3])}"
            if srcs:
                line += f"\n  Sources: {', '.join(srcs[:2])}"
            if f.get("triangulated"):
                line += "\n  [triangulated ✓]"
            if f.get("credibility"):
                line += f"\n  credibility={float(f.get('credibility', 0)):.2f}"
        else:
            # 更早的：一行摘要（仅 candidates + confidence）
            ans = cands[0] if cands else "(empty)"
            tri = " △" if f.get("triangulated") else ""
            line = f"[Step {i}]{tri} → {ans} (conf: {conf:.1f})"

        parts.append(line)
    return "\n\n".join(parts)


# ─── 来源可信度加权 + 多源三角验证 + 并行工具调用 ─────────────────

_DOMAIN_CREDIBILITY = {
    "wikipedia.org": 0.92,
    "baike.baidu.com": 0.85,
    "britannica.com": 0.90,
    "gov": 0.93,
    "edu": 0.88,
    "ac.uk": 0.88,
    "nih.gov": 0.94,
    "nature.com": 0.93,
    "science.org": 0.93,
    "arxiv.org": 0.80,
    "jstor.org": 0.88,
    "reuters.com": 0.86,
    "bbc.": 0.85,
    "nytimes.com": 0.84,
    "theguardian.com": 0.83,
    "zhihu.com": 0.55,
    "reddit.com": 0.45,
    "medium.com": 0.50,
    "blogspot.": 0.40,
    "wordpress.": 0.42,
}


def _source_credibility(url: str) -> float:
    """按域名启发式打可信度分 [0.3, 0.95]。"""
    if not url:
        return 0.4
    u = url.lower()
    for key, score in _DOMAIN_CREDIBILITY.items():
        if key in u:
            return score
    if u.endswith(".gov") or ".gov/" in u:
        return 0.93
    if u.endswith(".edu") or ".edu/" in u:
        return 0.88
    return 0.55


def _normalize_answer(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\"'“”‘’\.。,，、:：;；!！?？]", "", s)
    return s


def _answers_agree(a: str, b: str) -> bool:
    """收紧一致判定，避免 '12'⊆'1200'、过短子串误三角。"""
    na, nb = _normalize_answer(a), _normalize_answer(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # 纯数字：必须完全一致
    if na.isdigit() and nb.isdigit():
        return na == nb
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    # 过短子串不认（至少 4 字符，或中文 2 字以上且占比够高）
    min_len = 2 if re.search(r"[\u4e00-\u9fff]", shorter) else 4
    if len(shorter) < min_len:
        return False
    if shorter in longer:
        return len(shorter) / max(len(longer), 1) >= 0.55
    return False


def _mean_credibility(sources: list) -> float:
    if not sources:
        return 0.4
    scores = [_source_credibility(u) for u in sources if u]
    return sum(scores) / len(scores) if scores else 0.4


def _triangulate_findings(results: list[dict]) -> dict:
    """
    多源三角验证：独立工具结果交叉比对。
    - 两路候选归一化一致 → triangulated=True，confidence 加权 boost
    - 冲突 → 取 credibility 更高且 conf 更高者，标记未三角一致
    """
    nonempty = [r for r in results if r and (r.get("candidates") or r.get("evidence"))]
    if not nonempty:
        return {
            "sub_query": "",
            "candidates": [],
            "confidence": 0.1,
            "evidence": [],
            "sources": [],
            "triangulated": False,
            "credibility": 0.3,
            "tool_votes": [],
        }

    # 丰富每路 meta
    enriched = []
    for r in nonempty:
        rr = dict(r)
        cred = _mean_credibility(rr.get("sources") or [])
        rr["credibility"] = cred
        # 综合分：conf * 0.6 + cred * 0.4
        rr["_score"] = float(rr.get("confidence", 0.1)) * 0.6 + cred * 0.4
        enriched.append(rr)

    enriched.sort(key=lambda x: x["_score"], reverse=True)
    best = enriched[0]
    tool_votes = []
    for r in enriched:
        tool_votes.append({
            "candidates": (r.get("candidates") or [])[:2],
            "confidence": r.get("confidence", 0),
            "credibility": r.get("credibility", 0),
            "sources": (r.get("sources") or [])[:2],
        })

    triangulated = False
    conf = float(best.get("confidence", 0.1))
    if len(enriched) >= 2:
        a = (enriched[0].get("candidates") or [""])[0]
        b = (enriched[1].get("candidates") or [""])[0]
        if a and b and _answers_agree(a, b):
            triangulated = True
            conf = min(0.98, conf + TRIANGULATION_AGREE_BOOST)
            # 合并证据与来源
            ev, src = [], []
            for r in enriched[:3]:
                for e in r.get("evidence") or []:
                    if e not in ev:
                        ev.append(e)
                for s in r.get("sources") or []:
                    if s not in src:
                        src.append(s)
            best = dict(best)
            best["evidence"] = ev[:6]
            best["sources"] = src[:6]

    out = {
        "sub_query": best.get("sub_query", ""),
        "candidates": best.get("candidates") or [],
        "confidence": conf,
        "evidence": best.get("evidence") or [],
        "sources": best.get("sources") or [],
        "triangulated": triangulated,
        "credibility": best.get("credibility", 0.4),
        "tool_votes": tool_votes,
    }
    return out


def parallel_multi_source_search(search_focus: str, original_question: str) -> dict:
    """
    多源检索：
    1) Serper 高 conf → 快路径（跳过 Exa）
    2) Serper 有结果但不稳 → 补 Exa 三角
    3) 否则 Exa 单源（或与 Serper 并行兜底）
    单源失效不断链。
    """
    def _run_exa():
        try:
            return _bounded_search_and_extract(search_focus, original_question)
        except Exception as e:
            logger.warning(f"[parallel] exa failed: {e}")
            return None

    def _run_serper():
        if not SERPER_API_KEY:
            return None
        try:
            return _serper_search_and_extract(search_focus, original_question)
        except Exception as e:
            logger.warning(f"[parallel] serper failed: {e}")
            return None

    def _finalize_single(r: dict, tool: str) -> dict:
        r = dict(r)
        r["tool"] = tool
        r["sub_query"] = search_focus
        r["credibility"] = _mean_credibility(r.get("sources") or [])
        r.setdefault("triangulated", False)
        return r

    def _merge(results: list) -> dict:
        merged = _triangulate_findings(results)
        merged["sub_query"] = search_focus
        return merged

    empty = {
        "sub_query": search_focus,
        "candidates": [],
        "confidence": 0.1,
        "evidence": [],
        "sources": [],
        "triangulated": False,
        "credibility": 0.3,
    }

    serper_r = _run_serper() if SERPER_API_KEY else None
    if serper_r and serper_r.get("candidates"):
        conf = float(serper_r.get("confidence", 0))
        cred = _mean_credibility(serper_r.get("sources") or [])
        if conf >= SKIP_SECOND_SOURCE_CONF and cred >= MIN_CREDIBILITY_ACCEPT:
            logger.info(f"[parallel] serper fast-path conf={conf:.2f}")
            return _finalize_single(serper_r, "serper")
        exa_r = _run_exa()
        results = [dict(serper_r, tool="serper")]
        if exa_r:
            results.append(dict(exa_r, tool="exa"))
        return _merge(results)

    # Serper 无结果：Exa 主路径
    exa_r = _run_exa()
    if exa_r:
        conf = float(exa_r.get("confidence", 0))
        if conf >= SKIP_SECOND_SOURCE_CONF or not SERPER_API_KEY:
            return _finalize_single(exa_r, "exa")
        # Exa 不稳且 Serper 刚才为空：直接返回 Exa（避免再打一次 Serper）
        return _finalize_single(exa_r, "exa")

    return empty


def worker_execute_subtask(
    search_focus: str,
    original_question: str,
    confirmed_entities: list,
    executed_queries: list,
    expand: bool = True,
) -> dict:
    """
    Worker 闭环：主查询多源 → 低 conf 才 expand → 条件 self-check。
    """
    tried = []
    # 1) 主 query
    tried.append(search_focus)
    best = parallel_multi_source_search(search_focus, original_question)

    # 2) 仅当 conf 不足时才 LLM expand + 再搜
    conf0 = float(best.get("confidence", 0)) if best else 0
    if expand and conf0 < EXPAND_BELOW_CONF:
        variants = _reflect_and_expand_queries(
            primary_query=search_focus,
            question=original_question,
            confirmed_entities=confirmed_entities,
            executed_queries=executed_queries + tried,
            n=1,
        )
        for v in variants:
            if not v or v in tried or v in executed_queries:
                continue
            tried.append(v)
            r = parallel_multi_source_search(v, original_question)
            if float(r.get("confidence", 0)) > float(best.get("confidence", 0)):
                best = r
            if r.get("triangulated") and float(r.get("confidence", 0)) >= 0.8:
                break

    if best is None:
        best = {
            "sub_query": search_focus,
            "candidates": [],
            "confidence": 0.1,
            "evidence": [],
            "sources": [],
            "triangulated": False,
            "credibility": 0.3,
        }

    conf = float(best.get("confidence", 0))
    need_verify = (
        best.get("candidates")
        and (
            conf < VERIFY_BELOW_CONF
            or not best.get("triangulated")
            or float(best.get("credibility") or 0) < MIN_CREDIBILITY_ACCEPT
        )
    )

    if need_verify:
        vr = _verify_candidate(
            candidate=best["candidates"][0],
            sub_query=search_focus,
            original_question=original_question,
            evidence=best.get("evidence", []),
        )
        if vr.get("rejected"):
            refined = vr.get("refined_query") or ""
            if refined and refined not in tried:
                tried.append(refined)
                refined_r = parallel_multi_source_search(refined, original_question)
                if float(refined_r.get("confidence", 0)) >= float(best.get("confidence", 0)):
                    best = refined_r
            best["verification"] = {"rejected": True, "reason": vr.get("reason", "")}
        else:
            best["verification"] = {"rejected": False, "reason": vr.get("reason", "")}

    best["tried_queries"] = tried
    return best


def build_citations(findings: list, answer: str = "") -> list[dict]:
    """从 findings 生成 claim↔source citation 列表。"""
    cites = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        cands = f.get("candidates") or []
        evid = f.get("evidence") or []
        srcs = f.get("sources") or []
        claim = cands[0] if cands else (evid[0] if evid else "")
        if not claim:
            continue
        url = srcs[0] if srcs else ""
        cites.append({
            "claim": str(claim)[:200],
            "evidence": (evid[0] if evid else "")[:300],
            "source": url,
            "credibility": float(f.get("credibility") or _mean_credibility(srcs)),
            "triangulated": bool(f.get("triangulated")),
            "supports_answer": bool(
                answer and cands and _answers_agree(str(cands[0]), answer)
            ),
        })
    return cites[:12]

