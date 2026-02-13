# -*- coding: utf-8 -*-
"""
配置管理
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _getenv(name: str, default: Optional[str] = None, required: bool = False) -> str:
    """
    小工具：读取环境变量，并在 required=True 时做校验。
    """
    val = os.getenv(name, default)
    if required and (val is None or val.strip() == ""):
        raise RuntimeError(
            f"缺少环境变量 {name}。请在 .env 中配置，例如：\n"
            f"{name}=your_value"
        )
    return val


@dataclass()
class LLMConfig:
    model: str
    api_key: str
    base_url: str
    temperature: float = 0.2


def load_llm_config() -> LLMConfig:
    return LLMConfig(
        model=_getenv("DEEPRESEARCH_MODEL", default="qwen-plus"),
        api_key=_getenv("DASHSCOPE_API_KEY", required=True),
        base_url=_getenv(
            "DEEPRESEARCH_BASE_URL",
            default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        temperature=float(_getenv("DEEPRESEARCH_TEMPERATURE", default="0.2")),
    )


def create_llm():
    from langchain_openai import ChatOpenAI

    cfg = load_llm_config()
    return ChatOpenAI(
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
    )


def enable_langsmith_tracing_from_env() -> None:
    """
    想开 LangSmith tracing，可以在 .env 里配置 :ENABLE_LANGSMITH=1
    需要时，在 app.py/agent.py 的初始化里手动调用一次即可。
    """
    enable = _getenv("ENABLE_LANGSMITH", default="0")
    if enable not in ("1", "true", "True", "YES", "yes"):
        return

    # 可配置
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"
