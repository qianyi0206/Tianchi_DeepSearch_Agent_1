# deepresearch/schemas.py
# -*- coding: utf-8 -*-
"""
这里定义最小数据结构（pydantic）：
- Claim：题干拆出来的“可验证约束”
- SearchResult：搜索结果条目
- Document：打开网页/文档后保存的正文
- FinalAnswer：最终输出（答案 + 引用）
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field

class Claim(BaseModel):
    id:str = Field(...,description="claim 的唯一 id，比如 c1/c2")
    description:str= Field(...,description="claim的描述")
    must: bool = Field(default=True, description="是否必须满足")

class SearchResult(BaseModel):
    ## 搜索得到的结果
    title: str
    url: str
    snippet: Optional[str] = None

class Document(BaseModel):
    ## 抓取网页或 PDF得到的文档内容
    url: str
    title: Optional[str] = None
    content: str = Field(..., description="正文文本")

class FinalAnswer(BaseModel):
    """最终输出结构：答案 + 引用来源列表（S1..Sn）"""
    answer: str
    sources: List[str] = Field(default_factory=list, description="来源 url 列表，按顺序对应 S1/S2...")