"""用「必須出現的詞」自動評分,不需要人工、也不需要 LLM 當裁判。

作法取自 PoliChat(Wojtasik et al., ICCS 2025 Workshops)的 Inclusion Accuracy:
標註者為每題列出「正確答案必須出現的詞」(該論文接受替代寫法,例如 ["four", "4"]),
再檢查模型答案裡有沒有。好處是**可以用程式判定、完全可重現**,不像 RAGAS 那類
以另一個 LLM 當裁判的指標會引入第二層不確定性 —— 這正是本專案需要的客觀標準。

本專案的實作與該論文的差異:
  * 我們沒有另外標註 include words,而是**從既有的 `gold_points` 自動抽出數字**
    (阿拉伯數字與中文數字寫法,例如「八十五分」= 85)。行政法規的標準答案幾乎都是
    金額、學分數、分數、期限,數字對不對就是答案對不對的主要判準。
  * 比對時容忍千分位逗號與中文數字寫法(85 / 八十五 都算命中)。
  * 只看數字,**不看敘述是否正確**。數字全中不代表答案正確,所以這個指標是
    「快速篩選」用的,不能取代逐筆評分。太常見的 0 與 1 不列入判準。

用法:

    python benchmark/inclusion_accuracy.py benchmark/runs/results_raw_xxx.jsonl [...]
    python benchmark/inclusion_accuracy.py --field aa results.jsonl   # 比較同一批的不同欄位

輸出每個檔案的整體命中率與逐題明細,方便兩種設定並排比較。
"""

import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str):
    """把「八十五」「九十五」「十二」轉成整數,只處理 1~999;看不懂就回傳 None。"""
    total = section = num = 0
    for char in text:
        if char in _CN_DIGIT:
            num = _CN_DIGIT[char]
        elif char == "十":
            section += (num or 1) * 10
            num = 0
        elif char == "百":
            section += (num or 1) * 100
            num = 0
        else:
            return None
    return total + section + num or None


def required_numbers(text: str) -> set[str]:
    """抽出一段標準答案裡「必須出現」的數字。"""
    found = set()
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        found.add(token.replace(",", ""))
    for token in re.findall(r"[零一二兩三四五六七八九十百]{1,5}", text):
        value = cn_to_int(token)
        if value:
            found.add(str(value))
    # 0 與 1 在中文裡太常見(「一律」「一次」),當判準只會製造假命中
    return {n for n in found if n not in {"0", "1"}}


def _chinese(value: int) -> str:
    cn = {0: "零", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    if value >= 100:
        return ""
    tens, ones = divmod(value, 10)
    word = ("十" if tens == 1 else cn.get(tens, "") + "十") if tens else ""
    return word + (cn.get(ones, "") if ones else "")


def is_present(number: str, answer: str) -> bool:
    """答案裡有沒有這個數字。容忍千分位逗號與中文數字寫法。"""
    if number in answer.replace(",", ""):
        return True
    try:
        value = int(float(number))
    except ValueError:
        return False
    if f"{value:,}" in answer:
        return True
    word = _chinese(value)
    return bool(word) and word in answer


def score(records: list[dict], bank: dict, field: str = "answer"):
    """回傳 (命中數, 應命中數, 逐題明細)。"""
    hit = total = 0
    rows = []
    for record in records:
        question = bank.get(record.get("id"))
        if not question:
            continue
        needed = set()
        for point in question.get("gold_points", []):
            needed |= required_numbers(point)
        if not needed:
            continue                      # 沒有數字的題目(例如純程序題)不計分
        answer = record.get(field) or ""
        got = {n for n in needed if is_present(n, answer)}
        hit += len(got)
        total += len(needed)
        rows.append({"id": record["id"], "hit": len(got), "need": len(needed),
                     "missing": sorted(needed - got, key=float)})
    return hit, total, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", help="run_benchmark 產生的 benchmark/runs/results_raw_*.jsonl")
    parser.add_argument("--field", default="answer", help="要評分的欄位(預設 answer)")
    parser.add_argument("--questions", default=os.path.join(HERE, "questions.json"))
    parser.add_argument("--detail", action="store_true", help="印出每題缺哪些數字")
    args = parser.parse_args()

    bank = {q["id"]: q for q in json.load(io.open(args.questions, encoding="utf-8"))["questions"]}
    summary = []
    for path in args.files:
        records = [json.loads(line) for line in io.open(path, encoding="utf-8") if line.strip()]
        records = [r for r in records if not r.get("error")]
        hit, total, rows = score(records, bank, args.field)
        summary.append((os.path.basename(path), hit, total, rows))

    print("必要數字命中率(只看含數字要點的題目)")
    for name, hit, total, rows in summary:
        rate = hit / total if total else 0
        print(f"  {name:<58} {hit}/{total} = {rate:.1%}  ({len(rows)} 題)")

    if args.detail:
        for name, _, _, rows in summary:
            print(f"\n【{name}】")
            for row in rows:
                mark = "" if row["hit"] == row["need"] else f"  缺 {','.join(row['missing'])}"
                print(f"  {row['id']:<6} {row['hit']}/{row['need']}{mark}")

    if len(summary) == 2:
        (_, h1, t1, r1), (_, h2, t2, r2) = summary
        by_id = {row["id"]: row for row in r2}
        better = sum(1 for row in r1 if row["id"] in by_id and by_id[row["id"]]["hit"] > row["hit"])
        worse = sum(1 for row in r1 if row["id"] in by_id and by_id[row["id"]]["hit"] < row["hit"])
        same = sum(1 for row in r1 if row["id"] in by_id) - better - worse
        print(f"\n逐題比較(第二個檔相對第一個):較好 {better} 題、較差 {worse} 題、相同 {same} 題")


if __name__ == "__main__":
    main()
