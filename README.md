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

## Example：一题怎么被解出来

下面用一道**多跳间接指代**题说明流水线（结构与 `test_data.jsonl` 同类；中间检索结果为示意）。

### 输入

```text
在某一年，一位法国天文学家对一颗彗星的光谱进行了开创性观测，
同年的一张太阳黑子照片后来在东亚某大都市的天文展览中展出。
也正是在这一年，一位尚不满二十岁的南欧创业者，在家乡小镇创办了他的出版事业。
十余年后，他将公司总部迁往了该国北部的商业中心。
他所创立的这家出版公司的名字是什么？
```

题干不直接给出人名、年份或公司名，需要**逐跳锁定中间实体**。

### 1. Decomposition → 子任务 DAG

```text
t1  法国天文学家 + 彗星光谱 开创性观测 → 哪一年？
t2  同年 / 太阳黑子照片 + 东亚都市展览 → 佐证年份（可选）
t3  该年 + 南欧 + 不满二十岁 + 创办出版 → 创业者是谁？
t4  创业者 + 总部迁往北部商业中心 + 出版社 → 公司名？
```

依赖大致为 `t1 → t3 → t4`（串行锚点传递）；独立线索可并行。

### 2. Orchestrator + Worker（示意）

| 轮次 | 调度 | 检索焦点（示意） | 结构化发现 |
|------|------|------------------|------------|
| 1 | `t1` | `法国 天文学家 彗星 光谱 观测` | 候选年份 **1868**，附证据与 URL |
| 2 | `t3` | `1868 出版 南欧 创业`（注入上游锚点） | 候选人物 **阿诺尔多·蒙达多利** |
| 3 | `t4` | `Mondadori 出版社 总部 米兰` | 候选公司名 **阿诺尔多·蒙达多利出版社** |

每个 Worker 内部大致是：

```text
search_query
    →（可选）query 改写扩展
    → Serper / Exa 检索与阅读（高 conf 可单源快路径）
    → 段落抽取 / 长文压缩
    → 抽取 {candidates, confidence, evidence, sources}
    → 多源交叉验证 + 必要时 self-check
```

### 3. Reflection

- 若 `t4` 已有高 conf 公司名且链路完整 → **converge**  
- 若缺桥接实体（例如只有年份没有人名）→ **add_tasks** 补搜，再进入下一轮 orchestrator  

### 4. 输出

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

标准答案侧（评测）为 exact match 短文本，例如：`阿诺尔多·蒙达多利出版社`。

### 和单跳问答的差别

| | 普通 QA | 本 Agent |
|--|---------|----------|
| 问题形态 | 实体往往直接出现 | 间接描述，需多跳 |
| 检索 | 常一次搜索 | DAG 多轮，锚点传递 |
| 中间结果 | 可丢弃 | 写入 `findings` / `execution_trace` 供后续使用 |
| 输出 | 长回答亦可 | 竞赛向：**短答案 + 可核验证据** |

更简单的烟测输入：

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
out = graph.invoke(_make_initial_state("Where is the capital of France?"))
print(out["final_answer"])  # 期望: Paris
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
