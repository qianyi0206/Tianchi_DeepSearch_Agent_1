# Deep Research Agent Harness

面向**多跳复杂问答**的 Deep Research Agent 执行框架（Agent Harness）。

基于 **orchestrator–worker** 多智能体范式，用 LangGraph 打通：

**编排 → 多源工具调用 → 上下文压缩 → 执行记忆 → 结果核验**

曾用于阿里云天池 DeepSearch 竞赛场景（PAI-EAS / SSE 评测接口），也可作为通用多跳检索研究 Agent 的参考实现。

---

## 架构

```
用户问题
    │
preliminary_search          # 多源预搜 + 问题 Hint
    │
question_decomposition      # 多跳分解 → 子任务 DAG
    │
┌─► orchestrator            # 依赖分析，调度 ready 子任务
│       │
│   worker_pool             # 并行 Worker：改写 → Exa∥Serper → 三角验证 → self-check
│       │
│   reflection              # 缺口分析：补子任务 / 收敛（轮次 + 时间兜底）
│       │
└───────┘
    │
supplementary_search
    │
finalize_summary            # 综合 draft 答案
    │
chain_of_verification       # CoVe + citation
    │
answer_extraction           # 类型规则 + 格式归一 + session 记忆
```

| 能力 | 实现要点 |
|------|----------|
| 编排与控制循环 | `task_graph` 子任务 DAG；`orchestrator` / `worker_pool` / `reflection` |
| 多工具并行 | Exa / Serper / Jina / 百科；高 conf 快路径；多源三角验证与域名可信度 |
| 上下文工程 | query-aware 段落抽取；超长文档 LLM compaction |
| 记忆与审计 | `execution_trace` 注入后续轮次；进程内 short/long-term memory API |
| 结果核验 | 候选 self-check；Chain-of-Verification；claim↔source citation |
| 工程化 | SSE（Ping + Message）、分层超时、单源失败不断链、批量评测脚本 |

---

## 项目结构

```
├── agent.py          # LangGraph 组装 + AgentScope Runtime + SSE 端点
├── harness.py        # orchestrator / worker_pool / reflection / CoVe
├── task_graph.py     # 子任务 DAG
├── memory.py         # 可审计轨迹与 session 记忆
├── nodes.py          # 预搜 / 分解 / 综合 / 答案抽取
├── tools.py          # 搜索工具、压缩、三角验证、并行多源
├── plan_tips.py      # 领域 Plan Tips 规则库
├── config.py         # 模型与阈值（从环境变量读 Key）
├── state.py          # ResearchState
├── run_eval.py       # 批量评测客户端
├── test_data.jsonl   # 样例题（含标准答案，便于本地对照）
├── env.example       # 密钥模板
└── service.example.json
```

---

## 快速开始

### 1. 环境

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp env.example env.txt
# 编辑 env.txt，填入 DASHSCOPE / EXA / SERPER / JINA
```

### 2. 本地调用图（不启 HTTP）

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
result = graph.invoke(_make_initial_state("Where is the capital of France?"))
print(result["final_answer"])
```

### 3. 天池兼容 SSE 接口

部署于 AgentScope Runtime / PAI-EAS 后：

```bash
curl -X POST "$ENDPOINT" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Where is the capital of France?"}'
```

响应示例：

```text
event: Ping

event: Message
data: {"answer": "Paris", "citations": [...]}
```

### 4. 批量评测

```bash
cp service.example.json service.json
# 填写 api_url / auth_token
python run_eval.py
```

---

## 配置说明

密钥与模型通过环境变量或 `env.txt` 注入（**不要提交真实 Key**）：

| 变量 | 用途 |
|------|------|
| `DASHSCOPE_API_KEY` | 通义 / DashScope OpenAI 兼容接口 |
| `EXA_API_KEY` | Exa 语义搜索 |
| `SERPER_API_KEY` | Google 搜索（Serper） |
| `JINA_API_KEY` | Jina Reader 网页抓取 |
| `MAIN_MODEL` / `FLASH_MODEL` | 可选，覆盖默认模型名 |

调参集中在 `config.py`：`MAX_SEARCH_LOOPS`、`TIME_BUDGET_SECS`、三角验证阈值、CoVe 开关等。

---

## 设计要点（面试可讲）

1. **有界控制循环**：开放式 ReAct 易超时；改为 DAG + 固定轮次 / 时间预算收敛。  
2. **失败不堵链**：上游 `failed` 仍解锁下游，避免整图卡死。  
3. **条件多源**：Serper 高 conf 走快路径；否则补 Exa 并做三角验证。  
4. **锚点传递**：仅从直接上游高 conf 结果注入，降低错误实体污染。  
5. **CoVe 可跳过**：证据充分且已三角一致时跳过，省时延。  

---

## 安全说明

- 仓库内 **不包含** 真实 API Key / EAS Token。  
- 本地使用 `env.txt`、`service.json`（已在 `.gitignore`）。  
- 若密钥曾出现在历史提交中，请在对应平台**轮换密钥**。  

---

## License

MIT（可按需修改）
