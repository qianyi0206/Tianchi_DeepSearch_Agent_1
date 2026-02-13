# 修改说明（中文版）

本文档总结了本次对项目的修改内容、修改原因与效果。

---

## 总览
本次修改目标是提升检索质量与稳定性，修复关键逻辑错误，并加入一个“最小证据覆盖循环”和批量评测脚本。为避免编码问题，部分 prompt 改为 ASCII 英文版本。

---

## 修改点与逻辑说明

### 1) 修复检索抓取逻辑错误
**文件：** `deepresearch/nodes/retrieve.py`  
**问题：** `fetch` 调用在内层循环之外，导致只抓最后一个结果。  
**修复：** 将抓取移动到内层循环中，并保留去重逻辑。  
**效果：** 真正抓取多个 URL，召回率和稳定性显著提升。

---

### 2) 搜索工具正确接入
**文件：** `app.py`  
**问题：** 强行使用 `DuckDuckGoSearcher`，忽略 SerpApi 配置。  
**修复：** 改为 `build_searcher()`，自动选择 SerpApi 或 DDG。  
**效果：** 若配置 SerpApi，搜索质量更高；无配置仍可运行。

---

### 3) 移除硬编码 API Key
**文件：** `agent.py`  
**问题：** `DASHSCOPE_API_KEY` 被写死在源码中。  
**修复：** 删除硬编码，改为环境变量读取。  
**效果：** 更安全、可复用、可部署。

---

### 4) 查询规划改为通用策略
**文件：** `deepresearch/nodes/plan_queries.py`  
**问题：** 原逻辑对“1972 特刊”做特判，属于过拟合，且中文 prompt 有乱码风险。  
**修复：** 重写 prompt 为通用检索策略（ASCII 英文）。  
**效果：** 泛化能力更强，避免编码导致的 prompt 失效。

---

### 5) 抓取质量增强
**文件：** `deepresearch/tools/fetch_tool.py`  
**问题：** 仅用 bs4 简单抽取，学术页面正文容易丢失。  
**修复：** 支持 `readability-lxml`（若安装则优先使用），否则 fallback bs4。  
**效果：** 正文提取更稳，兼容性也保留。

**依赖更新：** `requirements.txt` 增加 `readability-lxml==0.8.4.1`

---

### 6) 增加最小证据覆盖循环
**文件：** `deepresearch/nodes/coverage_check.py`  
**新增节点：** 根据 claims + 证据判断覆盖情况，必要时生成 1–3 条补充查询。  
**重试限制：** 只允许 1 次（防止无限循环）。  
**效果：** 当证据不足时进行一次定向补检，而不是直接猜答案。

---

### 7) 实体扩展
**文件：** `deepresearch/nodes/entity_expand.py`  
**新增功能：** 抽取关键实体并生成别名/译名/笔名/历史名称等扩展。  
**效果：** 提升检索召回率，避免因名称差异漏搜。

---

### 8) 声明验证
**文件：** `deepresearch/nodes/verify_claims.py`  
**新增功能：** 将 claims 与证据包对齐，输出支持与缺失列表。  
**效果：** 自动化验证与定向补检更可靠。

---

### 9) 时间锚点抽取
**文件：** `deepresearch/nodes/time_anchor.py`  
**新增功能：** 抽取“before/after/resumed/shortly”等时间关系，并生成时间相关查询。  
**效果：** 改善时间关系类 claim 的检索命中率。

---

### 10) 时间线对齐
**文件：** `deepresearch/nodes/timeline_align.py`  
**新增功能：** 从证据中提取高频年份，生成“年份 + 关键线索”的补检查询。  
**效果：** 帮助锁定多线索交集年份。

---

### 11) 候选答案生成与打分
**文件：**  
- `deepresearch/nodes/generate_candidates.py`  
- `deepresearch/nodes/score_candidates.py`  
**新增功能：**  
- 在检索前生成 3–5 个候选答案  
- 在检索后基于证据打分并选择最佳候选

**效果：** 避免直接拍脑袋输出答案，提高命中率。

---

### 12) 图结构更新
**文件：** `deepresearch/graph.py`  
**变更：** 插入实体扩展、时间锚点、时间线对齐、声明验证、候选生成/打分节点，并在 `coverage_check` 后进入候选评分。  
**新流程：**
```
START -> parse_claims -> entity_expand -> time_anchor -> generate_candidates -> plan_queries -> retrieve
      -> timeline_align -> verify_claims -> coverage_check -> (retrieve | score_candidates) -> finalize -> END
```

---

### 13) State 扩展
**文件：** `deepresearch/state.py`  
**新增字段：**  
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

---

### 14) 批量评测脚本
**文件：** `run_batch_eval.py`  
**新增：** 批量读取 `question.jsonl` 并输出 `results.jsonl`。  
**输出字段：** `final_answer`、`final_answer_canonical`、`final_answer_normalized`、`queries`、`entities`、`expanded_entities`、`time_anchors`、`time_queries`、`timeline_years`、`timeline_queries`、`candidates`、`selected_candidate`、`candidate_scores`、`claim_verification`、`missing_claims`、`sources`

---

### 15) 答案标准化
**文件：** `deepresearch/utils/answer_normalize.py`、`deepresearch/nodes/finalize.py`  
**新增：**  
- 从 `Final Answer:` 行稳定提取答案  
- 基于候选答案做等价归一（例如 `Mondadori` 与 `Arnoldo Mondadori Editore`）  
- 产出 `final_answer_canonical` 与 `final_answer_normalized` 供评测使用

---

### 16) Claim 级检索
**文件：** `deepresearch/nodes/plan_queries.py`、`deepresearch/nodes/retrieve.py`、`deepresearch/state.py`、`run_batch_eval.py`  
**新增：**  
- `claim_queries` 字段与输出  
- 缺失 claim 查询时自动生成英文 fallback 检索词  
- 检索顺序改为“全局优先 + claim 补充”，避免过早耗尽抓取上限  
- 抓取阶段屏蔽部分高噪声社交站点

---

## 已知限制
- 最小循环只重试 1 次，避免成本无限增长。
- 覆盖检测与声明验证依赖 LLM，属于启发式判断，不是严格验证。

---

## 可选后续优化
- 引入 claim-to-evidence 评分与日志。
- 增加候选答案的多轮验证。
- 引入多路检索策略（site/学术引擎/多语种）。
