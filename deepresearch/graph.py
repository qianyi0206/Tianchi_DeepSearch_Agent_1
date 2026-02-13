# deepresearch/graph.py
# -*- coding: utf-8 -*-
"""
Graph:
START -> parse_claims -> entity_expand -> time_anchor -> generate_candidates -> plan_queries -> retrieve
      -> timeline_align -> verify_claims -> coverage_check -> (retrieve | score_candidates) -> finalize -> END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .state import DeepResearchState
from .nodes.parse_claims import make_parse_claims_node
from .nodes.entity_expand import make_entity_expand_node
from .nodes.time_anchor import make_time_anchor_node
from .nodes.timeline_align import make_timeline_align_node
from .nodes.generate_candidates import make_generate_candidates_node
from .nodes.plan_queries import make_plan_queries_node
from .nodes.retrieve import make_retrieve_node
from .nodes.verify_claims import make_verify_claims_node
from .nodes.coverage_check import make_coverage_check_node
from .nodes.score_candidates import make_score_candidates_node
from .nodes.finalize import make_finalize_node


def build_deepresearch_graph(llm, searcher, fetcher):
    g = StateGraph(DeepResearchState)
    g.add_node("parse_claims", make_parse_claims_node(llm))
    g.add_node("entity_expand", make_entity_expand_node(llm))
    g.add_node("time_anchor", make_time_anchor_node(llm))
    g.add_node("generate_candidates", make_generate_candidates_node(llm))
    g.add_node("plan_queries", make_plan_queries_node(llm))
    g.add_node("retrieve", make_retrieve_node(searcher, fetcher))
    g.add_node("timeline_align", make_timeline_align_node(llm))
    g.add_node("verify_claims", make_verify_claims_node(llm))
    g.add_node("coverage_check", make_coverage_check_node(llm))
    g.add_node("score_candidates", make_score_candidates_node(llm))
    g.add_node("finalize", make_finalize_node(llm))

    g.add_edge(START, "parse_claims")
    g.add_edge("parse_claims", "entity_expand")
    g.add_edge("entity_expand", "time_anchor")
    g.add_edge("time_anchor", "generate_candidates")
    g.add_edge("generate_candidates", "plan_queries")
    g.add_edge("plan_queries", "retrieve")
    g.add_edge("retrieve", "timeline_align")
    g.add_edge("timeline_align", "verify_claims")
    g.add_edge("verify_claims", "coverage_check")

    def _route(state: DeepResearchState) -> str:
        return state.get("next_action", "score_candidates")

    g.add_conditional_edges(
        "coverage_check",
        _route,
        {"retrieve": "retrieve", "finalize": "finalize", "score_candidates": "score_candidates"},
    )
    g.add_edge("score_candidates", "finalize")
    g.add_edge("finalize", END)
    return g
