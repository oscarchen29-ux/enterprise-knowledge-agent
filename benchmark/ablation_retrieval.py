"""檢索層級的消融實驗:只用 BM25、只用向量、兩者以 RRF 合併,各自撈不撈得到該撈的東西。

不呼叫生成模型。檢索是固定的程式,同一個查詢每次得到同樣的段落,所以直接拿
benchmark 原始紀錄裡模型當時寫出的查詢(tool_queries)重跑三種檢索即可。

兩個層級:
  文件層級  expected_docs 是否全部出現在前 TOP_K 段的檔名裡(同 run_benchmark 的 retrieval_hit_all)
  段落層級  含有關鍵原文的那一段是否進入前 TOP_K 段。只有 NEEDLES 列出的題目有這個欄位,
            關鍵原文是人工從標準答案挑出、並對照 docs/ 原文確認存在的字串。

「只用 BM25」「只用向量」直接取各自排名的前 TOP_K 段。單一清單套 RRF 不會改變排序
(1/(k+名次) 隨名次遞減),所以不需要融合。

用法:
    python benchmark/ablation_retrieval.py benchmark/results_raw_gemma4-31b_think-off_20260911_160059_merged.jsonl \
        benchmark/results_raw_gemma4-31b_think-off_verify-verdict_20260911_233103.jsonl --out ablation.csv

需要本機 Ollama 提供 bge-m3(向量那一路要把查詢轉成向量)。
"""

import argparse
import contextlib
import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tools  # noqa: E402

MODES = ("BM25", "向量", "合併")
_LABEL = re.compile(r"\[(.+?) 第(\d+)段\]")

# 題號、檔名、關鍵原文。原文比對前會去掉所有空白(docs/ 由 PDF 轉出,斷行位置不固定)。
NEEDLES = [
    ("A01", "資工系學士班必選修科目表_113.txt", "全校共同課程：16 學分"),
    ("A08", "學生請領學籍暨成績證件規則.txt", "英文成績單每份二十元"),
    ("B02", "資工系碩士班修業規則_1141120.txt", "最多可抵十二個學分"),
    ("B04", "研究生學位考試辦法.txt", "至十一月三十日"),
    ("B07", "學雜費收費標準.txt", "學雜費基數12,400"),
    ("D05", "資工系碩士班修業規則_1141120.txt", "指導教授以本系專任教師擔任為原則"),
    ("E01", "學生請假規則_115.6.10修正.txt", "三日以上之病假應出具校醫或醫院證"),
    ("E02", "學務處學生手冊_學習資源篇_p18-29.txt", "成績達七十分"),
    ("E03", "學雜費收費標準.txt", "總計26,185"),
    ("F02", "學生請假規則_115.6.10修正.txt", "三日以上之病假應出具校醫或醫院證"),
    ("F03", "學雜費收費標準.txt", "(學費＋雜費)×8(學期)÷128(學分)"),
    ("F04", "學務處學生手冊_學習資源篇_p18-29.txt", "曾在大學畢（肄）業之轉學生"),
    ("F06", "國立暨南國際大學學則.txt", "均達各該學期修習學分總數三分之二"),
    ("F07", "碩士班優秀學生獎勵辦法_113學年度起入學適用.txt", "不含在職生"),
    ("F09", "學生操行成績考評辦法_114修正.txt", "記小過一次減二．五分"),
    ("F10", "學生宿舍住宿費收退費標準.txt", "弘毅樓及致用樓全棟"),
]


def _strip(text):
    return re.sub(r"\s+", "", text)


def bm25_order(query, chunks, average_length):
    """和 tools.search_documents 的 BM25 部分相同的計分,回傳依分數排序的塊索引。"""
    keywords = set(tools._extract_keywords(query))
    scored = []
    for position, chunk in enumerate(chunks):
        text = chunk["text"]
        score = 0.0
        for keyword in keywords:
            frequency = text.count(keyword)
            if not frequency:
                continue
            normalized = tools.BM25_K1 * (1 - tools.BM25_B + tools.BM25_B * len(text) / average_length)
            score += tools._idf(keyword) * frequency * (tools.BM25_K1 + 1) / (frequency + normalized)
        if score <= 0:
            continue
        score += 0.3 * sum(tools._idf(k) for k in keywords if k in chunk["file"])
        scored.append((score, position))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [position for _, position in scored]


