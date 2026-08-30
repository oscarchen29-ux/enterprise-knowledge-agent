"""Benchmark 執行器。

跑完後產出兩個檔案:
  results_raw_*.jsonl   —— 每次執行的完整紀錄(含逐步 log),給程式分析用
  scoring_sheet_*.csv   —— 待人工評分的表格,用 Excel 開(已加 BOM)

本腳本只自動量測「可以客觀判定」的指標(工具呼叫、檢索命中、簡體字、延遲),
答案正確率與幻覺率一律留給人工評分 —— 用 LLM 當裁判會引入另一層不確定性,
在專題規模下人工評 31 題 x 3 次是可行的,而且比較好辯護。

用法:
    python benchmark/run_benchmark.py --model qwen2.5:7b --repeats 3
    python benchmark/run_benchmark.py --category multi_hop --repeats 3
    python benchmark/run_benchmark.py --category unanswerable --repeats 5
    python benchmark/run_benchmark.py --limit 2 --repeats 1        # 冒煙測試
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
import tools
from opencc import OpenCC
from providers.ollama_provider import OllamaProvider

HERE = os.path.dirname(os.path.abspath(__file__))

# 偵測器必須跟 agent.py 的後處理用「同一個」轉換設定,否則會誤判。
# 曾用 s2t 偵測而 pipeline 用 s2tw,結果把「群→羣」這種異體字正規化、
# 以及「核准」的准(繁體本來就有這個字)都算成簡體字,虛報 13% 的後處理漏洞,
# 改用 s2tw 後實際為 0%。詳見 benchmark/README.md 第七節。
_s2tw_probe = OpenCC("s2tw")


def _has_simplified(text: str) -> bool:
    """s2tw 轉換後若與原文不同,代表原文含 pipeline 應該要處理掉的簡體字。"""
    return bool(text) and _s2tw_probe.convert(text) != text


# 模型有時不走 API 的 tool-call 機制,而是把呼叫請求當成一般文字吐出來。
# 這種情況 agent.py 會判定為「沒有工具呼叫」而直接進入回答階段,不會報錯,
# 所以必須另外偵測,否則會被誤計為成功。
_LEAK_MARKERS = ("search_documents", "tool_call", '{"name"')

# 側錄緩衝。probe 只安裝一次,每次執行前由 run_one() 清空。
TOOL_LOG = []
RAW_SINK = []


class _RecordingConverter:
    """包住 agent._s2tw,側錄「OpenCC 後處理之前」的原始輸出。

    這樣才量得到模型本身的簡體傾向;只看最終答案的話,量到的是後處理有沒有壞掉。
    """

    def __init__(self, inner, sink):
        self._inner = inner
        self._sink = sink

    def convert(self, text):
        self._sink.append(text)
        return self._inner.convert(text)


def install_probes():
    """在不修改 agent.py / tools.py 的前提下側錄執行過程。全程只呼叫一次。"""
    original_search = tools.search_documents

    def logged_search(query):
        result = original_search(query)
        # search_documents 回傳格式為 "[檔名]\n內容",逐份抓出檔名
        docs = re.findall(r"^\[(.+?)\]$", result, re.M)
        TOOL_LOG.append({"query": query, "returned_docs": docs})
        return result

    # agent.py 用 `from tools import TOOL_FUNCTIONS` 綁定的是同一個 dict 物件,
    # 因此改 dict 內容即可生效,不需要動到原始碼。
    tools.TOOL_FUNCTIONS["search_documents"] = logged_search
    agent._s2tw = _RecordingConverter(agent._s2tw, RAW_SINK)


def run_one(question, provider):
    """跑單一題目一次,回傳量測結果。"""
    TOOL_LOG.clear()
    RAW_SINK.clear()
    buffer = io.StringIO()

    start = time.time()
    error = None
    try:
        with redirect_stdout(buffer):
            answer = agent.run_task(question["question"], provider)
    except Exception as exc:  # noqa: BLE001 —— benchmark 要記錄崩潰,不能讓它中斷整批
        answer = ""
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.time() - start, 2)

    stdout_log = buffer.getvalue()
    raw_answer = RAW_SINK[-1] if RAW_SINK else ""

    returned = {d for call in TOOL_LOG for d in call["returned_docs"]}
    expected = set(question["expected_docs"])

    return {
        "id": question["id"],
        "category": question["category"],
        "question": question["question"],
        "answer": answer,
        # 保留後處理前的原始輸出,事後才有辦法重新分析繁簡問題而不必重跑
        "raw_answer": raw_answer,
        "error": error,
        "latency_sec": elapsed,
        # --- 工具呼叫 ---
        "tool_call_count": len(TOOL_LOG),
        "tool_queries": [c["query"] for c in TOOL_LOG],
        "tool_arg_error": "工具參數格式錯誤" in stdout_log,
        # 該查文件卻一次都沒查 —— 靜默失敗,不會報錯但答案沒有文件依據
        "no_tool_call": len(TOOL_LOG) == 0,
        # 把工具呼叫當成純文字吐出來,是 no_tool_call 最常見的成因
        "tool_call_leaked_as_text": any(m in answer for m in _LEAK_MARKERS),
        "hit_max_steps": answer.startswith("已達最大步驟數"),
        # --- 檢索 ---
        "returned_docs": sorted(returned),
        "expected_docs": sorted(expected),
        "retrieval_hit_all": bool(expected) and expected.issubset(returned),
        "retrieval_hit_any": bool(expected & returned),
        # --- 繁簡 ---
        "raw_had_simplified": _has_simplified(raw_answer),
        "final_had_simplified": _has_simplified(answer),
        "stdout_log": stdout_log,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--repeats", type=int, default=3,
                        help="每題重複次數。LLM 輸出有隨機性,單次結果不可信,至少 3 次。")
    parser.add_argument("--category", default=None,
                        help="只跑單一類別 (multi_hop/unanswerable/cross_doc/procedural/single_doc/conditional)")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 題,用於快速冒煙測試")
    parser.add_argument("--out", default=HERE, help="輸出目錄")
    args = parser.parse_args()

    with io.open(os.path.join(HERE, "questions.json"), encoding="utf-8") as f:
        bank = json.load(f)

    questions = bank["questions"]
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]
    if args.limit:
        questions = questions[: args.limit]

    if not questions:
        print("沒有符合條件的題目,請檢查 --category 拼字。")
        return

    install_probes()
    provider = OllamaProvider(model=args.model)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = f"{args.model.replace(':', '-')}_{stamp}"
    jsonl_path = os.path.join(args.out, f"results_raw_{tag}.jsonl")
    csv_path = os.path.join(args.out, f"scoring_sheet_{tag}.csv")

    results = []
    total = len(questions) * args.repeats
    done = 0

    with io.open(jsonl_path, "w", encoding="utf-8") as jf:
        for question in questions:
            for run_idx in range(1, args.repeats + 1):
                record = run_one(question, provider)
                record["run"] = run_idx
                record["model"] = args.model
                results.append(record)
                jf.write(json.dumps(record, ensure_ascii=False) + "\n")
                jf.flush()

                done += 1
                flag = "ERR" if record["error"] else ("TOOL-ERR" if record["tool_arg_error"] else "ok")
                print(f"[{done}/{total}] {question['id']} run{run_idx} "
                      f"{record['latency_sec']}s tools={record['tool_call_count']} {flag}")

    # 人工評分表
    by_id = {q["id"]: q for q in bank["questions"]}
    with io.open(csv_path, "w", encoding="utf-8-sig", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow([
            "id", "category", "run", "question", "gold_points", "answer",
            "檢索是否命中", "有無實際查文件", "延遲(秒)",
            "【人工】命中要點數", "要點總數", "【人工】幻覺主張數",
            "【人工】判定(通過/失敗)", "【人工】備註",
        ])
        for r in results:
            q = by_id[r["id"]]
            writer.writerow([
                r["id"], r["category"], r["run"], r["question"],
                "\n".join(f"- {p}" for p in q["gold_points"]),
                r["answer"],
                "是" if r["retrieval_hit_all"] else ("部分" if r["retrieval_hit_any"] else "否"),
                "沒查(靜默失敗)" if r["no_tool_call"] else "有查",
                r["latency_sec"],
                "", len(q["gold_points"]), "", "", "",
            ])

    # 自動指標摘要
    n = len(results)
    answerable_ids = {q["id"] for q in bank["questions"] if q["answerable"]}
    ans = [r for r in results if r["id"] in answerable_ids]

    print("\n" + "=" * 52)
    print(f"模型: {args.model}   題數: {len(questions)}   重複: {args.repeats}   總執行: {n}")
    print("=" * 52)
    # 「有沒有真的去查文件」比「有沒有報錯」重要 —— 靜默失敗不會報錯,
    # 但答案完全沒有文件依據,是最危險的一種失效。
    print(f"有實際呼叫工具      : {sum(1 for r in results if not r['no_tool_call']) / n:.1%}")
    print(f"  靜默失敗(沒查就答): {sum(1 for r in results if r['no_tool_call']) / n:.1%}")
    print(f"  其中呼叫外洩成文字: {sum(1 for r in results if r['no_tool_call'] and r['tool_call_leaked_as_text'])}")
    print(f"工具參數格式錯誤    : {sum(1 for r in results if r['tool_arg_error'])}")
    print(f"程式崩潰次數        : {sum(1 for r in results if r['error'])}")
    print(f"達最大步驟數未完成  : {sum(1 for r in results if r['hit_max_steps'])}")
    if ans:
        print(f"檢索全命中率(可答題): {sum(1 for r in ans if r['retrieval_hit_all']) / len(ans):.1%}")
        print(f"檢索部分命中率      : {sum(1 for r in ans if r['retrieval_hit_any']) / len(ans):.1%}")
    print(f"後處理前含簡體字    : {sum(1 for r in results if r['raw_had_simplified']) / n:.1%}")
    print(f"後處理後仍含簡體字  : {sum(1 for r in results if r['final_had_simplified']) / n:.1%}  (應為 0%)")
    print(f"平均延遲            : {sum(r['latency_sec'] for r in results) / n:.1f} 秒")

    # 分類拆解。抽取題與推理題量的是不同東西,合併成單一數字會失去意義,
    # 詳見 benchmark/README.md「抽取題 vs 推理題」。
    INFERENCE = {"multi_hop", "unanswerable", "conditional"}
    print("\n分類拆解(自動指標):")
    print(f"  {'類別':<14}{'性質':<6}{'n':>4}{'有查文件':>9}{'檢索全中':>9}{'簡體(前)':>10}{'平均秒':>8}")
    for cat in ("multi_hop", "unanswerable", "conditional",
                "cross_doc", "procedural", "single_doc"):
        rows = [r for r in results if r["category"] == cat]
        if not rows:
            continue
        hit_rows = [r for r in rows if r["expected_docs"]]
        searched = sum(1 for r in rows if not r["no_tool_call"]) / len(rows)
        hit = (sum(1 for r in hit_rows if r["retrieval_hit_all"]) / len(hit_rows)) if hit_rows else None
        simp = sum(1 for r in rows if r["raw_had_simplified"]) / len(rows)
        lat = sum(r["latency_sec"] for r in rows) / len(rows)
        kind = "推理" if cat in INFERENCE else "抽取"
        hit_str = f"{hit:>8.0%}" if hit is not None else "     n/a"
        print(f"  {cat:<14}{kind:<6}{len(rows):>4}{searched:>8.0%} {hit_str}{simp:>9.0%}{lat:>8.1f}")

    print("\n輸出:")
    print(f"  {jsonl_path}")
    print(f"  {csv_path}  <- 用 Excel 開,填『【人工】』欄位")
    print("\n提醒:答案正確率與幻覺率需人工評分,評分標準見 benchmark/README.md。")


if __name__ == "__main__":
    main()
