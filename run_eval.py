import json
import re
import string
import sys
import time
import warnings
from pathlib import Path

import requests

# ========== 配置 ==========
STANDARD_FILE  = "test_data.jsonl"
SERVICE_CONFIG = "service.json"   # 单服务配置：{"api_url": "...", "auth_token": "..."}
OUTPUT_FILE    = "result.jsonl"
SCORE_FILE     = "score.json"
MAX_RETRIES    = 3
TIMEOUT        = 600
# ==========================


# ────────────────────────────────────────────
# 数据加载
# ────────────────────────────────────────────

def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_jsonl_as_dict(path: str) -> dict[str, str]:
    """读取 jsonl，以 id 为键、answer 为值返回字典（answer 统一转 str）。"""
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if "id" not in item or "answer" not in item:
                raise ValueError(f"{path} 第 {lineno} 行缺少 id 或 answer 字段")
            result[item["id"]] = str(item["answer"])
    return result


# ────────────────────────────────────────────
# SSE 解析 & API 调用
# ────────────────────────────────────────────

def parse_sse_stream(response: requests.Response, start: float) -> str:
    """解析 SSE 流，拼接所有 event: Message 的 answer 片段。"""
    chunks, current_event = [], None

    for line in response.iter_lines(decode_unicode=True):
        if time.time() - start > TIMEOUT:
            print("  SSE 解析超时，终止")
            break

        if line == "":
            current_event = None
            continue
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
            continue
        if line.startswith("data:") and current_event == "Message":
            try:
                chunk = json.loads(line[len("data:"):].strip()).get("answer", "")
                if chunk:
                    chunks.append(chunk)
            except json.JSONDecodeError:
                pass

    return "".join(chunks)


def call_api(api_url: str, auth_token: str, question: str, row_id) -> tuple[str | None, float]:
    """流式调用 API，重试最多 MAX_RETRIES 次。返回 (answer, 耗时秒)。"""
    headers = {
        "Authorization":  auth_token,
        "Content-Type":   "application/json",
        "Accept":         "text/event-stream",
    }
    total_elapsed = 0.0

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.time()
        try:
            resp = requests.post(
                api_url,
                headers=headers,
                json={"question": question},
                timeout=TIMEOUT,
                stream=True,
            )
            resp.raise_for_status()
            answer = parse_sse_stream(resp, start)
            total_elapsed += time.time() - start
            return answer, total_elapsed
        except Exception as e:
            total_elapsed += time.time() - start
            print(f"  id={row_id} 第 {attempt}/{MAX_RETRIES} 次失败: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(3)

    return None, total_elapsed


# ────────────────────────────────────────────
# 服务调用主流程
# ────────────────────────────────────────────

def run_service(api_url: str, auth_token: str, rows: list[dict]) -> dict:
    """逐条调用 API，结果写入 OUTPUT_FILE，返回统计信息。"""
    print(f"开始处理，共 {len(rows)} 条 → {OUTPUT_FILE}")
    success, skip, total_time = 0, 0, 0.0

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        for i, row in enumerate(rows, 1):
            row_id, question = row["id"], row["question"]
            print(f"[{i}/{len(rows)}] id={row_id}  {question[:60]}...")

            answer, elapsed = call_api(api_url, auth_token, question, row_id)
            total_time += elapsed

            if answer is None:
                print(f"  ⚠️  id={row_id} 重试均失败，跳过 (耗时 {elapsed:.1f}s)")
                skip += 1
                continue

            out.write(json.dumps({"id": row_id, "answer": answer, "elapsed_seconds": round(elapsed, 2)}, ensure_ascii=False) + "\n")
            out.flush()
            success += 1
            print(f"  ✅ id={row_id} 完成 (耗时 {elapsed:.1f}s) answer={answer[:80]}...")

    print(f"\n完成: 成功 {success}, 跳过 {skip}, 总耗时 {total_time:.1f}s")
    return {"success": success, "skip": skip, "total_seconds": round(total_time, 2)}


# ────────────────────────────────────────────
# 评分逻辑
# ────────────────────────────────────────────

def normalize_number_str(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def normalize_str(s: str, remove_punct: bool = True) -> str:
    s = re.sub(r"\s", "", s).lower()
    if remove_punct:
        s = s.translate(str.maketrans("", "", string.punctuation))
    return s


def question_scorer(model_answer: str | None, ground_truth: str) -> bool:
    def is_float(v: str) -> bool:
        try:
            float(v)
            return True
        except ValueError:
            return False

    if model_answer is None:
        model_answer = "None"

    # 数字
    if is_float(ground_truth):
        val = normalize_number_str(model_answer)
        return val is not None and abs(val - float(ground_truth)) < 1e-6

    # 字符串
    return normalize_str(model_answer) == normalize_str(ground_truth)


# ────────────────────────────────────────────
# 评估主流程
# ────────────────────────────────────────────

def evaluate(submit_file: str, standard_file: str, score_file: str, extra_metrics: dict = None):
    standard_dict = load_jsonl_as_dict(standard_file)
    submit_dict   = load_jsonl_as_dict(submit_file)

    if not (set(standard_dict) & set(submit_dict)):
        raise ValueError("标准答案与提交文件无任何匹配 ID")

    matched, per_q = 0, []
    for std_id, std_ans in standard_dict.items():
        sub_ans = submit_dict.get(std_id)
        correct = question_scorer(sub_ans, std_ans) if sub_ans is not None else False
        if correct:
            matched += 1
        per_q.append({"id": std_id, "standard": std_ans, "submitted": sub_ans, "correct": correct})

    score = round(matched / len(standard_dict), 6)
    print(f"评分: {matched}/{len(standard_dict)} = {score}")

    output = {
        "success": True,
        "score": score,
        "scoreJson": {"score": score},
        "per_question_results": per_q,
    }
    if extra_metrics:
        output.update(extra_metrics)

    with open(score_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────
# 入口
# ────────────────────────────────────────────

def main():
    # 加载服务配置
    with open(SERVICE_CONFIG, "r", encoding="utf-8") as f:
        config = json.load(f)

    rows = load_jsonl(STANDARD_FILE)
    print(f"服务: {config['api_url']}")
    print(f"数据条数: {len(rows)}\n")

    # 1. 调用 API，写入结果
    stats = run_service(config["api_url"], config["auth_token"], rows)

    # 2. 评估结果
    print("\n开始评估...")
    evaluate(
        submit_file=OUTPUT_FILE,
        standard_file=STANDARD_FILE,
        score_file=SCORE_FILE,
        extra_metrics={"performance_metrics": stats},
    )
    print(f"评估完成 → {SCORE_FILE}")


if __name__ == "__main__":
    main()