def rrf(bm25, vector):
    """和 tools.search_documents 相同:各取前 50 名,名次從 0 起算。"""
    fused = {}
    for rank, position in enumerate(bm25[:50]):
        fused[position] = fused.get(position, 0.0) + 1.0 / (tools.RRF_K + rank)
    for rank, position in enumerate(vector[:50]):
        fused[position] = fused.get(position, 0.0) + 1.0 / (tools.RRF_K + rank)
    return sorted(fused, key=lambda p: -fused[p])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jsonl", nargs="+", type=Path, help="run_benchmark 產生的 results_raw_*.jsonl")
    ap.add_argument("--out", type=Path, help="逐筆結果 CSV(UTF-8 BOM,Excel 可直接開)")
    ap.add_argument("--questions", type=Path, default=ROOT / "benchmark" / "questions.json")
    args = ap.parse_args()

    chunks = tools._load_chunks()
    average_length = sum(len(c["text"]) for c in chunks) / len(chunks)
    if not tools._load_index():
        sys.exit("向量索引不可用,無法做向量那一路。先執行 python scripts/build_index.py")

    cache = {}

    def rankings(query):
        if query not in cache:
            vector = tools._vector_ranking(query)
            if vector is None:
                sys.exit("向量檢索失敗:確認本機 Ollama 有 bge-m3 並且正在執行")
            bm25 = bm25_order(query, chunks, average_length)
            vector = [int(p) for p in vector]
            cache[query] = {"BM25": bm25, "向量": vector, "合併": rrf(bm25, vector)}
        return cache[query]

    bank = {q["id"]: q for q in json.load(io.open(args.questions, encoding="utf-8"))["questions"]}
    targets = {}
    for qid, filename, needle in NEEDLES:
        hit = {i for i, c in enumerate(chunks) if c["file"] == filename and _strip(needle) in _strip(c["text"])}
        if not hit:
            sys.exit(f"{qid} 的關鍵原文在 {filename} 裡找不到:{needle}")
        targets.setdefault(qid, set()).update(hit)

    rows = []
    for path in args.jsonl:
        for line in io.open(path, encoding="utf-8"):
            if not line.strip():
                continue
            record = json.loads(line)
            queries = list(record["tool_queries"])
            # 模型一次都沒查時,agent 會拿原問題強制檢索一次
            if "強制檢索" in record.get("stdout_log", ""):
                queries.append(record["question"])
            rows.append((path.name, record, queries))

    # 確認這裡重算的「合併」和系統實際回傳的前 TOP_K 段完全一致,才算得上是同一套檢索
    checked = 0
    for query in sorted({q for _, _, qs in rows for q in qs})[:40]:
        with contextlib.redirect_stdout(io.StringIO()):
            output = tools.search_documents(query)
        real = [(f, int(n)) for f, n in _LABEL.findall(output)]
        mine = [(chunks[p]["file"], chunks[p]["index"] + 1) for p in rankings(query)["合併"][:tools.TOP_K]]
        if real != mine:
            sys.exit(f"重算的合併結果和 tools.search_documents 不一致:{query}")
        checked += 1

    table = []
    for source, record, queries in rows:
        question = bank[record["id"]]
        row = {"來源檔": source, "題號": record["id"], "第幾次": record["run"],
               "可答題": question["answerable"], "查詢": " ｜ ".join(queries)}
        for mode in MODES:
            top = {p for q in queries for p in rankings(q)[mode][:tools.TOP_K]}
            files = {chunks[p]["file"] for p in top}
            expected = set(question["expected_docs"])
            row[f"文件層級_{mode}"] = (expected <= files) if (question["answerable"] and queries) else ""
            row[f"段落層級_{mode}"] = bool(targets[record["id"]] & top) if (record["id"] in targets and queries) else ""
        table.append(row)

    print(f"檢核:{checked} 個查詢的合併結果與 tools.search_documents 一致")
    for level in ("文件層級", "段落層級"):
        counted = [r for r in table if r[f"{level}_合併"] != ""]
        print(f"\n【{level}】n = {len(counted)}")
        for mode in MODES:
            hit = sum(r[f"{level}_{mode}"] for r in counted)
            print(f"  {mode:<4} {hit}/{len(counted)} = {hit / len(counted):.1%}")

    if args.out:
        with io.open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
        print(f"\n逐筆結果:{args.out}")


if __name__ == "__main__":
    main()
