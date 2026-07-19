# Tianchi DeepSearch Agent

多跳复杂问答的 Deep Research Agent。  
基于 LangGraph 的 **orchestrator–worker** 执行框架，统一完成任务编排、多源检索、上下文压缩、执行记忆与答案核验。

面向天池 DeepSearch 类场景设计，支持在阿里云 PAI-EAS（AgentScope Runtime）上以 SSE 接口部署评测。

---

## 系统流程

```
Question
    │
    ▼
Preliminary Search          多源预检索 + 问题预分析
    │
    ▼
Question Decomposition      多跳拆解 → 子任务 DAG
    │
    ▼
┌─ Orchestrator ──────────── 按依赖调度 ready 子任务
│       │
│       ▼
│  Worker Pool              并行执行：query 改写 → 检索/阅读/抽取
│       │                   Exa ∥ Serper，可信度加权 + 多源交叉验证
│       ▼
│  Reflection               缺口分析：回溯补任务 / 收敛
│       │
└───────┘  (轮次与时间预算兜底)
    │
    ▼
Supplementary Search
    │
    ▼
Finalize                    综合证据生成 draft 答案
    │
    ▼
Chain-of-Verification       验证问题 + 证据核对 + citation
    │
    ▼
Answer Extraction           类型约束与格式归一化
```

---

## 能力概览

**编排与控制**

- 问题分解为子任务 DAG，orchestrator 调度、worker 并行执行  
- Reflection：证据不足时补子任务，充分或无增量信息时收敛  
- 轮次上限与时间预算双重兜底  

**检索与工具**

- 统一接入 Exa、Serper（Google）、Jina Reader、百科类摘要  
- 检索 → 阅读 → 抽取 → query 改写闭环  
- 查询去重；来源域名可信度；多源结果交叉验证后采纳  
- 单源高置信快路径，失败源降级不断链  

**上下文**

- Query-aware 段落抽取  
- 超长文档触发 LLM 压缩，控制 token 占用  

**记忆**

- `execution_trace` 记录候选、证据与步骤，并回注后续推理  
- 短/长期 session 接口（进程内）  

**核验**

- 候选 self-check；Chain-of-Verification  
- 答案与证据对齐，输出 citation 元数据  

**工程**

- SSE：`event: Ping` 保活 + `event: Message` 返回答案  
- 分层超时与异常降级  
- `run_eval.py` 批量调用与评分  

---

## 目录结构

```text
agent.py            图组装、Runtime、SSE 入口
harness.py          orchestrator / worker_pool / reflection / CoVe
task_graph.py       子任务 DAG
memory.py           执行轨迹与 session 记忆
nodes.py            预搜、分解、综合、答案抽取
tools.py            搜索、抓取、压缩、多源验证
plan_tips.py        领域检索策略 tips
config.py           模型与阈值（密钥从环境变量读取）
state.py            全局状态定义
run_eval.py         批量评测客户端
test_data.jsonl     样例题目
env.example         环境变量模板
service.example.json  评测服务配置模板
```

---

## 环境配置

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp env.example env.txt
# 编辑 env.txt
```

| 变量 | 说明 |
|------|------|
| `DASHSCOPE_API_KEY` | 通义千问（DashScope OpenAI 兼容接口） |
| `EXA_API_KEY` | Exa 搜索 |
| `SERPER_API_KEY` | Serper / Google 搜索 |
| `JINA_API_KEY` | Jina 网页阅读（可选） |
| `MAIN_MODEL` / `FLASH_MODEL` | 可选，覆盖默认模型名 |

密钥仅通过环境变量或本地 `env.txt` 注入，**不要提交**到仓库。`env.txt`、`service.json` 已在 `.gitignore` 中。

阈值与超时见 `config.py`（如 `MAX_SEARCH_LOOPS`、`TIME_BUDGET_SECS`）。

---

## 使用方式

### 直接调用图

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
out = graph.invoke(_make_initial_state("你的多跳问题"))
print(out["final_answer"])
```

### HTTP（SSE）

部署到 AgentScope Runtime / PAI-EAS 后：

```bash
curl -N -X POST "$ENDPOINT" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"question": "Where is the capital of France?"}'
```

```text
event: Ping

event: Message
data: {"answer": "Paris"}
```

可选字段：`citations`（核验阶段生成的来源列表）。

### 批量评测

```bash
cp service.example.json service.json
# 填写 api_url、auth_token
python run_eval.py
```

读取 `test_data.jsonl`，结果写入 `result.jsonl`（本地文件，默认不入库）。

---

## 技术栈

| 组件 | 选型 |
|------|------|
| 编排 | LangGraph `StateGraph` |
| LLM | DashScope 兼容 Chat API（可配置模型名） |
| 搜索 | Exa、Serper、Jina |
| 服务 | FastAPI + AgentScope Runtime |
| 协议 | Server-Sent Events |

---

## License

MIT
