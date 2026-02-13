# DeepResearch Baseline（4-Node LangGraph）

一个最小可运行的 Deep Research Agent Baseline：
**先把问题拆成可验证约束（claims）→ 生成检索 query → 搜索并抓取证据 → 基于证据给出答案 + 引用**。

> 设计目标：先跑通“有证据地回答”，不引入候选池/并行/预算/复杂验证等认知负担。
> 后续扩展会基于这个骨架逐步加能力。

------

## 特性概览

- ✅ **4 节点研究链路**：parse_claims → plan_queries → retrieve → finalize
- ✅ **证据驱动输出**：finalize 强制引用 `[S1][S2]...`，证据不足返回 `Unknown` / “无法唯一确定”
- ✅ **可替换的搜索实现**：支持 SerpApi（推荐）/ DuckDuckGo（fallback）/ Mock（无网可跑）
- ✅ **可替换的抓取实现**：HTML 抽取 + PDF 提取（最小版）
- ✅ **LangGraph 编排**：状态（State）贯穿全流程，便于后续加循环/检查点

------

## 快速开始（Quickstart）

### 1) 安装依赖

```bash
pip install python-dotenv langgraph langchain-core langchain-openai httpx beautifulsoup4 pypdf
# 如果你要用 DuckDuckGo 搜索：
pip install ddgs
```

> 推荐用 SerpApi（稳定、结果结构化），DuckDuckGo 在部分环境可能不稳定或受网络影响。

### 2) 配置 `.env`

在项目根目录创建 `.env`：

```bash
# LLM（DashScope OpenAI-compatible）
DASHSCOPE_API_KEY=YOUR_KEY
DEEPRESEARCH_MODEL=qwen-plus
DEEPRESEARCH_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEEPRESEARCH_TEMPERATURE=0.2

# 搜索（推荐 SerpApi）
SERPAPI_API_KEY=YOUR_SERPAPI_KEY
SERPAPI_ENGINE=google   # 或 baidu
SERPAPI_MAX_RESULTS=5
```

### 3) 运行单题（本地直接跑图）

如果你有 `run_one_eval.py`（示例脚本），运行：

```bash
python run_one_eval.py
```

你会在控制台看到：

- parse_claims 输出的 claims
- plan_queries 输出的 queries
- retrieve 抓到的 sources（S1/S2/...）
- finalize 输出最终回答（含引用）

------

## 项目架构概览

核心逻辑分为三层：

1. **Nodes（研究节点）**
   - parse_claims：题干 → 约束列表（claims）
   - plan_queries：claims → 检索 queries（3~5 条）
   - retrieve：搜索 + 抓取网页/PDF → documents（证据包）
   - finalize：基于证据包输出答案 + 引用
2. **Tools（工具层）**
   - search_tool：统一搜索接口（SerpApi / DDG / Mock）
   - fetch_tool：统一抓取接口（HTML / PDF）
3. **Graph（LangGraph 编排层）**
   - graph.py 串联 4 个节点形成一条线性工作流
   - state.py 定义贯穿全流程的 state（单一真相源）

------

## 目录结构与文件说明

```
.
├── app.py                      # （可选）提供 HTTP/AgentApp 服务入口（如果你接了 agentscope_runtime）
├── run_one.py                   # 本地单题运行示例（可选）
├── run_one_eval.py              # 你自己的评测脚本（示例）
├── deepresearch/
│   ├── __init__.py
│   ├── config.py                # 配置加载（dotenv），创建 LLM
│   ├── schemas.py               # 数据结构：Claim/Document/FinalAnswer 等
│   ├── state.py                 # LangGraph State 定义（messages/claims/queries/documents/final_answer）
│   ├── graph.py                 # 组装 LangGraph（4 节点线性流程）
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── search_tool.py       # 搜索实现（SerpApi 优先，DDG fallback，支持 build_searcher）
│   │   ├── fetch_tool.py        # 抓取实现（HTML提取 + PDF提取）
│   │   └── mock_tools.py        # （可选）无网环境 MockSearcher/MockFetcher
│   └── nodes/
│       ├── __init__.py
│       ├── parse_claims.py      # 节点1：题干→约束 claims（LLM结构化输出）
│       ├── plan_queries.py      # 节点2：claims→queries（中英策略）
│       ├── retrieve.py          # 节点3：search + fetch → documents（证据包）
│       └── finalize.py          # 节点4：证据包→答案（强制引用）
```

