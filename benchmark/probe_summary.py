# -*- coding: utf-8 -*-
"""把 precondition_probe 的 jsonl 彙整成可以寫進報告的數字。

為什麼要單獨一支:
probe 本身跑完就印一次摘要,但續跑時它只統計「這一輪新跑的」,
重跑第二輪會印出 0/57 這種誤導的數字。而且逐次重複的變異、
與多數類基準線的對照、同一題三次答案是否一致,都要從整個檔案重算。
**報告裡引用的數字一律以這支的輸出為準。**

多數類基準線:一個永遠回答最常見答案的退化模型能拿幾分。
資格判定的數字一定要跟這條線並列,否則會高估模型。

用法:
    python benchmark/probe_summary.py benchmark/runs/probe_英語檢測獎勵_3x.jsonl
    python benchmark/probe_summary.py benchmark/runs/*.jsonl --detail
"""

import argparse
import glob
import io
import json
import os
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FORMS_DIR = os.path.join(ROOT, "forms")

CASE_FILES = {
    "英語檢測獎勵": "英語檢測獎勵_測試案例.json",
    "宿舍床位申請": "宿舍床位申請_測試案例.json",
}


def majority_baseline(form):
    """永遠回答最常見答案能拿幾分,以及分布。"""
    cases = json.load(io.open(os.path.join(FORMS_DIR, CASE_FILES[form]),
                              encoding="utf-8"))["cases"]
    labels = []
    for c in cases:
        v = c["verdict"]
        if "身分" in v:
            labels.append(v["身分"])
        else:
            e = v.get("符合", [])
            labels.append("不確定" if e == ["不確定"] else ("不符合" if not e else "符合"))
    cnt = Counter(labels)
    top, n = cnt.most_common(1)[0]
    return n / len(cases), top, cnt, len(cases)


def rate(hit, need):
    return hit / need if need else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--detail", action="store_true", help="印出逐題明細與不穩定的題目")
    args = parser.parse_args()

    paths = [p for pat in args.files for p in glob.glob(pat)]
    rows = []
    for p in paths:
        for line in io.open(p, encoding="utf-8"):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    groups = defaultdict(list)
    for r in rows:
        if r.get("error"):
            continue
        groups[(r.get("form", "?"), r.get("variant", "base"),
                r.get("context", "full"), r.get("model", "?"))].append(r)

    for form in sorted({k[0] for k in groups}):
        base, top, dist, ncase = majority_baseline(form) if form in CASE_FILES else (0, "?", {}, 0)
        print("=" * 78)
        print(f"表單:{form}   {ncase} 題")
        print(f"多數類基準線:永遠回答「{top}」= {base:.1%}   分布 {dict(dist)}")
        print("=" * 78)
        print(f"  {'模型 [提示法]':<24} {'n':>4} {'資格判定':>14} {'漏問率':>15} {'冗餘':>4} {'告知完整':>14}")
        print("  " + "-" * 76)

        for (f, variant, context, model), rs in sorted(groups.items()):
            if f != form:
                continue
            # 以「每一輪重複」各算一次,再看區間
            per_run = defaultdict(lambda: [0, 0, 0, 0, 0, 0])  # v_ok, n, miss, need, tell_hit, tell_need
            for r in rs:
                k = r.get("run", 1)
                s = per_run[k]
                s[0] += int(r["verdict_ok"]); s[1] += 1
                s[2] += len(r["missed_ask"]); s[3] += len(r["must_ask"])
                s[4] += len(r["must_tell"]) - len(r["missed_tell"]); s[5] += len(r["must_tell"])
            v_rates = [s[0] / s[1] for s in per_run.values() if s[1]]
            m_rates = [rate(s[2], s[3]) for s in per_run.values() if s[3]]
            t_rates = [rate(s[4], s[5]) for s in per_run.values() if s[5]]
            dup = sum(len(r["redundant"]) for r in rs)

            def fmt(xs):
                if not xs:
                    return "-"
                if len(xs) == 1 or min(xs) == max(xs):
                    return f"{xs[0]:.1%}"
                return f"{sum(xs)/len(xs):.1%} ({min(xs):.0%}~{max(xs):.0%})"

            print(f"  {model + ' [' + variant + '/' + context + ']':<24} {len(rs):>4} "
                  f"{fmt(v_rates):>16} {fmt(m_rates):>16} {dup:>5} {fmt(t_rates):>16}")

        # 同一題跨重複是否穩定
        print()
        for (f, variant, context, model), rs in sorted(groups.items()):
            if f != form:
                continue
            by_case = defaultdict(set)
            for r in rs:
                by_case[r["id"]].add(r["verdict_got"])
            unstable = {c: v for c, v in by_case.items() if len(v) > 1}
            if len(set(r.get("run", 1) for r in rs)) > 1:
                print(f"  {model} [{variant}/{context}]:{len(by_case)} 題中有 "
                      f"**{len(unstable)} 題三次給出不同判定**")
                if args.detail and unstable:
                    for c, v in sorted(unstable.items()):
                        print(f"      {c}: {sorted(v)}")
        print()

    if args.detail:
        print("=" * 78)
        print("漏問最多的前置條件(跨全部執行)")
        miss = Counter(p for r in rows if not r.get("error") for p in r.get("missed_ask", []))
        for k, v in miss.most_common(12):
            print(f"  {k:<6} 漏 {v} 次")


if __name__ == "__main__":
    main()
