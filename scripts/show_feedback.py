"""看使用者回報了什麼。

回報寫在 `web/feedback.jsonl`,**只存在跑網頁的那台機器上**,不會上傳到任何地方,
也沒有進 git(裡面是學生的真實提問,屬個人資料)。所以要看就得在那台機器上看,
或自己把檔案複製出來。

這是這個專題最有價值的資料 —— 自己出的考題只測得到自己想得到的東西,
真實提問才會暴露沒想過的問題(例如「阿大一呢」那種口語問法)。

用法:
    python scripts/show_feedback.py              # 摘要 + 全部被標記為有誤的
    python scripts/show_feedback.py --all        # 列出全部
    python scripts/show_feedback.py --export questions.txt   # 只匯出提問,供設計題目用
"""

import argparse
import collections
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "web", "feedback.jsonl")

LABEL = {"correct": "正確", "wrong": "有誤", "unclear": "看不懂"}


def load():
    if not os.path.exists(PATH):
        print(f"還沒有任何回報。({PATH} 不存在)")
        print("網頁被實際使用之後才會產生。")
        return []
    rows = []
    with io.open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="列出全部,不只有誤的")
    parser.add_argument("--export", metavar="檔案", help="把提問匯出成純文字,一行一題")
    args = parser.parse_args()

    rows = load()
    if not rows:
        return

    counts = collections.Counter(r.get("verdict") for r in rows)
    total = len(rows)
    print(f"共 {total} 筆回報")
    for key in ("correct", "wrong", "unclear"):
        n = counts.get(key, 0)
        if n:
            print(f"  {LABEL[key]}: {n} 筆({n / total:.0%})")
    print()

    if args.export:
        questions = []
        for r in rows:
            q = (r.get("question") or "").strip()
            if q and q not in questions:
                questions.append(q)
        with io.open(args.export, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(questions) + "\n")
        print(f"已匯出 {len(questions)} 個不重複提問到 {args.export}")
        return

    shown = rows if args.all else [r for r in rows if r.get("verdict") != "correct"]
    if not shown:
        print("沒有被標記為有誤或看不懂的回報。")
        return
    if not args.all:
        print(f"以下是被標記為有誤/看不懂的 {len(shown)} 筆(加 --all 看全部):\n")

    for r in shown:
        print("=" * 60)
        print(f"[{LABEL.get(r.get('verdict'), r.get('verdict'))}] {r.get('time', '')}")
        print(f"問題: {r.get('question', '')}")
        if r.get("comment"):
            print(f"使用者說: {r['comment']}")
        print(f"依據文件: {'、'.join(r.get('sources') or []) or '(沒有查到文件)'}")
        answer = (r.get("answer") or "").replace("\n", " ")
        print(f"回答: {answer[:200]}{'⋯' if len(answer) > 200 else ''}")
        print()


if __name__ == "__main__":
    main()
