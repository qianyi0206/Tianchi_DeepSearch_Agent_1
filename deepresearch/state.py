# deepresearch/state.py
# -*- coding: utf-8 -*-
"""
LangGraph State
"""
from __future__ import annotations
from typing import Annotated, List, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from .schemas import Claim, Document


class DeepResearchState(TypedDict, total=False):
    # Conversation context
    messages: Annotated[List[BaseMessage], add_messages]

    # Parsed question and claims
    question: str
    claims: List[Claim]

    # Retrieval
    queries: List[str]
    claim_queries: dict
    documents: List[Document]

    # Candidates
    candidates: List[str]
    candidate_scores: list
    selected_candidate: str

    # Entities
    entities: List[str]
    expanded_entities: List[str]

    # Claim verification
    claim_verification: list
    missing_claims: List[str]

    # Time anchors
    time_anchors: List[str]
    time_queries: List[str]

    # Timeline alignment
    timeline_years: List[str]
    timeline_queries: List[str]

    # Control for minimal loop
    retry_count: int
    next_action: str

    # Final answer
    final_answer: str
    final_answer_canonical: str
    final_answer_normalized: str
