# -*- coding: utf-8 -*-
"""
配置与 LLM 工厂函数。
所有 API Key、Base URL、模型选择都在这里改。
"""
import os
from pathlib import Path

from langchain_openai import ChatOpenAI


def _load_env_file(path: str = "env.txt") -> None:
    """加载 env.txt / .env（不覆盖已有环境变量）。"""
    p = Path(path)
    if not p.is_file():
        p = Path(__file__).resolve().parent / path
    if not p.is_file():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_env_file("env.txt")
_load_env_file(".env")

# ─── Environment ────────────────────────────────────────────────────
os.environ.setdefault(
    "DASHSCOPE_API_KEY",
    os.environ.get("DASHSCOPE_API_KEY", ""),
)
os.environ["LANGSMITH_OTEL_ENABLED"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_OTEL_ONLY"] = "false"

# ─── Constants ──────────────────────────────────────────────────────
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# ─── 模型名称 ──────────────────────────────────────────────────────
# 主模型：分解 / finalize；Flash：抽取 / reflection / CoVe 等
MAIN_MODEL = os.environ.get("MAIN_MODEL", "qwen3-max-2026-01-23")
FLASH_MODEL = os.environ.get("FLASH_MODEL", "qwen3-max-2026-01-23")

MAIN_LLM_TIMEOUT = 120
FLASH_LLM_TIMEOUT = 60


def get_llm(temperature=0, max_tokens=None, model=None):
    """主力模型，用于需要深度推理的节点。"""
    kw = dict(
        model=model or MAIN_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
        temperature=temperature,
        request_timeout=MAIN_LLM_TIMEOUT,
    )
    if max_tokens:
        kw["max_tokens"] = max_tokens
    return ChatOpenAI(**kw)


def get_flash_llm(temperature=0, max_tokens=None):
    """轻量快速模型，用于结构化提取、查询生成等。"""
    kw = dict(
        model=FLASH_MODEL,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
        temperature=temperature,
        request_timeout=FLASH_LLM_TIMEOUT,
    )
    if max_tokens:
        kw["max_tokens"] = max_tokens
    return ChatOpenAI(**kw)


# ─── Harness 控制参数 ────────────────────────────────────────────────
MAX_SEARCH_LOOPS = 5          # 最大 worker 轮次（应 ≥ 典型 DAG 深度）
TIME_BUDGET_SECS = 480
MAX_PARALLEL_WORKERS = 3
MAX_PARALLEL_TOOLS = 2
MAX_REFLECTION_NEW_TASKS = 2
ENABLE_CHAIN_OF_VERIFICATION = True
COVE_MAX_QUESTIONS = 2        # 降到 2，省时延
COVE_SKIP_CONF = 0.85         # findings 最高 conf 达此且三角一致可跳过 CoVe

# 三角验证 / 可信度 / 条件检索
TRIANGULATION_AGREE_BOOST = 0.15
MIN_CREDIBILITY_ACCEPT = 0.35
ANCHOR_MIN_CONF = 0.55        # 锚点注入最低 conf
SKIP_SECOND_SOURCE_CONF = 0.85  # 单源足够好则跳过第二源
EXPAND_BELOW_CONF = 0.75      # conf 低于此才 query expand
VERIFY_BELOW_CONF = 0.85      # conf 低于此或未三角才 self-check
