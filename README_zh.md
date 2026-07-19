# 天池 DeepSearch Agent

[English](./README.md) | [中文](./README_zh.md)

面向复杂事实型问题的**多跳深度研究 Agent**。

系统基于 [LangGraph](https://github.com/langchain-ai/langgraph) 实现 **orchestrator–worker 执行框架（Agent Harness）**：将问题分解为依赖子任务图，由编排器调度并行检索 Worker，结合反思循环、证据导向的上下文管理与答案核验。适用于 DeepSearch 类评测设定（短答案、精确匹配、硬时延约束），可部署于阿里云 PAI-EAS（AgentScope Runtime），通过 Server-Sent Events（SSE）对外提供服务。

---

## 特性概览

| 维度 | 设计 |
|------|------|
| **控制面** | Orchestrator 按子任务 DAG 调度 ready 节点；Worker 执行检索跳；Reflection 决定补任务或收敛 |
| **工具层** | 统一接入 Exa、Serper（Google）、Jina Reader 与百科类摘要；高置信时可走单源快路径 |
| **证据** | Query-aware 段落选取、长文档压缩、域名可信度启发、多源一致性校验 |
| **记忆** | 运行内 `execution_trace` 用于可审计与跨跳复用；可选短/长期 session 接口 |
| **核验** | 候选 self-check；Chain-of-Verification（CoVe）及 citation 元数据 |
| **工程** | 分层超时、单源失败隔离、SSE 保活、批量评测客户端 |

---

## 架构

```
                         ┌──────────────────────────────────────┐
  问题 ──► 初步检索 ──► 问题分解（DAG）                            │
                         │                                       │
                         ▼                                       │
              ┌── Orchestrator ◄──────────────────────┐          │
              │         │                             │          │
              │         ▼                             │          │
              │   Worker 池（并行）                     │          │
              │   改写 → 检索 → 阅读 → 抽取             │          │
              │         │                             │          │
              │         ▼                             │          │
              │    Reflection ── 继续 ────────────────┘          │
              │         │                                        │
              │       收敛                                       │
              └─────────┼────────────────────────────────────────┘
                        ▼
                   补充检索
                        ▼
                   综合（draft）
                        ▼
              Chain-of-Verification
                        ▼
               答案抽取 ──► final_answer（+ citations）
```

**模块职责**

| 模块 | 职责 |
|------|------|
| `agent.py` | 图组装、AgentScope Runtime、SSE 端点 |
| `harness.py` | Orchestrator、Worker 池、Reflection、CoVe |
| `task_graph.py` | 子任务 DAG（依赖、ready 集合、终态） |
| `tools.py` | 搜索/抓取/抽取、压缩、多源交叉验证 |
| `nodes.py` | 预搜、分解、综合、格式化 |
| `memory.py` | 执行轨迹与 session 存储 |
| `plan_tips.py` | 按题型注入规划启发式 tips |
| `config.py` | 模型、预算与阈值（密钥来自环境变量） |
| `run_eval.py` | 批量调用与评分 |

---

## 端到端示例

评测题多为**间接多跳**：实体以属性与关系描述，而非直接点名。以下示例取自 `test_data.jsonl` 同类题目（检索中间结果为示意）。

### 输入

```text
在某一年，一位法国天文学家对一颗彗星的光谱进行了开创性观测，
同年的一张太阳黑子照片后来在东亚某大都市的天文展览中展出。
也正是在这一年，一位尚不满二十岁的南欧创业者，在家乡小镇创办了他的出版事业。
十余年后，他将公司总部迁往了该国北部的商业中心。
他所创立的这家出版公司的名字是什么？
```

### 分解后的任务图

```text
t1  法国天文学家 + 彗星光谱开创性观测  →  哪一年？
t2  （可选）太阳黑子照片 + 东亚都市展览  →  佐证年份
t3  该年 + 南欧 + 未满二十岁创办出版  →  创业者是谁？
t4  创业者 + 总部迁往北部商业中心  →  出版社名称？
```

典型依赖：`t1 → t3 → t4`。无依赖边的子任务可在 ready 后并行执行。

### Worker 轮次（示意）

| 轮次 | 任务 | 检索焦点（示意） | 发现 |
|-----:|------|------------------|------|
| 1 | `t1` | 法国 天文学家 彗星 光谱 | 年份 **1868** |
| 2 | `t3` | `1868` 出版 南欧 创业（注入上游锚点） | **阿诺尔多·蒙达多利** |
| 3 | `t4` | Mondadori 总部 米兰 出版社 | **阿诺尔多·蒙达多利出版社** |

单个 Worker 内部流程：

1. 置信度不足时可选 query 扩展  
2. 多源检索与阅读（Serper / Exa；全文可用 Jina）  
3. 段落排序或 LLM 文档压缩  
4. 结构化抽取：`{candidates, confidence, evidence, sources}`  
5. 多源一致性校验 + 条件 self-check  

### Reflection 与退出

- **收敛（converge）**：终跳证据充分且链路完整，或触及轮次 / 时间预算。  
- **补任务（add_tasks）**：缺少桥接实体（例如有年份、无人名）。  
- 上游任务 **失败不会永久堵死** DAG，下游仍可变为 ready。

### 输出

评测侧期望短文本（exact-match 风格）。服务还可附带 citation：

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

**冒烟测试**（单跳）：

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
result = graph.invoke(_make_initial_state("Where is the capital of France?"))
assert "Paris" in result["final_answer"]
```

---

## 目录结构

```text
.
├── agent.py                 # 入口：图 + SSE
├── harness.py               # 控制循环
├── task_graph.py            # DAG 原语
├── tools.py                 # I/O 与证据流水线
├── nodes.py                 # 前处理 / 后处理节点
├── memory.py                # 轨迹与 session 记忆
├── plan_tips.py             # 规划启发式
├── config.py / state.py
├── run_eval.py
├── test_data.jsonl          # 样例题与标准答案
├── env.example
├── service.example.json
├── README.md                # English
├── README_zh.md             # 中文
└── requirements.txt
```

---

## 环境配置

**依赖：** Python 3.10+，可访问 LLM 与检索服务。

```bash
git clone https://github.com/qianyi0206/Tianchi_DeepSearch_Agent_1.git
cd Tianchi_DeepSearch_Agent_1

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp env.example env.txt             # 填写 API Key
```

### 环境变量

| 变量 | 是否必需 | 说明 |
|------|----------|------|
| `DASHSCOPE_API_KEY` | 是 | DashScope OpenAI 兼容 Chat API |
| `EXA_API_KEY` | 建议 | Exa 语义 / 关键词搜索 |
| `SERPER_API_KEY` | 建议 | 经 Serper 调用 Google |
| `JINA_API_KEY` | 可选 | Jina Reader（无 Key 时有限流） |
| `MAIN_MODEL` | 可选 | 覆盖主模型 ID |
| `FLASH_MODEL` | 可选 | 覆盖轻量模型 ID |

密钥从进程环境或本地 `env.txt` 加载。**请勿**将 `env.txt`、`service.json` 提交入库（均已在 `.gitignore` 中）。

预算与阈值见 `config.py`（如 `MAX_SEARCH_LOOPS`、`TIME_BUDGET_SECS`、三角验证与 CoVe 开关等）。

---

## 使用方式

### 库内直接调用

```python
from agent import build_research_graph, _make_initial_state

graph = build_research_graph()
state = graph.invoke(_make_initial_state(
    "在此填入多跳问题",
    session_id="demo",
))
print(state["final_answer"])
print(state.get("citations"))
```

### HTTP 接口（SSE）

兼容 DeepSearch 类评测协议。

**请求**

```http
POST /
Authorization: Bearer <token>
Content-Type: application/json
Accept: text/event-stream

{"question": "Where is the capital of France?"}
```

**响应流**

```text
event: Ping

event: Message
data: {"answer": "Paris"}
```

图执行期间周期性发送 `event: Ping` 以保活连接；最终以单条 `Message` 返回答案，视情况附带 `citations`。

### 批量评测

```bash
cp service.example.json service.json   # 填写 api_url、auth_token
python run_eval.py
```

读取 `test_data.jsonl`，在本地写出 `result.jsonl` / `score.json`（默认不纳入版本库）。

---

## 设计说明

1. **有界控制循环** — 开放式 ReAct 易突破时限；本框架使用显式 DAG、Worker 轮次上限与墙钟时间预算。  
2. **证据优先于自评置信度** — 模型 conf 仅作软信号；多源一致与域名可信度参与采纳。流控不单独依赖 conf。  
3. **锚点传递** — 仅当**直接上游**任务达到置信度门槛时，才将实体注入下游 query，降低错误传播。  
4. **失败隔离** — 单工具 / 单 Worker 失败不中断整次运行；失败任务仍可解锁依赖方。  
5. **成本与时延权衡** — 单源高 conf 可跳过第二检索引擎；证据已充分交叉验证时可跳过 CoVe。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 编排 | LangGraph `StateGraph` |
| LLM | DashScope 兼容 Chat Completions API |
| 检索与抓取 | Exa、Serper、Jina |
| 服务 | FastAPI、AgentScope Runtime |
| 流式协议 | Server-Sent Events |

---

## 许可证

MIT
