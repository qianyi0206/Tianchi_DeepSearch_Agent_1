# Modifications Summary

This document summarizes the changes applied to the project, why they were made, and how they work.

## Overview
The goal was to improve retrieval quality, reduce failure cases, and add a minimal evidence-coverage loop plus a batch evaluation script. Changes were made to fix a retrieval bug, normalize prompts to ASCII (to avoid encoding corruption), enhance HTML extraction, and add a controlled re-query step when evidence is insufficient.

---

## Changes by Area

### 1) Retrieval Bug Fix
**File:** `deepresearch/nodes/retrieve.py`  
**Problem:** Only the last search result was being fetched because the `fetch` call happened outside the inner loop.  
**Fix:** Move the `fetch` call inside the loop and ensure deduplication works correctly.

**Effect:** Multiple URLs are now fetched as intended, improving recall and stability.

---

### 2) Search Tool Wiring
**File:** `app.py`  
**Problem:** Searcher was hardcoded to `DuckDuckGoSearcher`, ignoring `SerpApi` even when configured.  
**Fix:** Use `build_searcher()` so the runtime picks SerpApi when available.

**Effect:** Better search quality with SerpApi, while still supporting DDG fallback.

---

### 3) API Key Hardcode Removal
**File:** `agent.py`  
**Problem:** `DASHSCOPE_API_KEY` was hardcoded in source.  
**Fix:** Removed the hardcoded key; now it must come from environment variables.

**Effect:** Safer and reproducible configuration.

---

### 4) Query Planning Cleanup
**File:** `deepresearch/nodes/plan_queries.py`  
**Problem:** Prompt was overfit to a single “1972 special issue” case and contained non-ASCII encoding corruption.  
**Fix:** Rewrote prompt in ASCII English; removed hardcoded special-case logic.

**Effect:** Generalizes across tasks and avoids encoding issues.

---

### 5) HTML Extraction Quality Upgrade
**File:** `deepresearch/tools/fetch_tool.py`  
**Problem:** Simple extraction often fails on complex pages.  
**Fix:** Added optional `readability-lxml` extraction with bs4 fallback.

**Effect:** More reliable content extraction while remaining robust without extra dependency.

**Dependency:** `readability-lxml==0.8.4.1` added to `requirements.txt`.

---

### 6) Evidence Coverage Check (Minimal Loop)
**File:** `deepresearch/nodes/coverage_check.py`  
**Added:** A new node that inspects claims + evidence and, if needed, generates 1–3 targeted queries.  
**Retry limit:** 1 (configurable in the node).

**Effect:** When evidence is weak, the system performs one focused re-search instead of guessing.

---

### 7) Entity Expansion
**File:** `deepresearch/nodes/entity_expand.py`  
**Added:** Entity extraction and alias/alternate name expansion.

**Effect:** Improves recall by searching for variants (aliases, translations, pen names).

---

### 8) Claim Verification
**File:** `deepresearch/nodes/verify_claims.py`  
**Added:** Structured claim verification against evidence with missing-claim reporting.

**Effect:** Enables automated evidence checking and targeted re-search.

---

### 9) Time Anchor Extraction
**File:** `deepresearch/nodes/time_anchor.py`  
**Added:** Time/sequence anchor extraction and time-specific query suggestions.

**Effect:** Improves recall for claims that depend on “before/after/resumed/shortly” temporal relations and explicit year anchors.

---

### 10) Timeline Alignment
**File:** `deepresearch/nodes/timeline_align.py`  
**Added:** Year-frequency extraction from evidence and year-focused queries.

**Effect:** Helps lock down a shared year across multiple clues, improving multi-hop alignment.

---

### 11) Candidate Generation and Scoring
**Files:**  
- `deepresearch/nodes/generate_candidates.py`  
- `deepresearch/nodes/score_candidates.py`  
**Added:**  
- Candidate generation (3–5 plausible answers before retrieval).  
- Candidate scoring after retrieval using evidence.

**Effect:** The system evaluates multiple hypotheses instead of jumping to a single answer.

---

### 12) Graph Update
**File:** `deepresearch/graph.py`  
**Change:** Added time-anchor extraction plus timeline alignment and claim verification.  
**Flow:**
```
START -> parse_claims -> entity_expand -> time_anchor -> generate_candidates -> plan_queries -> retrieve
      -> timeline_align -> verify_claims -> coverage_check -> (retrieve | score_candidates) -> finalize -> END
```

---

### 13) State Schema Update
**File:** `deepresearch/state.py`  
**Added fields:**  
- `retry_count`  
- `next_action`
- `candidates`
- `candidate_scores`
- `selected_candidate`
- `entities`
- `expanded_entities`
- `claim_verification`
- `missing_claims`
- `time_anchors`
- `time_queries`
- `timeline_years`
- `timeline_queries`

These fields control the minimal loop behavior.

---

### 14) Batch Evaluation Script
**File:** `run_batch_eval.py`  
**Added:** Batch runner to process `question.jsonl` and output `results.jsonl`.  
**Outputs:** `final_answer`, `final_answer_canonical`, `final_answer_normalized`, `queries`, `entities`, `expanded_entities`, `time_anchors`, `time_queries`, `timeline_years`, `timeline_queries`, `candidates`, `selected_candidate`, `candidate_scores`, `claim_verification`, `missing_claims`, and `sources`.

---

### 15) Answer Canonicalization
**Files:**  
- `deepresearch/utils/answer_normalize.py`  
- `deepresearch/nodes/finalize.py`  
**Added:**  
- Stable extraction of `Final Answer`
- Candidate-aware canonicalization (`Mondadori` vs `Arnoldo Mondadori Editore`)
- Normalized form for robust evaluation

---

### 16) Claim-Scoped Retrieval
**Files:**  
- `deepresearch/nodes/plan_queries.py`  
- `deepresearch/nodes/retrieve.py`  
- `deepresearch/state.py`  
- `run_batch_eval.py`  
**Added:**  
- `claim_queries` in state/output
- claim-scoped query generation (with English fallback)
- global-first retrieval scheduling to avoid early cap exhaustion
- low-value social-domain blocking during fetch

**Usage:**
```bash
python run_batch_eval.py --input question.jsonl --output results.jsonl --start 0 --limit 0
```

---

## Known Limitations
- The minimal loop only retries once to avoid runaway costs.
- The coverage check is LLM-based; it is a heuristic rather than strict verification.

---

## Next Steps (Optional)
- Add claim-to-evidence scoring and logging.
- Add candidate answer generation before finalization.
- Improve document ranking and snippet filtering.