### deepresearch/config.py

- 自动加载 `.env`（python-dotenv）
- 提供 `create_llm()`，统一创建 LLM（避免散落在各处）
- 可选提供 tracing 开关（建议后期再开）

### deepresearch/schemas.py

- 定义最小可用的数据结构（pydantic）
- 让节点之间的输入输出更清晰、更利于协作与调试

### deepresearch/state.py

- LangGraph 的 state：这是图运行时的“全局共享数据”
- baseline 只包含必需字段：claims/queries/documents/final_answer 等

### deepresearch/graph.py

- 串联 4 个节点的线性流程：START → parse_claims → plan_queries → retrieve → finalize → END

### deepresearch/tools/search_tool.py

- 推荐使用 SerpApi（结构化返回、稳定）
- 提供 `build_searcher()`：按 `.env` 自动选择 SerpApi 或 DDG
- 建议保留“失败兜底”：网络问题不应导致流程崩溃，而是返回空结果并让 finalize 给出“证据不足”

### deepresearch/tools/fetch_tool.py

- 最小 HTML 提取：BeautifulSoup 清理脚本/样式并抽取文本
- PDF 提取：pypdf 抽取前几页（baseline 够用）

### deepresearch/tools/mock_tools.py（可选）

- 无网环境也能跑通流程：用预置文档模拟 search+fetch
- 用于训练“拆约束 → 证据输出”的开发习惯

### deepresearch/nodes/*

- 每个节点一个文件：便于替换、测试、并行协作
- 节点之间只通过 state 交互，降低耦合

------

## 常见问题（FAQ）

### Q1：为什么 finalize 输出 Unknown？

这通常是正确行为：**证据包 documents 不包含题干要求的信息**。
常见原因：

- 搜索命中不相关内容（例如抓到了词频表/语料库）
- 抓取失败太多，只剩下“最容易抓”的垃圾链接
- query 太长太苛刻，导致召回差

建议打开 `retrieve` 的调试日志（打印每个 query 的结果数、每个 url 抓取失败原因）。

### Q2：为什么只抓到 1 个 URL？

可能是：

- 环境网络受限/反爬导致 fetch 失败
- 搜索源返回少或 query 太苛刻
- retrieve 内部限制了 max_total_docs

建议：

- 先用 SerpApi
- 在 retrieve 增加抓取失败日志
- 增加轻量相关性过滤（锚点词判断）

------

## 发展路线（Roadmap）

在保持 4 节点骨架不变的前提下，推荐按顺序扩展：

1. **检索质量**
   - query 生成策略：短锚点 + 长约束
   - 语言自适应（英文题优先英文 query，中文题优先中文 query）
   - 域名白名单/黑名单（过滤明显无关站点）
2. **抓取质量**
   - 更好的正文提取（trafilatura/readability 等）
   - 更强 PDF 支持（目录页、图录、扫描页 OCR）
3. **轻量验证（仍不引入候选池）**
   - finalize 后做“引用覆盖检查”，无 `[Sx]` 则自动重写一次
   - claim 覆盖率提示（缺哪条 claim 的证据）
4. **进入第二阶段：循环/候选池/并行**
   - 缺证据就 targeted search 回到 retrieve（最小循环）
   - 候选池：top-N 候选并行验证
   - 多代理并行检索（LangGraph 子图）

------

## 贡献规范（建议）

- 节点输出必须写回 state（例如 `final_answer` 和 `messages`）
- 搜索/抓取失败不可让流程崩（返回空结果，让 finalize 给“证据不足”）
- 所有新增功能建议带一个最小测试脚本或可复现样例（便于回归）

