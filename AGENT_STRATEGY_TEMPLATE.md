# Agent Strategy Template (DeepSearch / Multi-hop)

This template captures a reusable problem‑solving strategy distilled from successful answers. It is intended to guide prompt design and node logic.

---

## Goal
Solve multi-hop, evidence‑grounded questions by:
1) decomposing into verifiable claims,
2) generating candidate answers,
3) retrieving targeted evidence,
4) scoring candidates against evidence,
5) finalizing with citations or “Unknown”.

---

## Core Flow
```
parse_claims
  -> entity_expand
  -> time_anchor
  -> generate_candidates
  -> plan_queries
  -> retrieve
  -> timeline_align
  -> verify_claims
  -> coverage_check (1 retry max)
  -> score_candidates
  -> finalize
```

---

## Node Templates

### 1) parse_claims
**Purpose:** Convert the question into short, verifiable claims.
**Output:** `claims[]` (each with id + description + must=true)
**Heuristics:**
- Split by time, person, institution, award, publication, artifact.
- Include years, locations, names, and title‑like strings.

**Prompt skeleton:**
```
You are an information extractor.
Return JSON list only: [{"id":"c1","description":"...","must":true}, ...]
Make each claim concrete (name/year/location/relation).
Question: {question}
```

---

### 2) entity_expand
**Purpose:** Expand key entities with aliases/translations/pen names.
**Output:** `entities[]`, `expanded_entities[]`
**Heuristics:**
- Include people, places, orgs, works, awards.
- Add known aliases and historical names.

**Prompt skeleton:**
```
Extract entities and alias expansions.
Return JSON: {"entities":[...], "expanded":[...]}
```

---

### 3) generate_candidates
**Purpose:** Produce 3–5 plausible answers before evidence.
**Output:** `candidates[]`
**Heuristics:**
- Consider aliases, old names, translations.
- If question asks for a title, produce likely titles.

**Prompt skeleton:**
```
Generate 3–5 plausible candidate answers.
Use the claims as constraints. Return JSON list only.
```

---

### 3.5) time_anchor
**Purpose:** Extract time/sequence anchors and generate time-specific queries.
**Output:** `time_anchors[]`, `time_queries[]`
**Heuristics:**
- Focus on “before/after/resumed/shortly” patterns.
- Prefer official schedule/press release sources.
- Inject explicit year anchors into queries when present.

**Prompt skeleton:**
```
Extract time anchors and propose time-specific queries.
Return JSON: {"time_anchors":[...], "time_queries":[...]}
```

---

### 4) plan_queries
**Purpose:** Turn claims + candidates into targeted search queries.
**Output:** `queries[]`
**Heuristics:**
- Mix exact phrases and broad terms.
- At least one query per key entity.
- Add `filetype:pdf` for academic topics.
- Use bilingual queries when relevant.

**Prompt skeleton:**
```
Generate 3–5 queries based on claims and candidates.
Return JSON list only.
```

---

### 5) retrieve
### 5.5) timeline_align
**Purpose:** Extract likely year anchors from evidence and propose year-focused queries.
**Output:** `timeline_years[]`, `timeline_queries[]`
**Heuristics:**
- Use most frequent years in evidence as candidates.
- Propose 1–3 targeted queries combining year + key entities.

**Prompt skeleton:**
```
Propose likely year anchors and queries.
Return JSON: {"years":[...], "queries":[...]}
```

**Purpose:** Collect documents for queries.
**Output:** `documents[]` (URL + title + content)
**Heuristics:**
- Deduplicate by URL.
- Cap total docs (e.g., 6–10).
- Prefer primary sources.

---

### 6) verify_claims
**Purpose:** Map each claim to evidence sources and mark missing claims.
**Output:** `claim_verification[]`, `missing_claims[]`

**Prompt skeleton:**
```
Verify each claim against evidence.
Return JSON: {"items":[...], "missing_claims":[...]}
```

---

### 7) coverage_check (max 1 retry)
**Purpose:** Identify missing claims and propose targeted queries.
**Output:** `queries[]` updated, `retry_count+1`, `next_action`
**Heuristics:**
- If no evidence or missing claims, add 1–3 targeted queries.

**Prompt skeleton:**
```
Check if evidence covers claims.
Return JSON: {"missing_claims":[...], "queries":[...]}
```

---

### 8) score_candidates
**Purpose:** Score each candidate using evidence.
**Output:** `candidate_scores[]`, `selected_candidate`
**Heuristics:**
- Use 0–5 score scale.
- Require evidence‑based reasoning.

**Prompt skeleton:**
```
Score each candidate (0–5) using evidence.
Return JSON with scores and "best".
```

---

### 9) finalize
**Purpose:** Produce final answer with citations.
**Output:** `final_answer`
**Rules:**
- Must cite each key statement with [Sx].
- If evidence insufficient, answer “Unknown” and explain missing evidence.
- Canonicalize the answer surface form using candidate equivalence.

---

## Evidence Strategy Cheatsheet
- **Awards/Emmys:** search the award page + the platform’s newsroom.
- **Books/Statements/Documentaries:** search “author name + statement + year” and “documentary title”.
- **Historical figures:** search “alias/epithet” + “primary sources”.
- **TV tournaments:** search official show site + recap/news coverage.

---

## Logging Schema (for evaluation)
Store per‑question:
```
id, question, claims, queries, candidates, selected_candidate,
sources (title/url), final_answer
```

This makes debugging and regression analysis straightforward.
