# Tianchi DeepSearch Agent

[English](./README.md) | [中文](./README_zh.md)

A **multi-hop deep research agent** for complex fact-seeking questions.

The system implements an **orchestrator–worker execution harness** on [LangGraph](https://github.com/langchain-ai/langgraph): task decomposition into a dependency graph, parallel retrieval workers, iterative reflection, evidence-aware context management, and post-hoc answer verification. It is designed for DeepSearch-style evaluation (short-form exact answers under a hard latency budget) and can be deployed behind a Server-Sent Events (SSE) API on Alibaba Cloud PAI-EAS via AgentScope Runtime.

---

## Highlights

| Area | Design |
|------|--------|
| **Control plane** | Orchestrator schedules ready nodes on a subtask DAG; workers execute retrieval hops; reflection decides to backfill tasks or converge |
| **Tooling** | Unified Exa, Serper (Google), Jina Reader, and encyclopedia summaries; optional single-source fast path when confidence is high |
| **Evidence** | Query-aware passage selection, long-document compaction, domain credibility heuristics, multi-source agreement checks |
| **Memory** | In-run `execution_trace` for auditability and cross-hop reuse; optional short/long-term session APIs |
| **Verification** | Candidate self-check; chain-of-verification (CoVe) with citation metadata |
| **Production** | Layered timeouts, per-source failure isolation, SSE keep-alive, batch evaluation client |

---

## Architecture

```
                         ┌──────────────────────────────────────┐
  Question ──► Preliminary Search ──► Decomposition (DAG)        │
                         │                                       │
                         ▼                                       │
              ┌── Orchestrator ◄──────────────────────┐          │
              │         │                             │          │
              │         ▼                             │          │
              │   Worker Pool (parallel)              │          │
              │   rewrite → retrieve → read → extract │          │
              │         │                             │          │
              │         ▼                             │          │
              │    Reflection ── continue ────────────┘          │
              │         │                                        │
              │      converge                                    │
              └─────────┼────────────────────────────────────────┘
                        ▼
              Supplementary Search
                        ▼
                   Finalize (draft)
                        ▼
              Chain-of-Verification
                        ▼
               Answer Extraction ──► final_answer (+ citations)
```

**Module map**

| Module | Responsibility |
|--------|----------------|
| `agent.py` | Graph wiring, AgentScope Runtime, SSE endpoint |
| `harness.py` | Orchestrator, worker pool, reflection, CoVe |
| `task_graph.py` | Subtask DAG (dependencies, ready set, terminal states) |
| `tools.py` | Search/fetch/extract, compaction, triangulation |
| `nodes.py` | Preliminary search, decomposition, finalize, formatting |
| `memory.py` | Execution trace and session store |
| `plan_tips.py` | Heuristic planning tips by question type |
| `config.py` | Models, budgets, thresholds (secrets from env) |
| `run_eval.py` | Batch client and scoring harness |

---

## End-to-End Example

Benchmark questions are typically **indirect multi-hop**: entities are described, not named. The following walkthrough uses a representative item (abridged from `test_data.jsonl`). Intermediate retrieval results are illustrative.

### Input

```text
In a certain year, a French astronomer made a pioneering observation of a comet’s spectrum;
a sunspot photograph from the same year was later exhibited in a major East Asian city.
In that same year, a southern-European entrepreneur not yet twenty founded a publishing business
in his hometown, and more than a decade later moved the headquarters to the country’s northern
commercial center. What is the name of the publishing company he founded?
```

*(Chinese original available in `test_data.jsonl` and [README_zh.md](./README_zh.md).)*

### Task graph (after decomposition)

```text
t1  pioneering French comet spectroscopy observation  →  year?
t2  (optional) sunspot photo exhibition  →  corroborate year
t3  year + young southern-European publisher  →  founder?
t4  founder + HQ move to northern commercial center  →  company name?
```

Typical dependency: `t1 → t3 → t4`. Independent hops may use empty `depends_on` and run in parallel when ready.

### Worker rounds (illustrative)

| Round | Task | Anchored query (sketch) | Finding |
|------:|------|-------------------------|---------|
| 1 | `t1` | French astronomer comet spectrum | year **1868** |
| 2 | `t3` | `1868` publishing founder southern Europe | **Arnoldo Mondadori** |
| 3 | `t4` | Mondadori headquarters Milan publisher | **Arnoldo Mondadori Editore** / Chinese full name as required |

Per-worker pipeline:

1. Optional query expansion (when confidence is low)  
2. Multi-source retrieve & read (Serper / Exa; Jina for full text)  
3. Passage ranking or LLM compaction  
4. Structured extract: `{candidates, confidence, evidence, sources}`  
5. Cross-source agreement + conditional self-check  

### Reflection and exit

- **Converge** when the final hop is supported and the chain is complete, or when the budget / max rounds are exhausted.  
- **Add tasks** when a bridge entity is missing (e.g. year found, founder not).  
- Failed upstream tasks do **not** permanently block the DAG; dependents can still become ready.

### Output

Evaluation expects a short string (exact-match style). The service may also attach citations:

```json
{
  "answer": "阿诺尔多·蒙达多利出版社",
  "citations": [
    {
      "claim": "阿诺尔多·蒙达多利出版社",
      "evidence": "…",
      "source": "https://…"
    }
  ]
}
```

**Smoke test** (single-hop):

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
result = graph.invoke(_make_initial_state("Where is the capital of France?"))
assert "Paris" in result["final_answer"]
```

---

## Repository Layout

```text
.
├── agent.py                 # Entry: graph + SSE
├── harness.py               # Control loop
├── task_graph.py            # DAG primitives
├── tools.py                 # I/O and evidence pipeline
├── nodes.py                 # Pre/post pipeline nodes
├── memory.py                # Trace & session memory
├── plan_tips.py             # Planning heuristics
├── config.py / state.py
├── run_eval.py
├── test_data.jsonl          # Sample questions + gold answers
├── env.example
├── service.example.json
├── README.md                # English
├── README_zh.md             # 中文
└── requirements.txt
```

---

## Setup

**Requirements:** Python 3.10+, network access to LLM and search providers.

```bash
git clone https://github.com/qianyi0206/Tianchi_DeepSearch_Agent_1.git
cd Tianchi_DeepSearch_Agent_1

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp env.example env.txt             # fill in API keys
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DASHSCOPE_API_KEY` | Yes | DashScope OpenAI-compatible chat API |
| `EXA_API_KEY` | Recommended | Exa neural / keyword search |
| `SERPER_API_KEY` | Recommended | Google results via Serper |
| `JINA_API_KEY` | Optional | Jina Reader (rate limits apply without key) |
| `MAIN_MODEL` | Optional | Override primary model id |
| `FLASH_MODEL` | Optional | Override lightweight model id |

Secrets are loaded from the process environment or local `env.txt`. Do **not** commit `env.txt` or `service.json` (both are gitignored).

Tunable budgets and thresholds live in `config.py` (e.g. `MAX_SEARCH_LOOPS`, `TIME_BUDGET_SECS`, triangulation and CoVe gates).

---

## Usage

### Library / offline invoke

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
state = graph.invoke(_make_initial_state(
    "Your multi-hop question here",
    session_id="demo",
))
print(state["final_answer"])
print(state.get("citations"))
```

### HTTP API (SSE)

Compatible with the DeepSearch-style evaluation protocol.

**Request**

```http
POST /
Authorization: Bearer <token>
Content-Type: application/json
Accept: text/event-stream

{"question": "Where is the capital of France?"}
```

**Response stream**

```text
event: Ping

event: Message
data: {"answer": "Paris"}
```

`event: Ping` is emitted periodically while the graph runs (connection keep-alive). The final payload is a single `Message` event; `citations` may be included when available.

### Batch evaluation

```bash
cp service.example.json service.json   # set api_url and auth_token
python run_eval.py
```

Reads `test_data.jsonl`, writes `result.jsonl` / `score.json` locally (not versioned by default).

---

## Design Notes

1. **Bounded control loop** — Open-ended ReAct agents often exceed contest time limits. This harness uses an explicit DAG, a maximum number of worker rounds, and a wall-clock budget.  
2. **Evidence over confidence alone** — LLM self-reported confidence is a soft signal; multi-source agreement and domain credibility adjust acceptance. Flow control does not rely on confidence alone.  
3. **Anchor propagation** — Downstream queries inject entities only from **direct upstream** tasks that meet a confidence floor, reducing error cascade.  
4. **Failure isolation** — Individual tools or workers may fail without aborting the run; failed tasks still unlock dependents.  
5. **Cost–latency trade-offs** — High-confidence single-source results skip a second engine; CoVe can be skipped when findings are already strong and cross-validated.

---

## Stack

| Layer | Technology |
|-------|------------|
| Orchestration | LangGraph `StateGraph` |
| LLM | DashScope-compatible Chat Completions API |
| Search & fetch | Exa, Serper, Jina |
| Serving | FastAPI, AgentScope Runtime |
| Streaming | Server-Sent Events |

---

## License

MIT
