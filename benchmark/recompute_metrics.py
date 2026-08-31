"""從已存的 results_raw_*.jsonl 重算自動指標,不必重跑模型。

存在的理由:這個專案已經三次改掉量測工具的錯誤,每次都得回頭修正結論,而重跑
一批 96 次執行要將近半小時、還得開著 Ollama。原始 jsonl 存了足夠的資訊,
指標應該可以離線重算 —— 這樣「修正量測」和「重新測量」就分開了。

會修正兩件舊資料裡的量測錯誤:

1. 檔名比對:切塊檢索後 returned_docs 變成「檔名.txt 第N段」,而 expected_docs
   是純檔名,直接比會全部落空。這裡把段號去掉再比。
2. 追問被誤計成靜默失敗:舊版執行器只側錄 search_documents,模型改呼叫
   ask_clarification 的執行會顯示成「一個工具都沒呼叫」。舊檔沒有
   asked_clarification 欄位,改從 stdout_log 認。

用法:
    python benchmark/recompute_metrics.py benchmark/results_raw_qwen2.5-7b_20260830_233537.jsonl
    python benchmark/recompute_metrics.py <jsonl> --rewrite-csv <csv>   # 重寫評分表的自動欄位
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
INFERENCE = {"multi_hop", "unanswerable", "conditional"}
CATEGORIES = ("multi_hop", "unanswerable", "conditional",
              "cross_doc", "procedural", "single_doc")


def _basename(doc: str) -> str:
    """去掉切塊檢索加上的「第N段」後綴,還原成 expected_docs 用的純檔名。"""
    return re.sub(r"\s*第\d+段\s*$", "", doc)


def load(jsonl_path: Path, bank_path: Path):
    rows = [json.loads(line) for line in jsonl_path.open(encoding="utf-8") if line.strip()]
    bank = {q["id"]: q for q in json.loads(bank_path.read_text(encoding="utf-8"))["questions"]}

    for r in rows:
        # 新版執行器直接有這兩個欄位;舊檔沒有,從 stdout_log 回推。
        if "asked_clarification" not in r:
            r["asked_clarification"] = "ask_clarification" in r.get("stdout_log", "")
        if "no_search_call" not in r:
            r["no_search_call"] = not r["returned_docs"]
        r["silent_failure"] = r["no_search_call"] and not r["asked_clarification"]

        got = {_basename(d) for d in r["returned_docs"]}
        exp = set(r["expected_docs"])
        r["hit_all"] = bool(exp) and exp <= got
        r["hit_any"] = bool(exp & got)
        r["answerable"] = bank[r["id"]]["answerable"]
    return rows, bank


def pct(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def rewrite_csv(path: Path, rows, bank):
    """用修正後的判定重寫評分表的自動欄位,保留已填的【人工】欄位。

    需要這個是因為舊評分表把「模型向使用者追問」標成「沒查(靜默失敗)」,
    評分的人會照著這個標籤誤判。
    """
    by_key = {(r["id"], r["run"]): r for r in rows}
    with path.open(encoding="utf-8-sig", newline="") as f:
        table = list(csv.reader(f))
    header, body = table[0], table[1:]
    i_hit, i_searched = header.index("檢索是否命中"), header.index("有無實際查文件")

    changed = 0
    for line in body:
        r = by_key.get((line[0], int(line[2])))
        if r is None:
            continue
        hit = "是" if r["hit_all"] else ("部分" if r["hit_any"] else "否")
        searched = ("追問(未作答)" if r["asked_clarification"]
                    else "沒查(靜默失敗)" if r["silent_failure"] else "有查")
        if (line[i_hit], line[i_searched]) != (hit, searched):
            changed += 1
        line[i_hit], line[i_searched] = hit, searched

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([header] + body)
    print(f"已重寫 {path.name}:{changed}/{len(body)} 列的自動欄位有更動,"
          f"【人工】欄位未動\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--questions", type=Path, default=ROOT / "questions.json")
    ap.add_argument("--rewrite-csv", type=Path,
                    help="就地重寫評分表的自動欄位。【人工】欄位若已填會原樣保留。")
    args = ap.parse_args()

    rows, bank = load(args.jsonl, args.questions)
    if args.rewrite_csv:
        rewrite_csv(args.rewrite_csv, rows, bank)
    n = len(rows)
    ans = [r for r in rows if r["answerable"]]
    searched = [r for r in ans if not r["no_search_call"]]

    print(f"來源: {args.jsonl.name}   總執行: {n}\n")

    print("## 工具呼叫\n")
    print("| 指標 | 數值 |")
    print("|---|---|")
    print(f"| 有查文件 | {pct(sum(not r['no_search_call'] for r in rows), n)} |")
    print(f"| 向使用者追問(未作答) | {pct(sum(r['asked_clarification'] for r in rows), n)} |")
    print(f"| **靜默失敗(沒查也沒問)** | **{pct(sum(r['silent_failure'] for r in rows), n)}** |")
    print(f"| 程式崩潰 | {sum(1 for r in rows if r['error'])} 次 |")
    print(f"| 達最大步驟數未完成 | {sum(1 for r in rows if r['hit_max_steps'])} 次 |")

    print("\n## 檢索(分母:可答且真的發動了檢索)\n")
    print("| 指標 | 數值 |")
    print("|---|---|")
    print(f"| 全命中 (n={len(searched)}) | {pct(sum(r['hit_all'] for r in searched), len(searched))} |")
    print(f"| 部分命中 | {pct(sum(r['hit_any'] for r in searched), len(searched))} |")
    print(f"| 參考:分母含未檢索的執行 (n={len(ans)}) | "
          f"{pct(sum(r['hit_all'] for r in ans), len(ans))} / "
          f"{pct(sum(r['hit_any'] for r in ans), len(ans))} |")

    print("\n## 分類拆解\n")
    print("| 類別 | 性質 | n | 有查文件 | 追問 | 檢索全命中 | 部分命中 | 平均秒 |")
    print("|---|---|---|---|---|---|---|---|")
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for cat in CATEGORIES:
        v = by_cat.get(cat)
        if not v:
            continue
        hv = [r for r in v if r["expected_docs"] and not r["no_search_call"]]
        print(f"| `{cat}` | {'推理' if cat in INFERENCE else '抽取'} | {len(v)} "
              f"| {pct(sum(not r['no_search_call'] for r in v), len(v))} "
              f"| {pct(sum(r['asked_clarification'] for r in v), len(v))} "
              f"| {pct(sum(r['hit_all'] for r in hv), len(hv))} "
              f"| {pct(sum(r['hit_any'] for r in hv), len(hv))} "
              f"| {sum(r['latency_sec'] for r in v) / len(v):.1f} |")

    print("\n## 需要追查的題目\n")
    print("**真的檢索了、卻一份正確文件都沒撈到**:\n")
    for qid in sorted({r["id"] for r in searched if not r["hit_any"]}):
        v = [r for r in searched if r["id"] == qid]
        print(f"- `{qid}` {sum(not r['hit_any'] for r in v)}/{len(v)} 次 — "
              f"expected: {', '.join(v[0]['expected_docs'])}")

    print("\n**靜默失敗**:\n")
    sf = [r for r in rows if r["silent_failure"]]
    for qid in sorted({r["id"] for r in sf}):
        print(f"- `{qid}` {sum(1 for r in sf if r['id'] == qid)}/"
              f"{sum(1 for r in rows if r['id'] == qid)} 次")

    print("\n**每次執行都以追問收尾、從未給出答案**:\n")
    for qid in sorted({r["id"] for r in rows}):
        v = [r for r in rows if r["id"] == qid]
        if v and all(r["asked_clarification"] for r in v):
            print(f"- `{qid}` {len(v)}/{len(v)} 次")


if __name__ == "__main__":
    main()
