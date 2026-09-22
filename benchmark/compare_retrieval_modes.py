"""比較兩種檢索模式:bge-m3 dense+sparse 與 BM25 + 向量 RRF。

直接呼叫系統實際的 tools.search_documents(不是另寫一份計分),所以量到的就是上線
行為。不呼叫生成模型:檢索是固定程式,同一個查詢每次得到同樣的段落,因此拿 benchmark
原始紀錄裡模型當時寫出的查詢(tool_queries)重跑即可。

兩批查詢:
  挑選組  gemma4:31b think-off 的紀錄 —— 選權重時看過的查詢
  對照組  qwen2.5:7b 與 gemma4:31b think-on 的紀錄 —— 選權重時沒看過的查詢(寫法不同,
          但問的仍是同一套題目,所以這不是「沒看過的題目」)

兩個層級(與 experiment/ablation 分支的 ablation_retrieval.py 相同):
  文件層級  expected_docs 是否全部出現在前 TOP_K 段的檔名裡
  段落層級  含有關鍵原文的那一段是否進入前 TOP_K 段(只有 NEEDLES 列出的 16 題)

用法(需要 FlagEmbedding 與 index/m3_*):
    python benchmark/compare_retrieval_modes.py
"""

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tools  # noqa: E402

B = ROOT / "benchmark"
SETS = {
    "挑選組(gemma4:31b think-off)": ["results_raw_gemma4-31b_think-off_20260911_160059_merged.jsonl",
                                    "results_raw_gemma4-31b_think-off_verify-verdict_20260911_233103.jsonl"],
    "對照組(qwen2.5:7b、gemma think-on)": ["results_raw_qwen2.5-7b_20260829_225516.jsonl",
                                          "results_raw_qwen2.5-7b_20260829_234633.jsonl",
                                          "results_raw_qwen2.5-7b_20260830_233537.jsonl",
                                          "results_raw_qwen2.5-7b_20260911_151749.jsonl",
                                          "results_raw_gemma4-31b_think-on_20260911_211310.jsonl"],
}
# 題號、檔名、關鍵原文(與 experiment/ablation 分支的 ablation_retrieval.py 相同)。
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
_LABEL = re.compile(r"\[(.+?) 第(\d+)段\]")


def _strip(text):
    return re.sub(r"\s+", "", text)


def load_runs(names, bank):
    runs = []
    for name in names:
        for line in io.open(B / name, encoding="utf-8"):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("id") not in bank:
                continue
            queries = list(record.get("tool_queries") or [])
            if "強制檢索" in record.get("stdout_log", ""):
                queries.append(record["question"])   # 模型沒查時 agent 會拿原問題強制檢索
            if queries:
                runs.append((record["id"], queries))
    return runs


def top_chunks(query, cache):
    """回傳 search_documents 實際給模型的 (檔名, 段號) 集合。"""
    if query not in cache:
        with contextlib.redirect_stdout(io.StringIO()):
            output = tools.search_documents(query)
        cache[query] = {(f, int(n)) for f, n in _LABEL.findall(output)}
    return cache[query]


def set_mode(mode):
    os.environ["RETRIEVAL"] = "rrf" if mode == "rrf" else ""
    tools._M3_INDEX = None     # 讓 _load_m3_index 依新的環境變數重新判斷
    tools._M3_WARNED = False


def main():
    bank = {q["id"]: q for q in json.load(io.open(B / "questions.json", encoding="utf-8"))["questions"]}
    chunks = tools._load_chunks()
    targets = {}
    for qid, filename, needle in NEEDLES:
        hit = {(c["file"], c["index"] + 1) for c in chunks
               if c["file"] == filename and _strip(needle) in _strip(c["text"])}
        if not hit:
            sys.exit(f"{qid} 的關鍵原文在 {filename} 裡找不到:{needle}")
        targets.setdefault(qid, set()).update(hit)

    modes = {}
    for mode in ("rrf", "m3"):
        set_mode(mode)
        label = tools.retrieval_mode()
        if mode == "m3" and not label.startswith("bge-m3"):
            sys.exit(f"bge-m3 模式無法啟用({label}),先安裝 FlagEmbedding 並執行 scripts/build_m3_index.py")
        modes[mode] = (label, {})

    print(f"段落層級只看 {len(NEEDLES)} 題;文件層級看所有可答題。TOP_K = {tools.TOP_K}\n")
    for set_name, files in SETS.items():
        runs = load_runs(files, bank)
        print(f"【{set_name}】{len(runs)} 次執行")
        for mode, (label, cache) in modes.items():
            set_mode(mode)
            psg = [0, 0]
            doc = [0, 0]
            for qid, queries in runs:
                top = set().union(*(top_chunks(q, cache) for q in queries))
                if qid in targets:
                    psg[1] += 1
                    psg[0] += bool(targets[qid] & top)
                if bank[qid]["answerable"]:
                    doc[1] += 1
                    doc[0] += set(bank[qid]["expected_docs"]) <= {f for f, _ in top}
            print(f"  {label:<34} 段落 {psg[0]}/{psg[1]} = {psg[0] / psg[1]:.1%}   "
                  f"文件 {doc[0]}/{doc[1]} = {doc[0] / doc[1]:.1%}")
        print()


if __name__ == "__main__":
    main()
